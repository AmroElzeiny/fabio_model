from __future__ import annotations

from collections import defaultdict
from statistics import median

from fabio_market_replay.contracts import ProfileZone, TradePrint, VolumeProfileSnapshot


class VolumeProfileEngine:
    def __init__(self, value_area_fraction: float = 0.70, hvn_quantile: float = 0.75, lvn_quantile: float = 0.25) -> None:
        self.value_area_fraction = value_area_fraction
        self.hvn_quantile = hvn_quantile
        self.lvn_quantile = lvn_quantile
        self.volume_by_price: dict[int, int] = defaultdict(int)
        self.start_ns: int | None = None
        self.end_ns: int | None = None
        self.retests: dict[tuple[int, int], int] = defaultdict(int)

    def consume(self, trade: TradePrint) -> None:
        self.start_ns = trade.ts_event_ns if self.start_ns is None else self.start_ns
        self.end_ns = trade.ts_event_ns
        self.volume_by_price[trade.price_ticks] += trade.size
        for zone in list(self.retests):
            if zone[0] <= trade.price_ticks <= zone[1]:
                self.retests[zone] += 1

    @staticmethod
    def _quantile(values: list[int], q: float) -> float:
        if not values:
            return 0.0
        vals = sorted(values)
        pos = q * (len(vals) - 1)
        lo = int(pos); hi = min(len(vals) - 1, lo + 1)
        frac = pos - lo
        return vals[lo] * (1 - frac) + vals[hi] * frac

    def _value_area(self) -> tuple[int | None, int | None, int | None]:
        if not self.volume_by_price:
            return None, None, None
        poc = max(self.volume_by_price, key=lambda p: (self.volume_by_price[p], -abs(p)))
        total = sum(self.volume_by_price.values())
        target = total * self.value_area_fraction
        included = {poc}
        accum = self.volume_by_price[poc]
        prices = sorted(self.volume_by_price)
        idx = prices.index(poc)
        lo = hi = idx
        while accum < target and (lo > 0 or hi < len(prices) - 1):
            left_v = self.volume_by_price[prices[lo - 1]] if lo > 0 else -1
            right_v = self.volume_by_price[prices[hi + 1]] if hi < len(prices) - 1 else -1
            if right_v > left_v:
                hi += 1; p = prices[hi]
            else:
                lo -= 1; p = prices[lo]
            included.add(p); accum += self.volume_by_price[p]
        return poc, max(included), min(included)

    def _zones(self, low: bool) -> list[ProfileZone]:
        if len(self.volume_by_price) < 3:
            return []
        prices = sorted(self.volume_by_price)
        vols = [self.volume_by_price[p] for p in prices]
        threshold = self._quantile(vols, self.lvn_quantile if low else self.hvn_quantile)
        flags = [(v <= threshold) if low else (v >= threshold) for v in vols]
        zones: list[ProfileZone] = []
        i = 0
        med = median(vols) or 1
        while i < len(prices):
            if not flags[i]:
                i += 1; continue
            j = i
            while j + 1 < len(prices) and flags[j + 1] and prices[j + 1] == prices[j] + 1:
                j += 1
            zone_prices = prices[i:j+1]
            zone_vol = sum(self.volume_by_price[p] for p in zone_prices) / len(zone_prices)
            neighbors = []
            if i > 0: neighbors.append(self.volume_by_price[prices[i-1]])
            if j + 1 < len(prices): neighbors.append(self.volume_by_price[prices[j+1]])
            neighbor_mean = sum(neighbors) / len(neighbors) if neighbors else zone_vol
            if low:
                relative = max(0.0, 1.0 - zone_vol / med)
                ratio = neighbor_mean / max(zone_vol, 1)
            else:
                relative = zone_vol / med
                ratio = zone_vol / max(neighbor_mean, 1)
            key = (zone_prices[0], zone_prices[-1])
            self.retests.setdefault(key, 0)
            zones.append(ProfileZone(low_ticks=key[0], high_ticks=key[1], relative_volume_metric=float(relative),
                                     neighboring_volume_ratio=float(ratio), width_ticks=key[1]-key[0]+1,
                                     persistence=1, age_events=0, retest_count=self.retests[key]))
            i = j + 1
        return zones

    def _shape(self, poc: int | None) -> str:
        if poc is None or not self.volume_by_price:
            return "EMPTY"
        prices = list(self.volume_by_price)
        lo, hi = min(prices), max(prices)
        if hi == lo:
            return "SINGLE_PRICE"
        rel = (poc - lo) / (hi - lo)
        if rel < 0.33:
            return "b_SHAPE"
        if rel > 0.67:
            return "P_SHAPE"
        return "D_SHAPE"

    def snapshot(self, ts_event_ns: int, instrument: str, active_contract: str, profile_kind: str = "developing_session") -> VolumeProfileSnapshot:
        poc, vah, val = self._value_area()
        total = sum(self.volume_by_price.values())
        return VolumeProfileSnapshot(ts_event_ns=ts_event_ns, instrument=instrument, active_contract=active_contract,
                                     profile_kind=profile_kind, start_ns=self.start_ns or ts_event_ns,
                                     end_ns=self.end_ns or ts_event_ns, poc_ticks=poc, vah_ticks=vah, val_ticks=val,
                                     hvn_zones=self._zones(low=False), lvn_zones=self._zones(low=True),
                                     volume_by_price=dict(sorted(self.volume_by_price.items())),
                                     profile_shape=self._shape(poc), total_volume=total,
                                     value_area_fraction=self.value_area_fraction)

    def reset(self) -> None:
        self.volume_by_price.clear()
        self.start_ns = None
        self.end_ns = None
        self.retests.clear()

    @classmethod
    def from_trades(cls, trades: list[TradePrint], *, ts_event_ns: int, instrument: str,
                    active_contract: str, profile_kind: str = "arbitrary_range", **kwargs) -> VolumeProfileSnapshot:
        engine = cls(**kwargs)
        for trade in trades:
            engine.consume(trade)
        return engine.snapshot(ts_event_ns, instrument, active_contract, profile_kind=profile_kind)

    @classmethod
    def composite_from_snapshots(cls, profiles: list[VolumeProfileSnapshot], *, ts_event_ns: int,
                                 instrument: str, active_contract: str,
                                 value_area_fraction: float = 0.70,
                                 hvn_quantile: float = 0.75,
                                 lvn_quantile: float = 0.25) -> VolumeProfileSnapshot:
        engine = cls(value_area_fraction=value_area_fraction, hvn_quantile=hvn_quantile, lvn_quantile=lvn_quantile)
        if profiles:
            engine.start_ns = min(p.start_ns for p in profiles)
            engine.end_ns = max(p.end_ns for p in profiles)
        for p in profiles:
            for price, vol in p.volume_by_price.items():
                engine.volume_by_price[int(price)] += int(vol)
        return engine.snapshot(ts_event_ns, instrument, active_contract, profile_kind="composite")
