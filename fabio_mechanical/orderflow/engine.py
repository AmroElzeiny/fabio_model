"""Executed order flow: Delta/CVD, rolling aggression, big prints, absorption evidence.

Package 1 measurements, made incremental (bounded memory, no per-trade re-sorting of the
whole session) plus the strategy-facing aggression measures Package 2 expected:
signed rolling-delta z-score, normalized CVD slope and directional big-print percentiles.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from itertools import accumulate

from fabio_mechanical.contracts import Side, TradePrint


@dataclass(slots=True)
class FlowState:
    buy_volume: int = 0
    sell_volume: int = 0
    unknown_volume: int = 0
    cvd: int = 0
    last_cvd: int = 0
    previous_cvd: int = 0


class _Welford:
    __slots__ = ("n", "mean", "m2")

    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0

    def add(self, x: float) -> None:
        self.n += 1
        d = x - self.mean
        self.mean += d / self.n
        self.m2 += d * (x - self.mean)

    def std(self) -> float:
        return math.sqrt(self.m2 / (self.n - 1)) if self.n > 1 else 0.0


class OrderFlowEngine:
    def __init__(self, rolling_window: int = 50, big_print_percentile: float = 0.95) -> None:
        self.window = rolling_window
        self.big_print_percentile = big_print_percentile
        self.reset()

    def reset(self) -> None:
        self.state = FlowState()
        # rolling entries: (price_ticks, size, sign, ts_ns)
        self.rolling: deque[tuple[int, int, int, int]] = deque()
        self._rolling_delta = 0
        self._rolling_volume = 0
        self._rolling_notional = 0
        self._rolling_signs = 0
        self._slope_cache: tuple[int, float] = (-1, 0.0)
        self.size_hist: dict[int, int] = {}
        self.trade_count = 0
        self.total_size = 0
        self.first_ts: int | None = None
        self.last_ts: int | None = None
        self.last_price: int | None = None
        self.price_path: deque[int] = deque(maxlen=100)
        self._delta_stats = _Welford()
        self._last_delta_z = 0.0

    # ------------------------------------------------------------------ updates
    def consume(self, trade: TradePrint) -> None:
        ts = trade.ts_event_ns
        self.first_ts = ts if self.first_ts is None else self.first_ts
        self.last_ts = ts
        st = self.state
        st.previous_cvd = st.last_cvd
        st.last_cvd = st.cvd
        sign = 1 if trade.aggressor_side == Side.BID else -1 if trade.aggressor_side == Side.ASK else 0
        if sign > 0:
            st.buy_volume += trade.size
            st.cvd += trade.size
        elif sign < 0:
            st.sell_volume += trade.size
            st.cvd -= trade.size
        else:
            st.unknown_volume += trade.size
        self.rolling.append((trade.price_ticks, trade.size, sign, ts))
        self._rolling_delta += sign * trade.size
        self._rolling_volume += trade.size
        self._rolling_notional += trade.price_ticks * trade.size
        self._rolling_signs += sign
        if len(self.rolling) > self.window:
            p, s, g, _ = self.rolling.popleft()
            self._rolling_delta -= g * s
            self._rolling_volume -= s
            self._rolling_notional -= p * s
            self._rolling_signs -= g
        self.size_hist[trade.size] = self.size_hist.get(trade.size, 0) + 1
        self.trade_count += 1
        self.total_size += trade.size
        self.last_price = trade.price_ticks
        self.price_path.append(trade.price_ticks)
        # z-score of the current rolling delta against the session history of rolling deltas
        rd = float(self._rolling_delta)
        stats = self._delta_stats
        sd = stats.std()
        self._last_delta_z = (rd - stats.mean) / sd if stats.n >= self.window and sd > 0 else 0.0
        stats.add(rd)

    # ------------------------------------------------------------------ measures
    def rolling_delta(self) -> int:
        return self._rolling_delta

    def delta_zscore(self) -> float:
        return self._last_delta_z

    def _rolling_cvd_path(self) -> list[int]:
        return list(accumulate(g * s for _, s, g, _ in self.rolling))

    def cvd_slope(self) -> float:
        """OLS slope (contracts per trade) of the cumulative delta path over the rolling window."""
        if self._slope_cache[0] == self.trade_count:
            return self._slope_cache[1]
        n = len(self.rolling)
        slope = 0.0
        if n >= 2:
            vals = self._rolling_cvd_path()
            xbar = (n - 1) / 2
            den = n * (n * n - 1) / 12
            num = sum(i * v for i, v in enumerate(vals)) - xbar * sum(vals)
            slope = num / den
        self._slope_cache = (self.trade_count, slope)
        return slope

    def cvd_slope_normalized(self) -> float:
        """OLS CVD slope per trade divided by mean trade size in the window; in [-1, 1]."""
        n = len(self.rolling)
        if n < 2 or self._rolling_volume <= 0:
            return 0.0
        mean_size = self._rolling_volume / n
        return max(-1.0, min(1.0, self.cvd_slope() / mean_size))

    def cvd_acceleration(self) -> float:
        st = self.state
        return float(st.cvd - 2 * st.last_cvd + st.previous_cvd)

    def trade_rate_per_second(self) -> float:
        if self.first_ts is None or self.last_ts is None or self.last_ts <= self.first_ts:
            return 0.0
        return self.trade_count / ((self.last_ts - self.first_ts) / 1e9)

    def tape_speed_contracts_per_second(self) -> float:
        if self.first_ts is None or self.last_ts is None or self.last_ts <= self.first_ts:
            return 0.0
        return self.total_size / ((self.last_ts - self.first_ts) / 1e9)

    def _size_quantile(self, frac: float) -> float:
        if not self.trade_count:
            return 0.0
        target = min(self.trade_count, max(1, math.ceil(frac * self.trade_count)))
        cum = 0
        for size in sorted(self.size_hist):
            cum += self.size_hist[size]
            if cum >= target:
                return float(size)
        return float(max(self.size_hist))

    def size_percentile_rank(self, size: int) -> float:
        """Mid-rank percentile of ``size`` within the session trade-size distribution."""
        if not self.trade_count:
            return 0.0
        below = sum(c for s, c in self.size_hist.items() if s < size)
        equal = self.size_hist.get(size, 0)
        return (below + 0.5 * equal) / self.trade_count

    def big_print_threshold(self) -> float:
        if not self.trade_count:
            return math.inf
        if self.trade_count < 2:
            return float(next(iter(self.size_hist)))
        return self._size_quantile(self.big_print_percentile)

    def directional_big_print_percentiles(self) -> tuple[float, float]:
        buy = max((s for _, s, g, _ in self.rolling if g > 0), default=0)
        sell = max((s for _, s, g, _ in self.rolling if g < 0), default=0)
        return (
            self.size_percentile_rank(buy) if buy else 0.0,
            self.size_percentile_rank(sell) if sell else 0.0,
        )

    def trade_size_distribution(self) -> dict[str, float]:
        if not self.trade_count:
            return {"trade_size_mean": 0.0, "trade_size_p50": 0.0, "trade_size_p90": 0.0, "trade_size_max": 0.0}
        return {
            "trade_size_mean": self.total_size / self.trade_count,
            "trade_size_p50": self._size_quantile(0.50),
            "trade_size_p90": self._size_quantile(0.90),
            "trade_size_max": float(max(self.size_hist)),
        }

    def big_print_metrics(self) -> dict[str, float]:
        threshold = self.big_print_threshold()
        big = [(s, g) for _, s, g, _ in self.rolling if s >= threshold]
        out = {
            "threshold_contracts": 0.0 if math.isinf(threshold) else threshold,
            "big_print_count": float(len(big)),
            "big_buy_volume": float(sum(s for s, g in big if g > 0)),
            "big_sell_volume": float(sum(s for s, g in big if g < 0)),
        }
        out.update(self.trade_size_distribution())
        return out

    def signed_persistence(self) -> float:
        if not self.rolling:
            return 0.0
        return self._rolling_signs / len(self.rolling)

    def aggression_metrics(self) -> dict[str, float]:
        st = self.state
        total = st.buy_volume + st.sell_volume
        if total == 0:
            return {
                "buy_share": 0.0,
                "sell_share": 0.0,
                "persistence": 0.0,
                "signed_aggression_persistence": 0.0,
                "buy_aggression_persistence": 0.0,
                "sell_aggression_persistence": 0.0,
                "trade_rate_per_s": 0.0,
                "tape_speed_contracts_per_s": 0.0,
            }
        signed = self.signed_persistence()
        return {
            "buy_share": st.buy_volume / total,
            "sell_share": st.sell_volume / total,
            "persistence": abs(signed),
            "signed_aggression_persistence": signed,
            "buy_aggression_persistence": max(0.0, signed),
            "sell_aggression_persistence": max(0.0, -signed),
            "trade_rate_per_s": self.trade_rate_per_second(),
            "tape_speed_contracts_per_s": self.tape_speed_contracts_per_second(),
        }

    def absorption_metrics(self) -> dict[str, float]:
        if not self.rolling:
            return {
                "aggressive_volume_per_tick_progress": 0.0,
                "adverse_aggression_no_continuation": 0.0,
                "repeated_aggression_same_level": 0.0,
                "cvd_price_divergence": 0.0,
                "time_at_price_fraction": 0.0,
                "rejection_speed_ticks_per_second": 0.0,
            }
        start = self.rolling[0][0]
        end = self.rolling[-1][0]
        progress = max(1, abs(end - start))
        ratio = self._rolling_volume / progress
        rd = self._rolling_delta
        dominant_buy = rd > 0
        adverse = 1.0 if (dominant_buy and end <= start) or ((not dominant_buy) and rd < 0 and end >= start) else 0.0
        counts: dict[int, int] = {}
        for p, _, _, _ in self.rolling:
            counts[p] = counts.get(p, 0) + 1
        repeated = max(counts.values()) / len(self.rolling)
        change = end - start
        div = 1.0 if (rd > 0 and change <= 0) or (rd < 0 and change >= 0) else 0.0
        elapsed_s = max(0.0, (self.rolling[-1][3] - self.rolling[0][3]) / 1e9)
        speed = 0.0 if elapsed_s == 0 else abs(end - start) / elapsed_s
        return {
            "aggressive_volume_per_tick_progress": float(ratio),
            "adverse_aggression_no_continuation": adverse,
            "repeated_aggression_same_level": repeated,
            "cvd_price_divergence": div,
            "time_at_price_fraction": repeated,
            "rejection_speed_ticks_per_second": speed,
        }

    def delta_persistence(self) -> float:
        return self.signed_persistence()

    def vwap_ticks(self) -> float | None:
        if self._rolling_volume <= 0:
            return None
        return self._rolling_notional / self._rolling_volume

    def window_range(self) -> tuple[int | None, int | None]:
        if not self.rolling:
            return None, None
        prices = [p for p, _, _, _ in self.rolling]
        return min(prices), max(prices)

    def directional_aggression(self) -> int:
        """+1 buyers, -1 sellers, 0 flat: sign of rolling delta when trade-count persistence agrees."""
        rd = self._rolling_delta
        pers = self.signed_persistence()
        if rd > 0 and pers > 0:
            return 1
        if rd < 0 and pers < 0:
            return -1
        return 0

    def unknown_aggressor_fraction(self) -> float:
        st = self.state
        total = st.buy_volume + st.sell_volume + st.unknown_volume
        return 0.0 if total == 0 else st.unknown_volume / total
