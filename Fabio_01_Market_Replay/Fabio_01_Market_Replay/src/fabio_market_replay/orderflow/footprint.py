from __future__ import annotations

from collections import defaultdict

from fabio_market_replay.contracts import FootprintLevel, FootprintSnapshot, Side, TradePrint


class FootprintEngine:
    def __init__(self, imbalance_ratio: float = 3.0, stacked_levels: int = 3) -> None:
        self.imbalance_ratio = imbalance_ratio
        self.stacked_levels = stacked_levels
        self.trades: list[TradePrint] = []

    def consume(self, trade: TradePrint) -> None:
        self.trades.append(trade)

    def snapshot(self, ts_event_ns: int, instrument: str, active_contract: str,
                 window_start_ns: int | None = None, window_end_ns: int | None = None) -> FootprintSnapshot:
        if window_start_ns is None:
            window_start_ns = self.trades[0].ts_event_ns if self.trades else ts_event_ns
        if window_end_ns is None:
            window_end_ns = ts_event_ns
        selected = [t for t in self.trades if window_start_ns <= t.ts_event_ns <= window_end_ns]
        prices: dict[int, dict[str, int]] = defaultdict(lambda: {"bid": 0, "ask": 0})
        for t in selected:
            if t.aggressor_side == Side.BID:
                prices[t.price_ticks]["ask"] += t.size
            elif t.aggressor_side == Side.ASK:
                prices[t.price_ticks]["bid"] += t.size
        if not prices:
            return FootprintSnapshot(ts_event_ns=ts_event_ns, instrument=instrument, active_contract=active_contract,
                                     window_start_ns=window_start_ns, window_end_ns=window_end_ns, levels=[], total_volume=0, delta=0)
        ordered = sorted(prices)
        levels: list[FootprintLevel] = []
        buy_flags: list[bool] = []
        sell_flags: list[bool] = []
        for p in ordered:
            ask = prices[p]["ask"]; bid = prices[p]["bid"]
            lower_bid = prices.get(p - 1, {"bid": 0})["bid"]
            upper_ask = prices.get(p + 1, {"ask": 0})["ask"]
            buy_ratio = None if lower_bid == 0 else ask / lower_bid
            sell_ratio = None if upper_ask == 0 else bid / upper_ask
            buy_imb = ask > 0 and (lower_bid == 0 or (buy_ratio is not None and buy_ratio >= self.imbalance_ratio))
            sell_imb = bid > 0 and (upper_ask == 0 or (sell_ratio is not None and sell_ratio >= self.imbalance_ratio))
            buy_flags.append(buy_imb); sell_flags.append(sell_imb)
            levels.append(FootprintLevel(price_ticks=p, bid_executed=bid, ask_executed=ask, delta=ask-bid,
                                         diagonal_buy_ratio=buy_ratio, diagonal_sell_ratio=sell_ratio,
                                         buy_imbalance=buy_imb, sell_imbalance=sell_imb))
        def max_run(flags: list[bool]) -> int:
            best = cur = 0
            for x in flags:
                cur = cur + 1 if x else 0
                best = max(best, cur)
            return best
        total = sum(x.bid_executed + x.ask_executed for x in levels)
        delta = sum(x.delta for x in levels)
        low = levels[0]; high = levels[-1]
        low_two = 1.0 if low.bid_executed > 0 and low.ask_executed > 0 else 0.0
        high_two = 1.0 if high.bid_executed > 0 and high.ask_executed > 0 else 0.0
        unfinished = {
            "low_two_sided_unfinished_evidence": low_two,
            "high_two_sided_unfinished_evidence": high_two,
            "low_one_sided_finished_evidence": 1.0 - low_two,
            "high_one_sided_finished_evidence": 1.0 - high_two,
        }
        return FootprintSnapshot(ts_event_ns=ts_event_ns, instrument=instrument, active_contract=active_contract,
                                 window_start_ns=window_start_ns, window_end_ns=window_end_ns, levels=levels,
                                 total_volume=total, delta=delta,
                                 stacked_buy_imbalance_levels=max_run(buy_flags),
                                 stacked_sell_imbalance_levels=max_run(sell_flags),
                                 unfinished_auction_evidence=unfinished)

    def reset(self) -> None:
        self.trades.clear()

    def snapshot_last_events(self, n: int, ts_event_ns: int, instrument: str, active_contract: str) -> FootprintSnapshot:
        selected = self.trades[-max(1, n):]
        temp = FootprintEngine(self.imbalance_ratio, self.stacked_levels)
        for trade in selected:
            temp.consume(trade)
        start = selected[0].ts_event_ns if selected else ts_event_ns
        return temp.snapshot(ts_event_ns, instrument, active_contract, start, ts_event_ns)

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
