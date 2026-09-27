from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from fabio_market_replay.contracts import AuctionFeatureSnapshot, MarketState, VolumeProfileSnapshot


@dataclass(slots=True)
class PricePoint:
    ts_ns: int
    price_ticks: int
    delta: int
    cvd: int
    vwap_ticks: float | None = None


class AuctionFeatureEngine:
    """Feature-only state estimator. Thresholds are configurable; this module does not create trade entries."""

    def __init__(self, thresholds: dict[str, float] | None = None, window: int = 100) -> None:
        self.thresholds = thresholds or {}
        self.points: deque[PricePoint] = deque(maxlen=window)
        self.previous_profile: VolumeProfileSnapshot | None = None
        self.current_profile: VolumeProfileSnapshot | None = None
        self.inside_value_samples = 0
        self.outside_value_samples = 0
        self.outside_runs = 0
        self.return_to_value_count = 0
        self._was_outside = False

    def update_price(self, ts_ns: int, price_ticks: int, delta: int, cvd: int,
                     profile: VolumeProfileSnapshot | None = None, vwap_ticks: float | None = None) -> None:
        self.points.append(PricePoint(ts_ns, price_ticks, delta, cvd, vwap_ticks))
        if profile is not None:
            self.current_profile = profile
            if profile.val_ticks is not None and profile.vah_ticks is not None:
                inside = profile.val_ticks <= price_ticks <= profile.vah_ticks
                if inside:
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
        if self.current_profile is not None:
            self.previous_profile = self.current_profile
        self.current_profile = profile

    @staticmethod
    def _overlap(a_low: int, a_high: int, b_low: int, b_high: int) -> float:
        inter = max(0, min(a_high, b_high) - max(a_low, b_low) + 1)
        union = max(a_high, b_high) - min(a_low, b_low) + 1
        return 0.0 if union <= 0 else inter / union

    def _efficiency(self) -> float | None:
        if len(self.points) < 2:
            return None
        pts = list(self.points)
        net = abs(pts[-1].price_ticks - pts[0].price_ticks)
        path = sum(abs(b.price_ticks - a.price_ticks) for a, b in zip(pts, pts[1:]))
        return 0.0 if path == 0 else net / path

    def _range_expansion(self) -> float | None:
        if len(self.points) < 8:
            return None
        pts = list(self.points)
        mid = len(pts) // 2
        first = [p.price_ticks for p in pts[:mid]]
        second = [p.price_ticks for p in pts[mid:]]
        r1 = max(first) - min(first)
        r2 = max(second) - min(second)
        return 1.0 if r1 == 0 else r2 / r1

    def _slope(self, values: list[int]) -> float:
        n = len(values)
        if n < 2:
            return 0.0
        xbar = (n - 1) / 2
        ybar = sum(values) / n
        den = sum((i - xbar) ** 2 for i in range(n))
        return 0.0 if den == 0 else sum((i - xbar) * (v - ybar) for i, v in enumerate(values)) / den

    def snapshot(self, ts_ns: int, instrument: str, active_contract: str) -> AuctionFeatureSnapshot:
        pts = list(self.points)
        eff = self._efficiency()
        expansion = self._range_expansion()
        total_samples = self.inside_value_samples + self.outside_value_samples
        inside = None if total_samples == 0 else self.inside_value_samples / total_samples
        outside = None if total_samples == 0 else self.outside_value_samples / total_samples
        acceptance = None if self.outside_value_samples == 0 else min(1.0, self.outside_value_samples / max(1, total_samples))
        returns = None if self.outside_runs == 0 else self.return_to_value_count / self.outside_runs
        overlap = pocmig = vahmig = valmig = None
        if self.previous_profile and self.current_profile:
            p, c = self.previous_profile, self.current_profile
            if None not in (p.val_ticks, p.vah_ticks, c.val_ticks, c.vah_ticks):
                overlap = self._overlap(int(p.val_ticks), int(p.vah_ticks), int(c.val_ticks), int(c.vah_ticks))
            if p.poc_ticks is not None and c.poc_ticks is not None: pocmig = c.poc_ticks - p.poc_ticks
            if p.vah_ticks is not None and c.vah_ticks is not None: vahmig = c.vah_ticks - p.vah_ticks
            if p.val_ticks is not None and c.val_ticks is not None: valmig = c.val_ticks - p.val_ticks
        prices = [p.price_ticks for p in pts]
        cvds = [p.cvd for p in pts]
        deltas = [p.delta for p in pts]
        vwaps = [p.vwap_ticks for p in pts if p.vwap_ticks is not None]
        vwap_slope = self._slope([int(round(x * 1000)) for x in vwaps]) / 1000 if len(vwaps) >= 2 else None
        impulse_velocity = None
        if len(pts) >= 2:
            events = len(pts) - 1
            impulse_velocity = (pts[-1].price_ticks - pts[0].price_ticks) / events
        pullback = None
        if len(prices) >= 3:
            start, end = prices[0], prices[-1]
            if end >= start:
                peak = max(prices); impulse = max(1, peak - start); pullback = max(0.0, (peak - end) / impulse)
            else:
                trough = min(prices); impulse = max(1, start - trough); pullback = max(0.0, (end - trough) / impulse)
        delta_persistence = None if not deltas else sum(1 if d > 0 else -1 if d < 0 else 0 for d in deltas) / len(deltas)
        cvd_persistence = None
        if len(cvds) >= 2:
            moves = [b-a for a,b in zip(cvds, cvds[1:])]
            cvd_persistence = sum(1 if x > 0 else -1 if x < 0 else 0 for x in moves) / len(moves)

        balance_eff = self.thresholds.get("balance_efficiency_max", 0.35)
        imb_eff = self.thresholds.get("imbalance_efficiency_min", 0.60)
        exp_min = self.thresholds.get("range_expansion_min", 1.20)
        pers_min = self.thresholds.get("delta_persistence_min", 0.60)
        state = MarketState.UNKNOWN
        if eff is not None:
            if eff <= balance_eff and (inside is None or inside >= 0.5):
                state = MarketState.BALANCE
            elif eff >= imb_eff and (expansion is None or expansion >= exp_min):
                dp = delta_persistence or 0.0
                if dp >= pers_min:
                    state = MarketState.IMBALANCE_UP
                elif dp <= -pers_min:
                    state = MarketState.IMBALANCE_DOWN
                else:
                    state = MarketState.TRANSITION
            else:
                state = MarketState.TRANSITION
        return AuctionFeatureSnapshot(
            ts_event_ns=ts_ns, instrument=instrument, active_contract=active_contract, state_candidate=state,
            value_area_overlap=overlap, poc_migration_ticks=pocmig, vah_migration_ticks=vahmig, val_migration_ticks=valmig,
            directional_efficiency=eff, range_expansion_ratio=expansion, time_inside_value_fraction=inside,
            time_outside_value_fraction=outside, acceptance_outside_value=acceptance,
            return_to_value_frequency=returns, vwap_slope_ticks_per_event=vwap_slope,
            impulse_velocity_ticks_per_event=impulse_velocity, pullback_depth_fraction=pullback,
            delta_persistence=delta_persistence, cvd_persistence=cvd_persistence,
            raw={"sample_count": float(len(pts)), "outside_runs": float(self.outside_runs)}
        )
