"""Session-level auction measurements (Package 1). Feature-only: no trade decisions here.

Window statistics are maintained incrementally (identical definitions to Package 1, O(1) per
trade except the range/extreme scans, which run in C over an int deque).
"""

from __future__ import annotations

from collections import deque
from itertools import islice
from typing import Any

from fabio_mechanical.contracts import AuctionFeatureSnapshot, MarketState, VolumeProfileSnapshot


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


class AuctionFeatureEngine:
    def __init__(self, thresholds: dict[str, float] | None = None, window: int = 100) -> None:
        self.thresholds = thresholds or {}
        self.window = window
        self.prices: deque[int] = deque()
        self.cvds: deque[int] = deque()
        self.delta_signs: deque[int] = deque()
        self.vwaps: deque[float] = deque()
        self.previous_profile: VolumeProfileSnapshot | None = None
        self.current_profile: VolumeProfileSnapshot | None = None
        self.inside_value_samples = 0
        self.outside_value_samples = 0
        self.outside_runs = 0
        self.return_to_value_count = 0
        self._was_outside = False
        self._path = 0
        self._delta_sign_sum = 0
        self._cvd_move_sum = 0
        self._vw_s0 = 0.0
        self._vw_s1 = 0.0

    def update_price(
        self,
        ts_ns: int,
        price_ticks: int,
        delta: int,
        cvd: int,
        profile: VolumeProfileSnapshot | None = None,
        vwap_ticks: float | None = None,
    ) -> None:
        if len(self.prices) == self.window:
            p0 = self.prices.popleft()
            self._path -= abs(self.prices[0] - p0)
            c0 = self.cvds.popleft()
            self._cvd_move_sum -= _sign(self.cvds[0] - c0)
            self._delta_sign_sum -= self.delta_signs.popleft()
        if self.prices:
            self._path += abs(price_ticks - self.prices[-1])
            self._cvd_move_sum += _sign(cvd - self.cvds[-1])
        self.prices.append(price_ticks)
        self.cvds.append(cvd)
        s = _sign(delta)
        self.delta_signs.append(s)
        self._delta_sign_sum += s
        if vwap_ticks is not None:
            if len(self.vwaps) == self.window:
                v0 = self.vwaps.popleft()
                self._vw_s0 -= v0
                self._vw_s1 -= self._vw_s0  # every remaining index shifts down by one
            self._vw_s1 += len(self.vwaps) * vwap_ticks
            self._vw_s0 += vwap_ticks
            self.vwaps.append(vwap_ticks)
        if profile is not None:
            self.current_profile = profile
            if profile.val_ticks is not None and profile.vah_ticks is not None:
                if profile.val_ticks <= price_ticks <= profile.vah_ticks:
                    self.inside_value_samples += 1
                    if self._was_outside:
                        self.return_to_value_count += 1
                    self._was_outside = False
                else:
                    self.outside_value_samples += 1
                    if not self._was_outside:
                        self.outside_runs += 1
                    self._was_outside = True

    def roll_profile(self, profile: VolumeProfileSnapshot) -> None:
        """Called at a session/contract boundary with the completed profile (Package 1 semantics)."""
        if self.current_profile is not None:
            self.previous_profile = self.current_profile
        self.current_profile = profile

    @staticmethod
    def _overlap(a_low: int, a_high: int, b_low: int, b_high: int) -> float:
        inter = max(0, min(a_high, b_high) - max(a_low, b_low) + 1)
        union = max(a_high, b_high) - min(a_low, b_low) + 1
        return 0.0 if union <= 0 else inter / union

    def _efficiency(self) -> float | None:
        if len(self.prices) < 2:
            return None
        net = abs(self.prices[-1] - self.prices[0])
        return 0.0 if self._path == 0 else net / self._path

    def _range_expansion(self) -> float | None:
        n = len(self.prices)
        if n < 8:
            return None
        mid = n // 2
        first = list(islice(self.prices, 0, mid))
        second = list(islice(self.prices, mid, n))
        r1 = max(first) - min(first)
        r2 = max(second) - min(second)
        return 1.0 if r1 == 0 else r2 / r1

    @staticmethod
    def _slope(values: list[float]) -> float:
        """OLS slope per event (closed-form denominator)."""
        n = len(values)
        if n < 2:
            return 0.0
        xbar = (n - 1) / 2
        den = n * (n * n - 1) / 12
        return (sum(i * v for i, v in enumerate(values)) - xbar * sum(values)) / den

    def _vwap_slope(self) -> float | None:
        n = len(self.vwaps)
        if n < 2:
            return None
        xbar = (n - 1) / 2
        den = n * (n * n - 1) / 12
        return (self._vw_s1 - xbar * self._vw_s0) / den

    def fields(self) -> dict[str, Any]:
        n = len(self.prices)
        eff = self._efficiency()
        expansion = self._range_expansion()
        total = self.inside_value_samples + self.outside_value_samples
        inside = None if total == 0 else self.inside_value_samples / total
        outside = None if total == 0 else self.outside_value_samples / total
        acceptance = None if self.outside_value_samples == 0 else min(1.0, self.outside_value_samples / max(1, total))
        returns = None if self.outside_runs == 0 else self.return_to_value_count / self.outside_runs
        overlap = pocmig = vahmig = valmig = None
        p, c = self.previous_profile, self.current_profile
        if p is not None and c is not None:
            if p.val_ticks is not None and p.vah_ticks is not None and c.val_ticks is not None and c.vah_ticks is not None:
                overlap = self._overlap(p.val_ticks, p.vah_ticks, c.val_ticks, c.vah_ticks)
            if p.poc_ticks is not None and c.poc_ticks is not None:
                pocmig = c.poc_ticks - p.poc_ticks
            if p.vah_ticks is not None and c.vah_ticks is not None:
                vahmig = c.vah_ticks - p.vah_ticks
            if p.val_ticks is not None and c.val_ticks is not None:
                valmig = c.val_ticks - p.val_ticks
        prices = self.prices
        impulse_velocity = (prices[-1] - prices[0]) / (n - 1) if n >= 2 else None
        pullback = None
        if n >= 3:
            start, end = prices[0], prices[-1]
            if end >= start:
                peak = max(prices)
                pullback = max(0.0, (peak - end) / max(1, peak - start))
            else:
                trough = min(prices)
                pullback = max(0.0, (end - trough) / max(1, start - trough))
        delta_persistence = self._delta_sign_sum / n if n else None
        cvd_persistence = self._cvd_move_sum / (n - 1) if n >= 2 else None
        t = self.thresholds
        state = MarketState.UNKNOWN
        if eff is not None:
            if eff <= t.get("balance_efficiency_max", 0.35) and (inside is None or inside >= 0.5):
                state = MarketState.BALANCE
            elif eff >= t.get("imbalance_efficiency_min", 0.60) and (expansion is None or expansion >= t.get("range_expansion_min", 1.20)):
                dp = delta_persistence or 0.0
                pers_min = t.get("delta_persistence_min", 0.60)
                state = (
                    MarketState.IMBALANCE_UP
                    if dp >= pers_min
                    else MarketState.IMBALANCE_DOWN
                    if dp <= -pers_min
                    else MarketState.TRANSITION
                )
            else:
                state = MarketState.TRANSITION
        return {
            "state_candidate": state,
            "value_area_overlap": overlap,
            "poc_migration_ticks": pocmig,
            "vah_migration_ticks": vahmig,
            "val_migration_ticks": valmig,
            "directional_efficiency": eff,
            "range_expansion_ratio": expansion,
            "time_inside_value_fraction": inside,
            "time_outside_value_fraction": outside,
            "acceptance_outside_value": acceptance,
            "return_to_value_frequency": returns,
            "vwap_slope_ticks_per_event": self._vwap_slope(),
            "impulse_velocity_ticks_per_event": impulse_velocity,
            "pullback_depth_fraction": pullback,
            "delta_persistence": delta_persistence,
            "cvd_persistence": cvd_persistence,
            "raw": {"sample_count": float(n), "outside_runs": float(self.outside_runs)},
        }

    def snapshot(self, ts_ns: int, instrument: str, active_contract: str) -> AuctionFeatureSnapshot:
        return AuctionFeatureSnapshot(ts_event_ns=ts_ns, instrument=instrument, active_contract=active_contract, **self.fields())
