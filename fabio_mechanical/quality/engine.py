"""Independent data-quality engine (Package 1) with a hard, sticky ``trade_safe`` lock.

Integration changes (see INTEGRATION_REPORT.md):

* duplicate detection uses a bounded window (memory safe over a full MBO day);
* Databento snapshot records (``F_SNAPSHOT``) are exempt from sequence/timestamp ordering
  checks, and a ``CLEAR`` resets the snapshot requirement (daily 00:00 UTC re-seed);
* timestamp regression is checked on the replay clock (``ts_recv`` when present), which is the
  order Databento files are written in; ``ts_event`` regressions are counted as a diagnostic;
* the crossed/locked-book check runs only at packet boundaries (``F_LAST``) where the book is
  consistent;
* staleness is transient (evaluated per decision), not a permanent lock.
"""

from __future__ import annotations

from collections import deque

from fabio_mechanical.contracts import BookSnapshot, DataQualityFlags, EventAction, MarketEvent

F_LAST = 1 << 7
F_SNAPSHOT = 1 << 5
F_BAD_TS_RECV = 1 << 3
F_MAYBE_BAD_BOOK = 1 << 2

SEVERE_FLAGS = (
    "gaps",
    "out_of_order",
    "impossible_book",
    "negative_size",
    "crossed_book",
    "unknown_contract",
    "missing_snapshot",
    "timestamp_regression",
    "duplicated_event",
    "checksum_mismatch",
)


class DataQualityEngine:
    def __init__(
        self,
        stale_ns: int = 5_000_000_000,
        max_sequence_gap: int = 1_000_000,
        known_prefixes: tuple[str, ...] = ("NQ", "MNQ"),
        dedupe_window: int = 100_000,
    ) -> None:
        self.stale_ns = stale_ns
        self.max_sequence_gap = max_sequence_gap
        self.known_prefixes = tuple(known_prefixes)
        self.dedupe_window = dedupe_window
        self.last_clock: int | None = None
        self.last_ts_event: int | None = None
        self.last_sequence: int | None = None
        self._seen: set[tuple[object, ...]] = set()
        self._seen_order: deque[tuple[object, ...]] = deque()
        self._flags: dict[str, bool] = dict.fromkeys(SEVERE_FLAGS, False)
        self._reasons: set[str] = set()
        self._cached: DataQualityFlags | None = DataQualityFlags()
        self.have_snapshot = False
        self.in_snapshot = False
        self.ts_event_regressions = 0
        self.provider_warnings: dict[str, int] = {"bad_ts_recv": 0, "maybe_bad_book": 0}

    @property
    def trade_safe(self) -> bool:
        return not any(self._flags.values())

    @property
    def flags(self) -> DataQualityFlags:
        if self._cached is None:
            self._cached = DataQualityFlags(**self._flags, trade_safe=self.trade_safe, reasons=sorted(self._reasons))
        return self._cached

    def _set(self, name: str, reason: str) -> None:
        if not self._flags.get(name) or reason not in self._reasons:
            self._flags[name] = True
            self._reasons.add(reason)
            self._cached = None

    def mark_checksum_mismatch(self, detail: str = "manifest_checksum_mismatch") -> None:
        self._set("checksum_mismatch", detail)

    def mark_invalid_record(self, reason: str) -> None:
        """A provider record could not be converted (e.g. missing price): the book is no longer trustworthy."""
        self._set("impossible_book", f"invalid_provider_record:{reason}")

    def observe_event(self, e: MarketEvent) -> DataQualityFlags:
        flags = e.provider_flags or 0
        is_snapshot_record = bool(flags & F_SNAPSHOT)
        if flags & F_BAD_TS_RECV:
            self.provider_warnings["bad_ts_recv"] += 1
        if flags & F_MAYBE_BAD_BOOK:
            self.provider_warnings["maybe_bad_book"] += 1
        key = (e.ts_event_ns, e.ts_recv_ns, e.sequence, e.action.value, e.order_id, e.price_ticks, e.size, e.side.value, flags)
        if key in self._seen:
            self._set("duplicated_event", "duplicated_event")
        else:
            self._seen.add(key)
            self._seen_order.append(key)
            if len(self._seen_order) > self.dedupe_window:
                self._seen.discard(self._seen_order.popleft())
        clock = e.clock_ns
        if self.last_clock is not None and clock < self.last_clock:
            self._set("timestamp_regression", "timestamp_regression")
        if not is_snapshot_record:
            if self.last_ts_event is not None and e.ts_event_ns < self.last_ts_event:
                self.ts_event_regressions += 1
            if self.last_sequence is not None and e.sequence > 0:
                if e.sequence < self.last_sequence:
                    self._set("out_of_order", "out_of_order_sequence")
                elif e.sequence - self.last_sequence > self.max_sequence_gap:
                    self._set("gaps", "large_sequence_gap")
        if e.size < 0:  # pydantic blocks this; retained for defensive adapters using model_construct
            self._set("negative_size", "negative_size")
        if not e.active_contract.startswith(self.known_prefixes):
            self._set("unknown_contract", "unknown_contract")
        if e.action == EventAction.CLEAR:
            self.have_snapshot = False
            self.in_snapshot = is_snapshot_record
        elif e.action == EventAction.CONTRACT_ROLL:
            self.have_snapshot = False
            self.last_sequence = None
        elif e.action == EventAction.SNAPSHOT and e.snapshot_complete:
            self.have_snapshot = True
            self.in_snapshot = False
        elif e.action in {EventAction.ADD, EventAction.MODIFY, EventAction.CANCEL, EventAction.FILL}:
            if not self.have_snapshot and not is_snapshot_record:
                self._set("missing_snapshot", "book_update_before_snapshot")
        self.last_clock = clock if self.last_clock is None else max(self.last_clock, clock)
        if not is_snapshot_record:
            self.last_ts_event = e.ts_event_ns if self.last_ts_event is None else max(self.last_ts_event, e.ts_event_ns)
            if e.sequence > 0:
                self.last_sequence = e.sequence if self.last_sequence is None else max(self.last_sequence, e.sequence)
        return self.flags

    def observe_book(self, book: BookSnapshot) -> DataQualityFlags:
        if book.best_bid and book.best_ask and book.best_bid.price_ticks >= book.best_ask.price_ticks:
            self._set("crossed_book", "crossed_or_locked_book")
            self._set("impossible_book", "crossed_or_locked_book")
        return self.flags

    def observe_top_of_book(self, best_bid: int | None, best_ask: int | None) -> None:
        if best_bid is not None and best_ask is not None and best_bid >= best_ask:
            self._set("crossed_book", "crossed_or_locked_book")
            self._set("impossible_book", "crossed_or_locked_book")

    def stale_ms(self, now_ns: int) -> int:
        if self.last_clock is None:
            return 0
        return max(0, (now_ns - self.last_clock) // 1_000_000)

    def check_stale(self, now_ns: int) -> DataQualityFlags:
        """Transient staleness view for ``now_ns`` (does not latch ``trade_safe``)."""
        stale = self.last_clock is not None and now_ns - self.last_clock > self.stale_ns
        base = self.flags
        if not stale:
            return base
        reasons = sorted({*base.reasons, "stale_stream"})
        return base.model_copy(update={"stale_stream": True, "reasons": reasons})
