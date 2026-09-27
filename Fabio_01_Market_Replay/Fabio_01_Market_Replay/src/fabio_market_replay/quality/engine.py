from __future__ import annotations

from dataclasses import replace

from fabio_market_replay.contracts import BookSnapshot, DataQualityFlags, EventAction, MarketEvent


class DataQualityEngine:
    def __init__(self, stale_ns: int = 5_000_000_000, max_sequence_gap: int = 1_000_000,
                 known_prefixes: tuple[str, ...] = ("NQ", "MNQ")) -> None:
        self.stale_ns = stale_ns
        self.max_sequence_gap = max_sequence_gap
        self.known_prefixes = known_prefixes
        self.last_ts: int | None = None
        self.last_sequence: int | None = None
        self.seen: set[tuple] = set()
        self.reasons: set[str] = set()
        self.flags = DataQualityFlags()
        self.have_snapshot = False

    def _set(self, name: str, reason: str, severe: bool = True) -> None:
        data = self.flags.model_dump()
        data[name] = True
        reasons = set(data["reasons"]); reasons.add(reason); data["reasons"] = sorted(reasons)
        if severe:
            data["trade_safe"] = False
        self.flags = DataQualityFlags(**data)

    def observe_event(self, e: MarketEvent) -> DataQualityFlags:
        key = (e.ts_event_ns, e.sequence, e.action.value, e.order_id, e.price_ticks, e.size, e.side.value)
        if key in self.seen:
            self._set("duplicated_event", "duplicated_event")
        self.seen.add(key)
        if self.last_ts is not None and e.ts_event_ns < self.last_ts:
            self._set("timestamp_regression", "timestamp_regression")
        if self.last_sequence is not None:
            if e.sequence < self.last_sequence:
                self._set("out_of_order", "out_of_order_sequence")
            elif e.sequence - self.last_sequence > self.max_sequence_gap:
                self._set("gaps", "large_sequence_gap")
        if e.size < 0:  # pydantic normally blocks this; retained for defensive adapters
            self._set("negative_size", "negative_size")
        if not e.active_contract.startswith(self.known_prefixes):
            self._set("unknown_contract", "unknown_contract")
        if e.action == EventAction.SNAPSHOT and e.snapshot_complete:
            self.have_snapshot = True
        if e.action in {EventAction.ADD, EventAction.MODIFY, EventAction.CANCEL, EventAction.FILL} and not self.have_snapshot:
            self._set("missing_snapshot", "book_update_before_snapshot")
        self.last_ts = max(self.last_ts or e.ts_event_ns, e.ts_event_ns)
        self.last_sequence = max(self.last_sequence or e.sequence, e.sequence)
        return self.flags

    def observe_book(self, book: BookSnapshot) -> DataQualityFlags:
        if book.best_bid and book.best_ask and book.best_bid.price_ticks >= book.best_ask.price_ticks:
            self._set("crossed_book", "crossed_or_locked_book")
            self._set("impossible_book", "crossed_or_locked_book")
        return self.flags

    def check_stale(self, now_ns: int) -> DataQualityFlags:
        if self.last_ts is not None and now_ns - self.last_ts > self.stale_ns:
            self._set("stale_stream", "stale_stream")
        return self.flags
