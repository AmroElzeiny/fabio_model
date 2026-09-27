from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import math
from statistics import quantiles

from fabio_market_replay.contracts import Side, TradePrint


@dataclass(slots=True)
class FlowState:
    buy_volume: int = 0
    sell_volume: int = 0
    cvd: int = 0
    last_cvd: int = 0
    previous_cvd: int = 0


class OrderFlowEngine:
    def __init__(self, rolling_window: int = 50, big_print_percentile: float = 0.95) -> None:
        self.state = FlowState()
        self.rolling: deque[TradePrint] = deque(maxlen=rolling_window)
        self.all_sizes: list[int] = []
        self.per_price: dict[int, dict[str, int]] = defaultdict(lambda: {"buy": 0, "sell": 0})
        self.big_print_percentile = big_print_percentile
        self.first_ts: int | None = None
        self.last_ts: int | None = None
        self.last_price: int | None = None
        self.price_path: list[int] = []

    def consume(self, trade: TradePrint) -> None:
        self.first_ts = trade.ts_event_ns if self.first_ts is None else self.first_ts
        self.last_ts = trade.ts_event_ns
        self.state.previous_cvd = self.state.last_cvd
        self.state.last_cvd = self.state.cvd
        if trade.aggressor_side == Side.BID:
            self.state.buy_volume += trade.size
            self.state.cvd += trade.size
            self.per_price[trade.price_ticks]["buy"] += trade.size
        elif trade.aggressor_side == Side.ASK:
            self.state.sell_volume += trade.size
            self.state.cvd -= trade.size
            self.per_price[trade.price_ticks]["sell"] += trade.size
        self.rolling.append(trade)
        self.all_sizes.append(trade.size)
        self.last_price = trade.price_ticks
        self.price_path.append(trade.price_ticks)

    def rolling_delta(self) -> int:
        return sum(t.size if t.aggressor_side == Side.BID else -t.size if t.aggressor_side == Side.ASK else 0 for t in self.rolling)

    def cvd_slope(self) -> float:
        if len(self.rolling) < 2:
            return 0.0
        vals = []
        cvd = 0
        for t in self.rolling:
            cvd += t.size if t.aggressor_side == Side.BID else -t.size if t.aggressor_side == Side.ASK else 0
            vals.append(cvd)
        n = len(vals)
        xbar = (n - 1) / 2
        ybar = sum(vals) / n
        den = sum((i - xbar) ** 2 for i in range(n))
        return 0.0 if den == 0 else sum((i - xbar) * (v - ybar) for i, v in enumerate(vals)) / den

    def cvd_acceleration(self) -> float:
        return float(self.state.cvd - 2 * self.state.last_cvd + self.state.previous_cvd)

    def trade_rate_per_second(self) -> float:
        if self.first_ts is None or self.last_ts is None or self.last_ts <= self.first_ts:
            return 0.0
        return len(self.all_sizes) / ((self.last_ts - self.first_ts) / 1e9)

    def tape_speed_contracts_per_second(self) -> float:
        if self.first_ts is None or self.last_ts is None or self.last_ts <= self.first_ts:
            return 0.0
        return sum(self.all_sizes) / ((self.last_ts - self.first_ts) / 1e9)

    def big_print_threshold(self) -> float:
        if not self.all_sizes:
            return math.inf
        if len(self.all_sizes) < 2:
            return float(self.all_sizes[0])
        ordered = sorted(self.all_sizes)
        idx = min(len(ordered) - 1, max(0, math.ceil(self.big_print_percentile * len(ordered)) - 1))
        return float(ordered[idx])

    def big_print_metrics(self) -> dict[str, float]:
        threshold = self.big_print_threshold()
        big = [t for t in self.rolling if t.size >= threshold]
        buys = sum(t.size for t in big if t.aggressor_side == Side.BID)
        sells = sum(t.size for t in big if t.aggressor_side == Side.ASK)
        out = {"threshold_contracts": 0.0 if math.isinf(threshold) else threshold,
               "big_print_count": float(len(big)), "big_buy_volume": float(buys), "big_sell_volume": float(sells)}
        out.update(self.trade_size_distribution())
        return out

    def aggression_metrics(self) -> dict[str, float]:
        total = self.state.buy_volume + self.state.sell_volume
        if total == 0:
            return {"buy_share": 0.0, "sell_share": 0.0, "persistence": 0.0,
                    "trade_rate_per_s": 0.0, "tape_speed_contracts_per_s": 0.0}
        signed = [1 if t.aggressor_side == Side.BID else -1 if t.aggressor_side == Side.ASK else 0 for t in self.rolling]
        persistence = 0.0 if not signed else abs(sum(signed)) / len(signed)
        signed_persistence = sum(signed) / len(signed) if signed else 0.0
        return {"buy_share": self.state.buy_volume / total, "sell_share": self.state.sell_volume / total,
                "persistence": persistence, "signed_aggression_persistence": signed_persistence,
                "buy_aggression_persistence": max(0.0, signed_persistence),
                "sell_aggression_persistence": max(0.0, -signed_persistence),
                "trade_rate_per_s": self.trade_rate_per_second(),
                "tape_speed_contracts_per_s": self.tape_speed_contracts_per_second()}

    def absorption_metrics(self) -> dict[str, float]:
        if not self.rolling:
            return {"aggressive_volume_per_tick_progress": 0.0, "adverse_aggression_no_continuation": 0.0,
                    "repeated_aggression_same_level": 0.0, "cvd_price_divergence": 0.0, "time_at_price_fraction": 0.0}
        start = self.rolling[0].price_ticks
        end = self.rolling[-1].price_ticks
        progress = max(1, abs(end - start))
        aggressive = sum(t.size for t in self.rolling)
        ratio = aggressive / progress
        dominant_buy = self.rolling_delta() > 0
        adverse = 1.0 if (dominant_buy and end <= start) or ((not dominant_buy) and self.rolling_delta() < 0 and end >= start) else 0.0
        counts: dict[int, int] = defaultdict(int)
        for t in self.rolling:
            counts[t.price_ticks] += 1
        repeated = max(counts.values()) / len(self.rolling)
        price_change = end - start
        div = 1.0 if (self.rolling_delta() > 0 and price_change <= 0) or (self.rolling_delta() < 0 and price_change >= 0) else 0.0
        tap = repeated
        elapsed_s = 0.0 if self.first_ts is None or self.last_ts is None else max(0.0, (self.last_ts-self.first_ts)/1e9)
        rejection_speed = 0.0 if elapsed_s == 0 else abs(end-start)/elapsed_s
        return {"aggressive_volume_per_tick_progress": float(ratio), "adverse_aggression_no_continuation": adverse,
                "repeated_aggression_same_level": repeated, "cvd_price_divergence": div,
                "time_at_price_fraction": tap, "rejection_speed_ticks_per_second": rejection_speed}

    def delta_persistence(self) -> float:
        if not self.rolling:
            return 0.0
        signs = [1 if t.aggressor_side == Side.BID else -1 if t.aggressor_side == Side.ASK else 0 for t in self.rolling]
        s = sum(signs)
        return 0.0 if not signs else s / len(signs)

    def vwap_ticks(self) -> float | None:
        total = sum(t.size for t in self.rolling)
        if total <= 0:
            return None
        return sum(t.price_ticks * t.size for t in self.rolling) / total

    def trade_size_distribution(self) -> dict[str, float]:
        if not self.all_sizes:
            return {"trade_size_mean": 0.0, "trade_size_p50": 0.0, "trade_size_p90": 0.0, "trade_size_max": 0.0}
        vals = sorted(self.all_sizes)
        def q(frac: float) -> float:
            idx = min(len(vals)-1, max(0, math.ceil(frac*len(vals))-1))
            return float(vals[idx])
        return {"trade_size_mean": sum(vals)/len(vals), "trade_size_p50": q(.50), "trade_size_p90": q(.90), "trade_size_max": float(vals[-1])}

    def reset(self) -> None:
        self.state = FlowState()
        self.rolling.clear()
        self.all_sizes.clear()
        self.per_price.clear()
        self.first_ts = None
        self.last_ts = None
        self.last_price = None
        self.price_path.clear()
