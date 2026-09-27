"""Auction structure layer: balance areas, excursions, impulse and reclaim legs.

Package 2's FSMs consume ``balanced``/``out_of_balance``/``breakout_*``/``acceptance_events``/
``displacement_zscore``/``structure_break``/``impulse_*``/``failed_auction``/``reclaim_*`` and
leg volume profiles, but neither pre-integration package computed them. This module is the
causal, deterministic measurement layer that produces them from executed trades only.
Every definition below is documented in RULE_TRACEABILITY.md; thresholds come from
``interpretation.yaml`` (``strategy`` definitions + ``structure`` operational settings).

Definitions (tick units, replay clock):

* Structure period: ``period_seconds`` clock buckets; the unit of every ``*_events`` counter.
* Balance: at each period close, the last ``balance_window_minutes`` are split in two halves.
  The window is BALANCED when value-area overlap of the halves >= ``balance_value_overlap_min``,
  |POC(second) - POC(first)| <= ``poc_migration_max`` and the directional efficiency of
  ``sample_seconds`` samples over the window <= ``directional_efficiency_max_for_balance``.
  The reference balance is the window value area (VAL..VAH, POC, range low/high).
* Reference update: while an excursion is active the reference is frozen unless a new balanced
  window whose value area does not overlap it forms (new value accepted elsewhere); the old
  reference is then archived and its POC stays a target candidate.
* Excursion: trades beyond VAH (up) or VAL (down). ``breakout_distance_ticks`` is the maximum
  excursion beyond the edge; ``acceptance_events`` counts completed periods that closed beyond
  the edge. Returning inside ends the excursion; it is a FAILED AUCTION when acceptance was
  below ``acceptance_event_count``; ``reclaim_inside_balance`` is true while price is back
  inside value after a failed auction.
* Impulse leg (trend): starts at the last trade on the far side of the reference POC and runs
  to the excursion extreme. ``structure_break`` = extreme beyond the balance range extreme.
  ``impulse_efficiency`` = |extreme - origin| / sampled path length. ``out_of_balance`` =
  active excursion with impulse efficiency >= ``directional_efficiency_min``. The leg LOCKS
  (``impulse_id`` + frozen ``impulse_profile``) once price retraces >= ``pullback_depth_min`` of it.
* Reclaim leg (mean reversion): from the excursion extreme back toward value; it locks the same
  way and yields ``reclaim_leg_id`` + ``reclaim_profile``. ``rejection_speed`` = reclaim-leg
  velocity / excursion velocity, clipped to [0, 1].
* Displacement z-score: signed move over the last period in the excursion direction versus the
  mean/std of absolute close-to-close moves of recent active periods.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from fabio_mechanical.contracts import BalanceArea, Direction, ProfileZone, VolumeProfileSnapshot
from fabio_mechanical.profile.volume_profile import ZoneTracker, profile_from_vap, value_area

NS = 1_000_000_000


@dataclass(frozen=True, slots=True)
class StructureSettings:
    period_ns: int
    sample_ns: int
    window_periods: int
    overlap_min: float
    poc_migration_max: float
    efficiency_max_balance: float
    efficiency_min_trend: float
    acceptance_count: int
    pullback_min: float
    displacement_lookback: int
    displacement_min_periods: int
    value_area_fraction: float
    lvn_quantile: float
    hvn_quantile: float
    max_archived: int
    max_prior_sessions: int

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> StructureSettings:
        s = cfg["strategy"]
        st = cfg["structure"]
        m = cfg["market"]
        period_ns = int(st["period_seconds"]) * NS
        return cls(
            period_ns=period_ns,
            sample_ns=int(st["sample_seconds"]) * NS,
            window_periods=int(st["balance_window_minutes"]) * 60 * NS // period_ns,
            overlap_min=float(s["balance_value_overlap_min"]),
            poc_migration_max=float(s["poc_migration_max"]),
            efficiency_max_balance=float(s["directional_efficiency_max_for_balance"]),
            efficiency_min_trend=float(s["directional_efficiency_min"]),
            acceptance_count=int(s["acceptance_event_count"]),
            pullback_min=float(s["pullback_depth_min"]),
            displacement_lookback=int(st["displacement_lookback_periods"]),
            displacement_min_periods=int(st["displacement_min_periods"]),
            value_area_fraction=float(m["value_area_fraction"]),
            lvn_quantile=float(m["lvn_quantile"]),
            hvn_quantile=float(m["hvn_quantile"]),
            max_archived=int(st["max_archived_balances"]),
            max_prior_sessions=int(st["max_prior_sessions"]),
        )


class LegTracker:
    """A directional leg from ``origin`` to its running extreme (``direction`` +1 up / -1 down)."""

    __slots__ = (
        "direction",
        "origin",
        "origin_ns",
        "extreme",
        "extreme_ns",
        "vap",
        "pending",
        "last_sample_price",
        "path_to_sample",
        "path_at_extreme",
        "locked",
        "lock_count",
        "lock_ns",
        "lock_period",
        "profile_tracker",
        "_base",
        "_profile",
        "_profile_key",
    )

    def __init__(self, direction: int, origin: int, origin_ns: int, size: int = 0) -> None:
        self.direction = direction
        self.origin = origin
        self.origin_ns = origin_ns
        self.extreme = origin
        self.extreme_ns = origin_ns
        self.vap: dict[int, int] = {origin: size} if size else {}
        self.pending: dict[int, int] = {}
        self.last_sample_price = origin
        self.path_to_sample = 0
        self.path_at_extreme = 0
        self.locked = False
        self.lock_count = 0
        self.lock_ns = 0
        self.lock_period = 0
        self.profile_tracker: ZoneTracker | None = None
        self._base: VolumeProfileSnapshot | None = None
        self._profile: VolumeProfileSnapshot | None = None
        self._profile_key: tuple[int, int] | None = None

    @property
    def length(self) -> int:
        return (self.extreme - self.origin) * self.direction

    def efficiency(self) -> float:
        if self.length <= 0:
            return 0.0
        return 1.0 if self.path_at_extreme <= 0 else min(1.0, self.length / self.path_at_extreme)

    def on_sample(self, price: int) -> None:
        self.path_to_sample += abs(price - self.last_sample_price)
        self.last_sample_price = price

    def on_trade(self, price: int, size: int, ns: int) -> None:
        if (price - self.extreme) * self.direction > 0:
            for p, v in self.pending.items():
                self.vap[p] = self.vap.get(p, 0) + v
            self.pending.clear()
            self.extreme = price
            self.extreme_ns = ns
            self.vap[price] = self.vap.get(price, 0) + size
            self.path_at_extreme = self.path_to_sample + abs(price - self.last_sample_price)
            self.locked = False
            self.profile_tracker = None
            self._base = None
            self._profile = None
        else:
            if price == self.extreme and not self.pending:
                self.vap[price] = self.vap.get(price, 0) + size
            else:
                self.pending[price] = self.pending.get(price, 0) + size
            if self.locked and self.profile_tracker is not None:
                self.profile_tracker.observe_price(price)

    def retrace_fraction(self, price: int) -> float:
        return 0.0 if self.length <= 0 else (self.extreme - price) * self.direction / self.length

    def try_lock(self, price: int, period: int, ns: int, *, s: StructureSettings, kind: str, instrument: str, contract: str) -> None:
        if self.locked or self.length <= 0 or self.retrace_fraction(price) < s.pullback_min:
            return
        self.locked = True
        self.lock_count += 1
        self.lock_ns = ns
        self.lock_period = period
        base = profile_from_vap(
            self.vap,
            ts_event_ns=ns,
            instrument=instrument,
            active_contract=contract,
            profile_kind=kind,
            start_ns=self.origin_ns,
            end_ns=self.extreme_ns,
            value_area_fraction=s.value_area_fraction,
            hvn_quantile=s.hvn_quantile,
            lvn_quantile=s.lvn_quantile,
        )
        tracker = ZoneTracker()
        tracker.refresh([(z.low_ticks, z.high_ticks) for z in (*base.lvn_zones, *base.hvn_zones)], period)
        self.profile_tracker = tracker
        self._base = base
        self._profile = base
        self._profile_key = (period, 0)

    def profile(self, period: int) -> VolumeProfileSnapshot | None:
        """Frozen leg profile with zone age (structure periods since lock) and revisit counts."""
        if not self.locked or self.profile_tracker is None or self._base is None:
            return None
        tracker = self.profile_tracker
        key = (period, sum(tracker.retests.values()))
        if self._profile is not None and self._profile_key == key:
            return self._profile
        tracker.current_period = period

        def upd(zs: list[ProfileZone]) -> list[ProfileZone]:
            out = []
            for z in zs:
                pers, age, ret = tracker.stats((z.low_ticks, z.high_ticks))
                out.append(z.model_copy(update={"age_events": age, "retest_count": ret, "persistence": pers}))
            return out

        base = self._base
        self._profile = base.model_copy(update={"lvn_zones": upd(base.lvn_zones), "hvn_zones": upd(base.hvn_zones)})
        self._profile_key = key
        return self._profile


@dataclass(slots=True)
class Excursion:
    side: int
    edge: int
    start_ns: int
    extreme: int
    extreme_ns: int
    counter_leg: LegTracker
    acceptance_periods: int = 0
    active: bool = True
    returned: bool = False
    failed: bool = False

    @property
    def distance(self) -> int:
        return (self.extreme - self.edge) * self.side


@dataclass(slots=True)
class _Ref:
    area: BalanceArea
    seq: int


@dataclass(slots=True)
class _Period:
    index: int
    vap: dict[int, int] = field(default_factory=dict)
    close: int | None = None


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]) + 1)
    union = max(a[1], b[1]) - min(a[0], b[0]) + 1
    return 0.0 if union <= 0 else inter / union


class AuctionStructureEngine:
    def __init__(self, settings: StructureSettings, instrument: str = "", contract: str = "") -> None:
        self.s = settings
        self.instrument = instrument
        self.contract = contract
        self.reset()

    # ------------------------------------------------------------------ lifecycle
    def reset(self) -> None:
        self.ref: _Ref | None = None
        self._ref_counter = 0
        self.archived: deque[BalanceArea] = deque(maxlen=self.s.max_archived)
        self.prior_sessions: deque[BalanceArea] = deque(maxlen=self.s.max_prior_sessions)
        self._periods: deque[_Period] = deque()
        self._cur: _Period | None = None
        self._samples: deque[tuple[int, int]] = deque()
        self._sample_bucket: int | None = None
        self._last_price: int | None = None
        self._period_moves: deque[int] = deque(maxlen=self.s.displacement_lookback)
        self._last_active_close: int | None = None
        self._recent: deque[tuple[int, int]] = deque()
        self.excursion: Excursion | None = None
        self.up_leg: LegTracker | None = None
        self.down_leg: LegTracker | None = None
        self.last_eval: dict[str, Any] = {"balanced": False, "overlap": None, "poc_migration": None, "efficiency": None}
        self._displacement_z = 0.0

    def on_contract_roll(self, contract: str) -> None:
        self.contract = contract
        self.reset()

    def add_prior_session(self, profile: VolumeProfileSnapshot) -> None:
        if profile.poc_ticks is None or profile.val_ticks is None or profile.vah_ticks is None or not profile.volume_by_price:
            return
        prices = [int(p) for p in profile.volume_by_price]
        self.prior_sessions.append(
            BalanceArea(
                balance_id=f"S:{profile.active_contract}:{profile.start_ns}",
                source="prior_session",
                active_contract=profile.active_contract,
                low_ticks=profile.val_ticks,
                high_ticks=profile.vah_ticks,
                poc_ticks=profile.poc_ticks,
                range_low_ticks=min(prices),
                range_high_ticks=max(prices),
                start_ns=profile.start_ns,
                end_ns=profile.end_ns,
            )
        )

    # ------------------------------------------------------------------ periods & balance
    def _close_period(self, period: _Period) -> None:
        self._periods.append(period)
        while len(self._periods) > self.s.window_periods:
            self._periods.popleft()
        if period.close is not None:
            if self._last_active_close is not None:
                self._period_moves.append(abs(period.close - self._last_active_close))
            self._last_active_close = period.close
            exc = self.excursion
            if exc is not None and exc.active and (period.close - exc.edge) * exc.side > 0:
                exc.acceptance_periods += 1
        self._evaluate_balance(period.index)

    def _advance_to(self, period_index: int) -> None:
        if self._cur is None:
            self._cur = _Period(period_index)
            return
        if period_index <= self._cur.index:
            return
        self._close_period(self._cur)
        gap = period_index - self._cur.index - 1
        if gap >= self.s.window_periods:
            self._periods.clear()
        else:
            for k in range(1, gap + 1):
                self._close_period(_Period(self._cur.index + k))
        self._cur = _Period(period_index)

    def _window_efficiency(self, start_ns: int) -> float | None:
        pts = [p for ns, p in self._samples if ns >= start_ns]
        if len(pts) < 2:
            return None
        path = sum(abs(b - a) for a, b in zip(pts, pts[1:], strict=False))
        return 0.0 if path == 0 else abs(pts[-1] - pts[0]) / path

    def _evaluate_balance(self, closed_index: int) -> None:
        s = self.s
        if len(self._periods) < s.window_periods or self._periods[0].index != closed_index - s.window_periods + 1:
            self.last_eval = {"balanced": False, "overlap": None, "poc_migration": None, "efficiency": None}
            return
        half = s.window_periods // 2
        first: dict[int, int] = {}
        second: dict[int, int] = {}
        for i, per in enumerate(self._periods):
            target = first if i < half else second
            for p, v in per.vap.items():
                target[p] = target.get(p, 0) + v
        if not first or not second:
            self.last_eval = {"balanced": False, "overlap": None, "poc_migration": None, "efficiency": None}
            return
        poc1, vah1, val1 = value_area(first, s.value_area_fraction)
        poc2, vah2, val2 = value_area(second, s.value_area_fraction)
        assert poc1 is not None and poc2 is not None and vah1 is not None and val1 is not None
        assert vah2 is not None and val2 is not None
        overlap = _overlap((val1, vah1), (val2, vah2))
        migration = poc2 - poc1
        window_start_ns = self._periods[0].index * s.period_ns
        eff = self._window_efficiency(window_start_ns)
        balanced = (
            overlap >= s.overlap_min and abs(migration) <= s.poc_migration_max and eff is not None and eff <= s.efficiency_max_balance
        )
        self.last_eval = {"balanced": balanced, "overlap": overlap, "poc_migration": migration, "efficiency": eff}
        if not balanced:
            return
        full = dict(first)
        for p, v in second.items():
            full[p] = full.get(p, 0) + v
        poc, vah, val = value_area(full, s.value_area_fraction)
        assert poc is not None and vah is not None and val is not None
        end_ns = (closed_index + 1) * s.period_ns
        self._update_reference(val, vah, poc, min(full), max(full), window_start_ns, end_ns)

    def _new_area(self, val: int, vah: int, poc: int, lo: int, hi: int, start: int, end: int, bid: str | None) -> BalanceArea:
        if bid is None:
            self._ref_counter += 1
            bid = f"B:{self.contract}:{start}:{self._ref_counter}"
        return BalanceArea(
            balance_id=bid,
            source="intraday_balance",
            active_contract=self.contract,
            low_ticks=val,
            high_ticks=vah,
            poc_ticks=poc,
            range_low_ticks=lo,
            range_high_ticks=hi,
            start_ns=start,
            end_ns=end,
        )

    def _update_reference(self, val: int, vah: int, poc: int, lo: int, hi: int, start: int, end: int) -> None:
        if self.ref is None:
            self.ref = _Ref(self._new_area(val, vah, poc, lo, hi, start, end, None), 0)
            self._reset_structure()
            return
        cur = self.ref.area
        overlaps = _overlap((cur.low_ticks, cur.high_ticks), (val, vah)) > 0
        exc_active = self.excursion is not None and self.excursion.active
        if overlaps and exc_active:
            return  # frozen while the market is testing the edge
        if overlaps:
            self.ref = _Ref(self._new_area(val, vah, poc, lo, hi, cur.start_ns, end, cur.balance_id), self.ref.seq + 1)
            return
        self.archived.append(cur)
        self.ref = _Ref(self._new_area(val, vah, poc, lo, hi, start, end, None), 0)
        self._reset_structure()

    def _reset_structure(self) -> None:
        self.excursion = None
        self.up_leg = None
        self.down_leg = None

    # ------------------------------------------------------------------ per trade
    def on_trade(self, price: int, size: int, ns: int) -> None:
        s = self.s
        bucket = ns // s.sample_ns
        if self._sample_bucket is None or bucket != self._sample_bucket:
            # the previous sample closes with the last price seen before this bucket
            if self._last_price is not None and self._sample_bucket is not None:
                self._samples.append((self._sample_bucket * s.sample_ns, self._last_price))
                for leg in (self.up_leg, self.down_leg):
                    if leg is not None:
                        leg.on_sample(self._last_price)
                if self.excursion is not None:
                    self.excursion.counter_leg.on_sample(self._last_price)
            self._sample_bucket = bucket
            horizon = ns - s.window_periods * s.period_ns - s.sample_ns
            while self._samples and self._samples[0][0] < horizon:
                self._samples.popleft()
        self._advance_to(ns // s.period_ns)
        assert self._cur is not None
        cur = self._cur
        cur.vap[price] = cur.vap.get(price, 0) + size
        cur.close = price
        self._last_price = price
        # price one period ago (for displacement)
        self._recent.append((ns, price))
        cutoff = ns - s.period_ns
        while len(self._recent) > 1 and self._recent[1][0] <= cutoff:
            self._recent.popleft()
        if self.ref is None:
            self._displacement_z = 0.0
            return
        area = self.ref.area
        # --- impulse leg trackers anchored at the reference POC ---
        if price <= area.poc_ticks:
            self.up_leg = LegTracker(1, price, ns, size)
        elif self.up_leg is not None:
            self.up_leg.on_trade(price, size, ns)
        if price >= area.poc_ticks:
            self.down_leg = LegTracker(-1, price, ns, size)
        elif self.down_leg is not None:
            self.down_leg.on_trade(price, size, ns)
        # --- excursion state machine ---
        side = 1 if price > area.high_ticks else -1 if price < area.low_ticks else 0
        exc = self.excursion
        if exc is not None and exc.active:
            if side == exc.side:
                if (price - exc.extreme) * exc.side > 0:
                    exc.extreme = price
                    exc.extreme_ns = ns
                    exc.counter_leg = LegTracker(-exc.side, price, ns, size)
                else:
                    exc.counter_leg.on_trade(price, size, ns)
            else:
                exc.active = False
                exc.returned = True
                exc.failed = exc.acceptance_periods < s.acceptance_count
                exc.counter_leg.on_trade(price, size, ns)
        elif exc is not None:
            exc.counter_leg.on_trade(price, size, ns)
        if side != 0 and (self.excursion is None or not self.excursion.active):
            edge = area.high_ticks if side > 0 else area.low_ticks
            self.excursion = Excursion(side, edge, ns, price, ns, LegTracker(-side, price, ns, size))
        exc = self.excursion
        period = ns // s.period_ns
        if exc is not None:
            inst, con = self.instrument, self.contract
            exc.counter_leg.try_lock(price, period, ns, s=s, kind="reclaim_leg", instrument=inst, contract=con)
            leg = self.up_leg if exc.side > 0 else self.down_leg
            if leg is not None and exc.active:
                leg.try_lock(price, period, ns, s=s, kind="impulse_leg", instrument=inst, contract=con)
        # --- displacement z-score in the excursion direction ---
        self._displacement_z = 0.0
        if exc is not None and len(self._period_moves) >= s.displacement_min_periods:
            moves = list(self._period_moves)
            mu = sum(moves) / len(moves)
            var = sum((m - mu) ** 2 for m in moves) / (len(moves) - 1)
            sd = math.sqrt(var)
            d = (price - self._recent[0][1]) * exc.side
            self._displacement_z = (d - mu) / sd if sd > 0 else 0.0

    # ------------------------------------------------------------------ output
    def poc_candidates(self) -> list[int]:
        pocs = {a.poc_ticks for a in self.archived} | {a.poc_ticks for a in self.prior_sessions}
        if self.ref is not None:
            pocs.add(self.ref.area.poc_ticks)
        return sorted(pocs)

    def balance_areas(self) -> list[BalanceArea]:
        out = list(self.prior_sessions) + list(self.archived)
        if self.ref is not None:
            out.append(self.ref.area)
        return out

    def fields(self, price: int | None, ns: int) -> dict[str, Any]:
        s = self.s
        ev = self.last_eval
        out: dict[str, Any] = {
            "balance_value_overlap": ev["overlap"],
            "balance_poc_migration_ticks": ev["poc_migration"],
            "balance_directional_efficiency": ev["efficiency"],
        }
        extra: dict[str, Any] = {"impulse_profile": None, "reclaim_profile": None, "rejection_speed": 0.0}
        if self.ref is None or price is None:
            out["balanced"] = False
            return {**out, **extra}
        a = self.ref.area
        inside = a.low_ticks <= price <= a.high_ticks
        out.update(
            balanced=bool(ev["balanced"]) and inside,
            balance_id=a.balance_id,
            balance_low_ticks=a.low_ticks,
            balance_high_ticks=a.high_ticks,
            balance_poc_ticks=a.poc_ticks,
            balance_range_low_ticks=a.range_low_ticks,
            balance_range_high_ticks=a.range_high_ticks,
        )
        exc = self.excursion
        if exc is None:
            return {**out, **extra}
        period = ns // s.period_ns
        direction = Direction.LONG if exc.side > 0 else Direction.SHORT
        out.update(
            excursion_active=exc.active,
            breakout_side=direction,
            breakout_distance_ticks=float(exc.distance),
            acceptance_events=exc.acceptance_periods,
            displacement_zscore=self._displacement_z,
        )
        if exc.active:
            range_edge = a.range_high_ticks if exc.side > 0 else a.range_low_ticks
            out["structure_break"] = (exc.extreme - range_edge) * exc.side > 0
            leg = self.up_leg if exc.side > 0 else self.down_leg
            if leg is not None and leg.length > 0:
                eff = leg.efficiency()
                out.update(
                    impulse_low_ticks=min(leg.origin, leg.extreme),
                    impulse_high_ticks=max(leg.origin, leg.extreme),
                    impulse_ticks=float(leg.length),
                    impulse_efficiency=eff,
                    out_of_balance=eff >= s.efficiency_min_trend,
                )
                if leg.locked:
                    out["impulse_id"] = f"{a.balance_id}:I{exc.side:+d}:{leg.origin_ns}:{leg.extreme_ns}"
                    extra["impulse_profile"] = leg.profile(period)
        elif exc.failed:
            out.update(failed_auction=True, failed_auction_extreme_ticks=exc.extreme, reclaim_inside_balance=inside)
            leg = exc.counter_leg
            if leg.length > 0:
                exc_seconds = max(1.0, (exc.extreme_ns - exc.start_ns) / NS)
                leg_seconds = max(1.0, (leg.extreme_ns - leg.origin_ns) / NS)
                v_exc = max(0, exc.distance) / exc_seconds
                v_leg = leg.length / leg_seconds
                extra["rejection_speed"] = min(1.0, v_leg / v_exc) if v_exc > 0 else 1.0
                if leg.locked:
                    out.update(
                        reclaim_leg_id=f"{a.balance_id}:R{exc.side:+d}:{exc.extreme_ns}:{leg.lock_count}",
                        reclaim_leg_low_ticks=min(leg.origin, leg.extreme),
                        reclaim_leg_high_ticks=max(leg.origin, leg.extreme),
                        reclaim_leg_ticks=float(leg.length),
                    )
                    extra["reclaim_profile"] = leg.profile(period)
        return {**out, **extra}
