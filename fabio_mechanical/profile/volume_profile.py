"""Volume profile: POC/VAH/VAL, shape and zone-based LVN/HVN (Package 1).

``profile_from_vap`` is the single profile implementation. It is used for the developing
session profile, composite/arbitrary profiles, and the impulse/reclaim leg profiles built by
``market/structure.py``. Integration corrections (documented in INTEGRATION_REPORT.md):

* untraded ticks inside the profile range count as zero-volume prices for LVN/HVN detection;
* ``ProfileZone.volume_to_median_ratio`` (zone mean volume / profile median volume) is exposed
  because Package 1's ``relative_volume_metric`` for LVNs is ``1 - ratio`` (clamped) and cannot
  express Package 2's ``lvn_relative_volume_max`` semantics;
* ``retest_count`` counts revisits (entries into the zone), not prints inside it;
* LVN zones touching the profile edge are not LVNs (a valley needs volume on both sides).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable
from statistics import median

from fabio_mechanical.contracts import ProfileZone, TradePrint, VolumeProfileSnapshot

ZoneStats = Callable[[tuple[int, int]], tuple[int, int, int]]  # key -> (persistence, age, retests)


def _quantile(values: list[int], q: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    pos = q * (len(vals) - 1)
    lo = int(pos)
    hi = min(len(vals) - 1, lo + 1)
    frac = pos - lo
    return vals[lo] * (1 - frac) + vals[hi] * frac


def value_area(vap: dict[int, int], fraction: float) -> tuple[int | None, int | None, int | None]:
    """Return (poc, vah, val) using the Package 1 single-price expansion rule."""
    if not vap:
        return None, None, None
    poc = max(vap, key=lambda p: (vap[p], -abs(p)))
    total = sum(vap.values())
    target = total * fraction
    accum = vap[poc]
    prices = sorted(vap)
    idx = prices.index(poc)
    lo = hi = idx
    while accum < target and (lo > 0 or hi < len(prices) - 1):
        left_v = vap[prices[lo - 1]] if lo > 0 else -1
        right_v = vap[prices[hi + 1]] if hi < len(prices) - 1 else -1
        if right_v > left_v:
            hi += 1
            accum += vap[prices[hi]]
        else:
            lo -= 1
            accum += vap[prices[lo]]
    return poc, prices[hi], prices[lo]


def zones(vap: dict[int, int], *, low: bool, lvn_quantile: float, hvn_quantile: float, stats: ZoneStats | None = None) -> list[ProfileZone]:
    if len(vap) < 3:
        return []
    lo_p, hi_p = min(vap), max(vap)
    prices = list(range(lo_p, hi_p + 1))
    vols = [vap.get(p, 0) for p in prices]
    threshold = _quantile(vols, lvn_quantile if low else hvn_quantile)
    flags = [(v <= threshold) if low else (v >= threshold and v > 0) for v in vols]
    med = median(vols) or 1
    out: list[ProfileZone] = []
    i = 0
    n = len(prices)
    while i < n:
        if not flags[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and flags[j + 1]:
            j += 1
        if low and (i == 0 or j == n - 1):
            # an LVN is a valley bounded by traded volume on both sides; profile-edge tails are excess
            i = j + 1
            continue
        zone_vol = sum(vols[i : j + 1]) / (j - i + 1)
        neighbors = []
        if i > 0:
            neighbors.append(vols[i - 1])
        if j + 1 < n:
            neighbors.append(vols[j + 1])
        neighbor_mean = sum(neighbors) / len(neighbors) if neighbors else zone_vol
        if low:
            relative = max(0.0, 1.0 - zone_vol / med)
            ratio = neighbor_mean / max(zone_vol, 1)
        else:
            relative = zone_vol / med
            ratio = zone_vol / max(neighbor_mean, 1)
        key = (prices[i], prices[j])
        persistence, age, retests = stats(key) if stats else (1, 0, 0)
        out.append(
            ProfileZone(
                low_ticks=key[0],
                high_ticks=key[1],
                relative_volume_metric=float(relative),
                neighboring_volume_ratio=float(ratio),
                width_ticks=key[1] - key[0] + 1,
                volume_to_median_ratio=float(zone_vol / med),
                persistence=persistence,
                age_events=age,
                retest_count=retests,
            )
        )
        i = j + 1
    return out


def profile_shape(vap: dict[int, int], poc: int | None) -> str:
    if poc is None or not vap:
        return "EMPTY"
    lo, hi = min(vap), max(vap)
    if hi == lo:
        return "SINGLE_PRICE"
    rel = (poc - lo) / (hi - lo)
    if rel < 0.33:
        return "b_SHAPE"
    if rel > 0.67:
        return "P_SHAPE"
    return "D_SHAPE"


def profile_from_vap(
    vap: dict[int, int],
    *,
    ts_event_ns: int,
    instrument: str,
    active_contract: str,
    profile_kind: str,
    start_ns: int,
    end_ns: int,
    value_area_fraction: float = 0.70,
    hvn_quantile: float = 0.75,
    lvn_quantile: float = 0.25,
    stats: ZoneStats | None = None,
    include_volume: bool = True,
) -> VolumeProfileSnapshot:
    clean = {p: v for p, v in vap.items() if v > 0}
    poc, vah, val = value_area(clean, value_area_fraction)
    return VolumeProfileSnapshot(
        ts_event_ns=ts_event_ns,
        instrument=instrument,
        active_contract=active_contract,
        profile_kind=profile_kind,
        start_ns=start_ns,
        end_ns=end_ns,
        poc_ticks=poc,
        vah_ticks=vah,
        val_ticks=val,
        hvn_zones=zones(clean, low=False, lvn_quantile=lvn_quantile, hvn_quantile=hvn_quantile, stats=stats),
        lvn_zones=zones(clean, low=True, lvn_quantile=lvn_quantile, hvn_quantile=hvn_quantile, stats=stats),
        volume_by_price=dict(sorted(clean.items())) if include_volume else {},
        profile_shape=profile_shape(clean, poc),
        total_volume=sum(clean.values()),
        value_area_fraction=value_area_fraction,
    )


class ZoneTracker:
    """Persistence / age / revisit bookkeeping for zones of an evolving or frozen profile."""

    def __init__(self) -> None:
        self.first_seen_period: dict[tuple[int, int], int] = {}
        self.persistence: dict[tuple[int, int], int] = {}
        self.retests: dict[tuple[int, int], int] = {}
        self._index: list[tuple[int, int]] = []  # sorted zone keys by low
        self._lows: list[int] = []
        self._inside: tuple[int, int] | None = None
        self.current_period = 0

    def refresh(self, keys: list[tuple[int, int]], period: int) -> None:
        self.current_period = period
        live = set(keys)
        for k in list(self.first_seen_period):
            if k not in live:
                self.first_seen_period.pop(k, None)
                self.persistence.pop(k, None)
                self.retests.pop(k, None)
        for k in keys:
            self.first_seen_period.setdefault(k, period)
            self.persistence[k] = self.persistence.get(k, 0) + 1
            self.retests.setdefault(k, 0)
        self._index = sorted(keys)
        self._lows = [k[0] for k in self._index]
        if self._inside not in live:
            self._inside = None

    def observe_price(self, price: int) -> None:
        i = bisect_right(self._lows, price) - 1
        zone = self._index[i] if i >= 0 and self._index[i][0] <= price <= self._index[i][1] else None
        if zone is not None and zone != self._inside:
            self.retests[zone] = self.retests.get(zone, 0) + 1
        self._inside = zone

    def stats(self, key: tuple[int, int]) -> tuple[int, int, int]:
        first = self.first_seen_period.get(key, self.current_period)
        return self.persistence.get(key, 1), max(0, self.current_period - first), self.retests.get(key, 0)


class VolumeProfileEngine:
    """Developing profile with incremental volume-at-price (session profile in the replay)."""

    def __init__(self, value_area_fraction: float = 0.70, hvn_quantile: float = 0.75, lvn_quantile: float = 0.25) -> None:
        self.value_area_fraction = value_area_fraction
        self.hvn_quantile = hvn_quantile
        self.lvn_quantile = lvn_quantile
        self.volume_by_price: dict[int, int] = {}
        self.start_ns: int | None = None
        self.end_ns: int | None = None
        self.zone_tracker = ZoneTracker()

    def consume(self, trade: TradePrint) -> None:
        self.start_ns = trade.ts_event_ns if self.start_ns is None else self.start_ns
        self.end_ns = trade.ts_event_ns
        self.volume_by_price[trade.price_ticks] = self.volume_by_price.get(trade.price_ticks, 0) + trade.size
        self.zone_tracker.observe_price(trade.price_ticks)

    def snapshot(
        self,
        ts_event_ns: int,
        instrument: str,
        active_contract: str,
        profile_kind: str = "developing_session",
        period_index: int | None = None,
    ) -> VolumeProfileSnapshot:
        snap = profile_from_vap(
            self.volume_by_price,
            ts_event_ns=ts_event_ns,
            instrument=instrument,
            active_contract=active_contract,
            profile_kind=profile_kind,
            start_ns=self.start_ns or ts_event_ns,
            end_ns=self.end_ns or ts_event_ns,
            value_area_fraction=self.value_area_fraction,
            hvn_quantile=self.hvn_quantile,
            lvn_quantile=self.lvn_quantile,
            stats=self.zone_tracker.stats,
        )
        keys = [(z.low_ticks, z.high_ticks) for z in (*snap.lvn_zones, *snap.hvn_zones)]
        self.zone_tracker.refresh(keys, self.zone_tracker.current_period if period_index is None else period_index)
        return snap

    def reset(self) -> None:
        self.volume_by_price = {}
        self.start_ns = None
        self.end_ns = None
        self.zone_tracker = ZoneTracker()

    @classmethod
    def from_trades(
        cls,
        trades: list[TradePrint],
        *,
        ts_event_ns: int,
        instrument: str,
        active_contract: str,
        profile_kind: str = "arbitrary_range",
        value_area_fraction: float = 0.70,
        hvn_quantile: float = 0.75,
        lvn_quantile: float = 0.25,
    ) -> VolumeProfileSnapshot:
        engine = cls(value_area_fraction, hvn_quantile, lvn_quantile)
        for trade in trades:
            engine.consume(trade)
        return engine.snapshot(ts_event_ns, instrument, active_contract, profile_kind=profile_kind)

    @classmethod
    def composite_from_snapshots(
        cls,
        profiles: list[VolumeProfileSnapshot],
        *,
        ts_event_ns: int,
        instrument: str,
        active_contract: str,
        value_area_fraction: float = 0.70,
        hvn_quantile: float = 0.75,
        lvn_quantile: float = 0.25,
    ) -> VolumeProfileSnapshot:
        vap: dict[int, int] = {}
        for p in profiles:
            for price, vol in p.volume_by_price.items():
                vap[int(price)] = vap.get(int(price), 0) + int(vol)
        return profile_from_vap(
            vap,
            ts_event_ns=ts_event_ns,
            instrument=instrument,
            active_contract=active_contract,
            profile_kind="composite",
            start_ns=min((p.start_ns for p in profiles), default=ts_event_ns),
            end_ns=max((p.end_ns for p in profiles), default=ts_event_ns),
            value_area_fraction=value_area_fraction,
            hvn_quantile=hvn_quantile,
            lvn_quantile=lvn_quantile,
        )
