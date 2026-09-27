"""Footprint (bid/ask executed volume by price) with diagonal and stacked imbalances (Package 1).

``footprint_from_volume`` is the single implementation of the footprint arithmetic. It is used
by ``RollingFootprint`` (bounded, incremental; the strategy-facing aggression-window footprint)
and by ``FootprintEngine`` (arbitrary windows / time bars for research).
"""

from __future__ import annotations

from collections import deque

from fabio_mechanical.contracts import FootprintLevel, FootprintSnapshot, Side, TradePrint

_LEVEL_CACHE: dict[tuple[int, int, int, int, int, float], FootprintLevel] = {}


def _max_run(flags: list[bool]) -> int:
    best = cur = 0
    for x in flags:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    return best


def footprint_from_volume(
    volume: dict[int, list[int]],
    *,
    ts_event_ns: int,
    instrument: str,
    active_contract: str,
    window_start_ns: int,
    window_end_ns: int,
    imbalance_ratio: float,
) -> FootprintSnapshot:
    """``volume[price] = [bid_executed (sell aggressor), ask_executed (buy aggressor)]``."""
    prices = sorted(p for p, v in volume.items() if v[0] or v[1])
    if not prices:
        return FootprintSnapshot(
            ts_event_ns=ts_event_ns,
            instrument=instrument,
            active_contract=active_contract,
            window_start_ns=window_start_ns,
            window_end_ns=window_end_ns,
            levels=[],
            total_volume=0,
            delta=0,
        )
    levels: list[FootprintLevel] = []
    buy_flags: list[bool] = []
    sell_flags: list[bool] = []
    zero = [0, 0]
    prev: int | None = None
    for p in prices:
        if prev is not None and p != prev + 1:
            buy_flags.append(False)  # stacked imbalances must be on adjacent price levels
            sell_flags.append(False)
        prev = p
        bid, ask = volume[p]
        lower_bid = volume.get(p - 1, zero)[0]
        upper_ask = volume.get(p + 1, zero)[1]
        buy_imb = ask > 0 and (lower_bid == 0 or ask / lower_bid >= imbalance_ratio)
        sell_imb = bid > 0 and (upper_ask == 0 or bid / upper_ask >= imbalance_ratio)
        buy_flags.append(buy_imb)
        sell_flags.append(sell_imb)
        key = (p, bid, ask, lower_bid, upper_ask, imbalance_ratio)
        level = _LEVEL_CACHE.get(key)
        if level is None:
            if len(_LEVEL_CACHE) > 50_000:
                _LEVEL_CACHE.clear()
            level = _LEVEL_CACHE[key] = FootprintLevel(
                price_ticks=p,
                bid_executed=bid,
                ask_executed=ask,
                delta=ask - bid,
                diagonal_buy_ratio=None if lower_bid == 0 else ask / lower_bid,
                diagonal_sell_ratio=None if upper_ask == 0 else bid / upper_ask,
                buy_imbalance=buy_imb,
                sell_imbalance=sell_imb,
            )
        levels.append(level)
    total = sum(x.bid_executed + x.ask_executed for x in levels)
    delta = sum(x.delta for x in levels)
    low, high = levels[0], levels[-1]
    low_two = 1.0 if low.bid_executed > 0 and low.ask_executed > 0 else 0.0
    high_two = 1.0 if high.bid_executed > 0 and high.ask_executed > 0 else 0.0
    return FootprintSnapshot(
        ts_event_ns=ts_event_ns,
        instrument=instrument,
        active_contract=active_contract,
        window_start_ns=window_start_ns,
        window_end_ns=window_end_ns,
        levels=levels,
        total_volume=total,
        delta=delta,
        stacked_buy_imbalance_levels=_max_run(buy_flags),
        stacked_sell_imbalance_levels=_max_run(sell_flags),
        unfinished_auction_evidence={
            "low_two_sided_unfinished_evidence": low_two,
            "high_two_sided_unfinished_evidence": high_two,
            "low_one_sided_finished_evidence": 1.0 - low_two,
            "high_one_sided_finished_evidence": 1.0 - high_two,
        },
    )


def stacked_imbalance_levels(footprint: FootprintSnapshot, buy: bool, ratio: float) -> int:
    """Longest run of consecutive price levels whose diagonal imbalance is >= ``ratio``."""
    vol = {x.price_ticks: (x.bid_executed, x.ask_executed) for x in footprint.levels}
    flags: list[bool] = []
    prev: int | None = None
    for p in sorted(vol):
        if prev is not None and p != prev + 1:
            flags.append(False)  # a price gap breaks a stack
        bid, ask = vol[p]
        if buy:
            lower_bid = vol.get(p - 1, (0, 0))[0]
            flags.append(ask > 0 and (lower_bid == 0 or ask / lower_bid >= ratio))
        else:
            upper_ask = vol.get(p + 1, (0, 0))[1]
            flags.append(bid > 0 and (upper_ask == 0 or bid / upper_ask >= ratio))
        prev = p
    return _max_run(flags)


class RollingFootprint:
    """Footprint of the last ``window`` trades (the aggression window), O(1) per trade."""

    def __init__(self, window: int = 50, imbalance_ratio: float = 3.0) -> None:
        self.window = window
        self.imbalance_ratio = imbalance_ratio
        self.reset()

    def reset(self) -> None:
        self._trades: deque[tuple[int, int, int, int]] = deque()
        self._volume: dict[int, list[int]] = {}

    def consume(self, trade: TradePrint) -> None:
        col = 1 if trade.aggressor_side == Side.BID else 0 if trade.aggressor_side == Side.ASK else -1
        self._trades.append((trade.price_ticks, trade.size, col, trade.ts_event_ns))
        if col >= 0:
            self._volume.setdefault(trade.price_ticks, [0, 0])[col] += trade.size
        if len(self._trades) > self.window:
            p, s, c, _ = self._trades.popleft()
            if c >= 0:
                v = self._volume[p]
                v[c] -= s
                if not v[0] and not v[1]:
                    del self._volume[p]

    def snapshot(self, ts_event_ns: int, instrument: str, active_contract: str) -> FootprintSnapshot:
        start = self._trades[0][3] if self._trades else ts_event_ns
        return footprint_from_volume(
            self._volume,
            ts_event_ns=ts_event_ns,
            instrument=instrument,
            active_contract=active_contract,
            window_start_ns=start,
            window_end_ns=ts_event_ns,
            imbalance_ratio=self.imbalance_ratio,
        )


class FootprintEngine:
    """Arbitrary-window / time-bar footprints over a stored trade list (research API)."""

    def __init__(self, imbalance_ratio: float = 3.0, stacked_levels: int = 3) -> None:
        self.imbalance_ratio = imbalance_ratio
        self.stacked_levels = stacked_levels
        self.trades: list[TradePrint] = []

    def consume(self, trade: TradePrint) -> None:
        self.trades.append(trade)

    def reset(self) -> None:
        self.trades.clear()

    def _volume(self, trades: list[TradePrint]) -> dict[int, list[int]]:
        vol: dict[int, list[int]] = {}
        for t in trades:
            if t.aggressor_side == Side.BID:
                vol.setdefault(t.price_ticks, [0, 0])[1] += t.size
            elif t.aggressor_side == Side.ASK:
                vol.setdefault(t.price_ticks, [0, 0])[0] += t.size
        return vol

    def snapshot(
        self,
        ts_event_ns: int,
        instrument: str,
        active_contract: str,
        window_start_ns: int | None = None,
        window_end_ns: int | None = None,
    ) -> FootprintSnapshot:
        if window_start_ns is None:
            window_start_ns = self.trades[0].ts_event_ns if self.trades else ts_event_ns
        if window_end_ns is None:
            window_end_ns = ts_event_ns
        selected = [t for t in self.trades if window_start_ns <= t.ts_event_ns <= window_end_ns]
        return footprint_from_volume(
            self._volume(selected),
            ts_event_ns=ts_event_ns,
            instrument=instrument,
            active_contract=active_contract,
            window_start_ns=window_start_ns,
            window_end_ns=window_end_ns,
            imbalance_ratio=self.imbalance_ratio,
        )

    def snapshot_last_events(self, n: int, ts_event_ns: int, instrument: str, active_contract: str) -> FootprintSnapshot:
        selected = self.trades[-max(1, n) :]
        start = selected[0].ts_event_ns if selected else ts_event_ns
        return footprint_from_volume(
            self._volume(selected),
            ts_event_ns=ts_event_ns,
            instrument=instrument,
            active_contract=active_contract,
            window_start_ns=start,
            window_end_ns=ts_event_ns,
            imbalance_ratio=self.imbalance_ratio,
        )

    def time_bar_snapshots(self, bar_ns: int, instrument: str, active_contract: str) -> list[FootprintSnapshot]:
        if bar_ns <= 0:
            raise ValueError("bar_ns must be positive")
        if not self.trades:
            return []
        start = (self.trades[0].ts_event_ns // bar_ns) * bar_ns
        end = self.trades[-1].ts_event_ns
        out = []
        cur = start
        while cur <= end:
            out.append(self.snapshot(min(cur + bar_ns - 1, end), instrument, active_contract, cur, cur + bar_ns - 1))
            cur += bar_ns
        return out
