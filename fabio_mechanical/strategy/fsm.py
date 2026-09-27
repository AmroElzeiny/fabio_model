"""Mechanical Fabio finite-state machines: Trend and Mean Reversion (Package 2, tick units).

Market State -> Location -> Aggression. The transition graphs are Package 2's. Integration
fixes (all documented in INTEGRATION_REPORT.md, none changes an entry rule):

* ``EXIT`` returns to ``BALANCED`` (Package 2 machines stayed in ``EXIT`` forever: one trade per run);
* setup age is measured from the setup start in structure periods (Package 2 counted every
  update since construction, killing any multi-update setup after 40 updates);
* ``ENTRY_READY`` returns to ``BALANCED`` when risk/execution rejects the candidate;
* a setup is reset if price trades through its structural invalidation level before entry;
* candidates are only produced with a valid target in the trade direction and valid geometry;
* ``aggression_persistence`` (declared, unused in Package 2) is enforced; baseline value 1
  equals Package 2 behaviour; trend aggression must be at location (aggression window traded
  in the LVN); progress requires the excursion side to stay in the setup direction;
* a setup is bound to the reference balance it started from (reset if the balance changes) and
  the failed auction must be a breakout attempt of at least ``breakout_distance_ticks``;
* the mean-reversion target is the POC of that balance captured at setup start;
* one trade per structural leg (a traded impulse/reclaim leg id cannot start a new entry);
* mean reversion uses the same location semantics as trend: the LVN retest must have happened
  (price traded inside the LVN) and the snap-back aggression must be at location (aggression
  window traded inside the LVN). Package 2 required price to still be inside the LVN on the same
  snapshot where the snap-back aggression is measured, which a snap-back contradicts.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fabio_mechanical.configs import config_hash
from fabio_mechanical.contracts import (
    DecisionSnapshot,
    Direction,
    MarketFeatureSnapshot,
    ProfileZone,
    SetupType,
    TradeCandidate,
    payload_sha256,
)

from .common import aggression_evidence, at_location, choose_lvn, is_trade_safe, lvn_touched, session_allowed
from .target_stop import stop_for, target_for


class InvalidTransition(RuntimeError):
    pass


class TrendState(str, Enum):
    BALANCED = "BALANCED"
    VALID_BREAKOUT = "VALID_BREAKOUT"
    DISPLACEMENT = "DISPLACEMENT"
    STRUCTURE_BREAK = "STRUCTURE_BREAK"
    IMPULSE_LOCKED = "IMPULSE_LOCKED"
    IMPULSE_PROFILE_READY = "IMPULSE_PROFILE_READY"
    WAIT_LVN_RETEST = "WAIT_LVN_RETEST"
    LVN_TOUCHED = "LVN_TOUCHED"
    WAIT_AGGRESSION = "WAIT_AGGRESSION"
    ENTRY_READY = "ENTRY_READY"
    ACTIVE_TRADE = "ACTIVE_TRADE"
    EXIT = "EXIT"


class MeanRevState(str, Enum):
    BALANCED = "BALANCED"
    BREAKOUT_ATTEMPT = "BREAKOUT_ATTEMPT"
    NO_ACCEPTANCE = "NO_ACCEPTANCE"
    FAILED_AUCTION = "FAILED_AUCTION"
    VALUE_RECLAIM = "VALUE_RECLAIM"
    RECLAIM_LEG_LOCKED = "RECLAIM_LEG_LOCKED"
    RECLAIM_PROFILE = "RECLAIM_PROFILE"
    WAIT_LVN_RETEST = "WAIT_LVN_RETEST"
    SNAPBACK_AGGRESSION = "SNAPBACK_AGGRESSION"
    ENTRY_READY = "ENTRY_READY"
    ACTIVE_TRADE = "ACTIVE_TRADE"
    EXIT = "EXIT"


TREND_TRANSITIONS: dict[TrendState, set[TrendState]] = {
    TrendState.BALANCED: {TrendState.VALID_BREAKOUT},
    TrendState.VALID_BREAKOUT: {TrendState.DISPLACEMENT},
    TrendState.DISPLACEMENT: {TrendState.STRUCTURE_BREAK},
    TrendState.STRUCTURE_BREAK: {TrendState.IMPULSE_LOCKED},
    TrendState.IMPULSE_LOCKED: {TrendState.IMPULSE_PROFILE_READY},
    TrendState.IMPULSE_PROFILE_READY: {TrendState.WAIT_LVN_RETEST},
    TrendState.WAIT_LVN_RETEST: {TrendState.LVN_TOUCHED},
    TrendState.LVN_TOUCHED: {TrendState.WAIT_AGGRESSION},
    TrendState.WAIT_AGGRESSION: {TrendState.ENTRY_READY},
    TrendState.ENTRY_READY: {TrendState.ACTIVE_TRADE},
    TrendState.ACTIVE_TRADE: {TrendState.EXIT},
    TrendState.EXIT: {TrendState.BALANCED},
}

MEANREV_TRANSITIONS: dict[MeanRevState, set[MeanRevState]] = {
    MeanRevState.BALANCED: {MeanRevState.BREAKOUT_ATTEMPT},
    MeanRevState.BREAKOUT_ATTEMPT: {MeanRevState.NO_ACCEPTANCE},
    MeanRevState.NO_ACCEPTANCE: {MeanRevState.FAILED_AUCTION},
    MeanRevState.FAILED_AUCTION: {MeanRevState.VALUE_RECLAIM},
    MeanRevState.VALUE_RECLAIM: {MeanRevState.RECLAIM_LEG_LOCKED},
    MeanRevState.RECLAIM_LEG_LOCKED: {MeanRevState.RECLAIM_PROFILE},
    MeanRevState.RECLAIM_PROFILE: {MeanRevState.WAIT_LVN_RETEST},
    MeanRevState.WAIT_LVN_RETEST: {MeanRevState.SNAPBACK_AGGRESSION},
    MeanRevState.SNAPBACK_AGGRESSION: {MeanRevState.ENTRY_READY},
    MeanRevState.ENTRY_READY: {MeanRevState.ACTIVE_TRADE},
    MeanRevState.ACTIVE_TRADE: {MeanRevState.EXIT},
    MeanRevState.EXIT: {MeanRevState.BALANCED},
}


@dataclass(frozen=True, slots=True)
class Transition:
    model: str
    timestamp_ns: int
    event_index: int
    from_state: str
    to_state: str
    reason: str
    direction: str
    price_ticks: int | None
    detail: dict[str, Any] = field(default_factory=dict)


def _opposite(d: Direction) -> Direction:
    return Direction.SHORT if d == Direction.LONG else Direction.LONG if d == Direction.SHORT else Direction.FLAT


@dataclass
class MachineBase:
    cfg: dict[str, Any]
    model: str = ""
    setup_type: SetupType = SetupType.TREND
    history: list[str] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    direction: Direction = Direction.FLAT
    setup_start_period: int = 0
    node: ProfileZone | None = None
    aggression_streak: int = 0
    last_rejection: str | None = None
    clock_hint: tuple[int, int] = (0, 0)
    setup_balance_id: str | None = None
    setup_balance_poc: int | None = None
    setup_leg_id: str | None = None
    retest_seen: bool = False
    traded_legs: list[str] = field(default_factory=list)
    _config_hash: str = ""

    def __post_init__(self) -> None:
        self._config_hash = config_hash(self.cfg)
        self.history = [self.state.value]

    # the concrete classes define ``state`` and ``_graph``
    state: Any = None

    def _graph(self) -> dict[Any, set[Any]]:
        raise NotImplementedError

    def _log(self, s: MarketFeatureSnapshot | None, old: Any, new: Any, reason: str, detail: dict[str, Any] | None = None) -> None:
        self.transitions.append(
            Transition(
                model=self.model,
                timestamp_ns=s.timestamp_ns if s is not None else self.clock_hint[0],
                event_index=s.event_index if s is not None else self.clock_hint[1],
                from_state=old.value,
                to_state=new.value,
                reason=reason,
                direction=self.direction.value,
                price_ticks=s.last_trade_ticks if s is not None else None,
                detail=detail or {},
            )
        )

    def force_transition(self, new_state: Any, s: MarketFeatureSnapshot | None = None, reason: str = "rule") -> None:
        if new_state not in self._graph()[self.state]:
            raise InvalidTransition(f"{self.state}->{new_state}")
        old = self.state
        self.state = new_state
        if not self.history or self.history[-1] != new_state.value:
            self.history.append(new_state.value)
        self._log(s, old, new_state, reason)

    def reset(self, s: MarketFeatureSnapshot | None, reason: str) -> None:
        old = self.state
        balanced = type(self.state).BALANCED
        if old == balanced:
            return
        self.state = balanced
        self._log(s, old, balanced, reason)
        self.direction = Direction.FLAT
        self.node = None
        self.aggression_streak = 0
        self.retest_seen = False
        self.history = [balanced.value]

    def drain_transitions(self) -> list[Transition]:
        out, self.transitions = self.transitions, []
        return out

    def _expired(self, s: MarketFeatureSnapshot) -> bool:
        return s.structure_period_index - self.setup_start_period > int(self.cfg["strategy"]["setup_expiry_events"])

    def _start_setup(self, s: MarketFeatureSnapshot, direction: Direction) -> None:
        self.direction = direction
        self.setup_balance_id = s.auction.balance_id
        self.setup_balance_poc = s.auction.balance_poc_ticks
        self.setup_leg_id = None
        self.retest_seen = False
        self.setup_start_period = s.structure_period_index
        self.aggression_streak = 0
        self.node = None

    def _aggression_persisted(self, s: MarketFeatureSnapshot) -> tuple[bool, dict[str, Any]]:
        ev = aggression_evidence(s, self.direction, self.cfg)
        self.aggression_streak = self.aggression_streak + 1 if ev["ok"] else 0
        return self.aggression_streak >= int(self.cfg["strategy"]["aggression_persistence"]), ev

    def _invalidated(self, s: MarketFeatureSnapshot) -> bool:
        if self.node is None or s.last_trade_ticks is None:
            return False
        stop = stop_for(s, self.setup_type, self.direction, self.node, self.cfg)
        px = s.last_trade_ticks
        return px <= stop if self.direction == Direction.LONG else px >= stop

    # ------------------------------------------------------------------ trade lifecycle hooks
    def on_entry_rejected(self, s: MarketFeatureSnapshot | None, reason: str) -> None:
        if self.state == type(self.state).ENTRY_READY:
            self.reset(s, f"entry_rejected:{reason}")

    def on_trade_opened(self, s: MarketFeatureSnapshot | None) -> None:
        self.force_transition(type(self.state).ACTIVE_TRADE, s, "entry_filled")
        if self.setup_leg_id:
            # one trade per structural leg: the same impulse/reclaim leg is never traded twice
            self.traded_legs = (self.traded_legs + [self.setup_leg_id])[-50:]

    def on_trade_closed(self, s: MarketFeatureSnapshot | None, reason: str) -> None:
        if self.state == type(self.state).ACTIVE_TRADE:
            self.force_transition(type(self.state).EXIT, s, f"exit:{reason}")

    def on_market_reset(self, s: MarketFeatureSnapshot | None, reason: str) -> None:
        if self.state not in {type(self.state).ACTIVE_TRADE, type(self.state).ENTRY_READY}:
            self.reset(s, reason)

    # ------------------------------------------------------------------ candidate
    def _candidate(self, s: MarketFeatureSnapshot, state_name: str, evidence: dict[str, Any]) -> TradeCandidate | None:
        assert self.node is not None and s.last_trade_ticks is not None
        entry = s.last_trade_ticks
        stop = stop_for(s, self.setup_type, self.direction, self.node, self.cfg)
        target = target_for(s, self.setup_type, self.direction, entry, self.setup_balance_poc)
        long = self.direction == Direction.LONG
        if target is None:
            self.last_rejection = "no_balance_poc_target_in_trade_direction"
            return None
        if not ((stop < entry < target) if long else (target < entry < stop)):
            self.last_rejection = "invalid_stop_entry_target_geometry"
            return None
        evidence = {
            "state_history": list(self.history) + [state_name],
            "lvn": self.node.model_dump(mode="json"),
            "entry_reference_ticks": entry,
            "stop_ticks": stop,
            "target_ticks": target,
            "balance_id": s.auction.balance_id,
            "balance_poc_ticks": s.auction.balance_poc_ticks,
            **evidence,
        }
        cid = hashlib.sha256(
            f"{self._config_hash}|{self.setup_type.value}|{self.direction.value}|{s.timestamp_ns}|{s.event_index}".encode()
        ).hexdigest()[:32]
        decision = DecisionSnapshot(
            timestamp_ns=s.timestamp_ns,
            instrument=s.instrument,
            active_contract=s.active_contract,
            setup_type=self.setup_type,
            direction=self.direction,
            state=state_name,
            decision="ENTER",
            evidence=evidence,
            parameter_version=str(self.cfg.get("version", "unknown")),
            config_hash=self._config_hash,
            market_feature_sha256=payload_sha256(s),
            trade_safe=s.trade_safe,
        )
        return TradeCandidate(
            candidate_id=cid,
            setup_type=self.setup_type,
            direction=self.direction,
            timestamp_ns=s.timestamp_ns,
            event_index=s.event_index,
            instrument=s.instrument,
            active_contract=s.active_contract,
            entry_reference_ticks=entry,
            stop_ticks=stop,
            target_ticks=target,
            evidence=evidence,
            decision_snapshot=decision,
        )

    def _pre_update(self, s: MarketFeatureSnapshot, model_key: str) -> bool:
        """Lifecycle handling shared by both machines. Returns False when no rule may run."""
        cls = type(self.state)
        if self.state == cls.EXIT:
            self.force_transition(cls.BALANCED, s, "post_exit_reset")
            self.direction = Direction.FLAT
            self.node = None
            self.history = [cls.BALANCED.value]
        if self.state in {cls.ACTIVE_TRADE, cls.ENTRY_READY}:
            return False
        if self.state != cls.BALANCED and self._expired(s):
            self.reset(s, "setup_expired")
        if self.state != cls.BALANCED and s.auction.balance_id != self.setup_balance_id:
            self.reset(s, "reference_balance_changed")
        if not is_trade_safe(s, self.cfg):
            return False
        return session_allowed(s, model_key, self.cfg)


@dataclass
class TrendMachine(MachineBase):
    state: TrendState = TrendState.BALANCED
    model: str = "TREND"
    setup_type: SetupType = SetupType.TREND

    def _graph(self) -> dict[Any, set[Any]]:
        return TREND_TRANSITIONS  # type: ignore[return-value]

    def _in_direction(self, s: MarketFeatureSnapshot) -> bool:
        return s.auction.breakout_side == self.direction

    def _oob(self, s: MarketFeatureSnapshot) -> bool:
        return s.auction.out_of_balance and self._in_direction(s)

    def _pullback_in_range(self, s: MarketFeatureSnapshot) -> bool:
        a = s.auction
        p = self.cfg["strategy"]
        if a.impulse_low_ticks is None or a.impulse_high_ticks is None or a.impulse_ticks <= 0 or s.last_trade_ticks is None:
            return False
        px = s.last_trade_ticks
        depth = (
            (a.impulse_high_ticks - px) / a.impulse_ticks
            if self.direction == Direction.LONG
            else (px - a.impulse_low_ticks) / a.impulse_ticks
        )
        return float(p["pullback_depth_min"]) <= depth <= float(p["pullback_depth_max"])

    def update(self, s: MarketFeatureSnapshot) -> TradeCandidate | None:
        if not self._pre_update(s, "trend"):
            return None
        p = self.cfg["strategy"]
        a = s.auction
        T = TrendState
        if (
            self.state == T.BALANCED
            and a.out_of_balance
            and a.breakout_side in {Direction.LONG, Direction.SHORT}
            and a.breakout_distance_ticks >= p["breakout_distance_ticks"]
        ):
            self._start_setup(s, a.breakout_side)
            self.force_transition(T.VALID_BREAKOUT, s, "out_of_balance_breakout")
        if self.state == T.VALID_BREAKOUT and self._in_direction(s) and a.displacement_zscore >= p["displacement_zscore_min"]:
            self.force_transition(T.DISPLACEMENT, s, "displacement")
        if self.state == T.DISPLACEMENT and self._in_direction(s) and a.structure_break:
            self.force_transition(T.STRUCTURE_BREAK, s, "structure_break")
        if (
            self.state == T.STRUCTURE_BREAK
            and self._in_direction(s)
            and a.impulse_id
            and a.impulse_id not in self.traded_legs
            and a.impulse_ticks >= p["impulse_min_ticks"]
        ):
            self.setup_leg_id = a.impulse_id
            self.force_transition(T.IMPULSE_LOCKED, s, "impulse_locked")
        if self.state == T.IMPULSE_LOCKED and s.impulse_profile is not None:
            self.node = choose_lvn(s, "impulse_profile", self.direction, self.cfg)
            if self.node is not None:
                self.force_transition(T.IMPULSE_PROFILE_READY, s, "qualifying_lvn")
        if self.state == T.IMPULSE_PROFILE_READY:
            self.force_transition(T.WAIT_LVN_RETEST, s, "wait_retest")
        if self.state in {T.WAIT_LVN_RETEST, T.LVN_TOUCHED, T.WAIT_AGGRESSION} and self._invalidated(s):
            self.reset(s, "structural_invalidation_before_entry")
            return None
        if self.state == T.WAIT_LVN_RETEST and self._oob(s) and lvn_touched(s, self.node) and self._pullback_in_range(s):
            self.force_transition(T.LVN_TOUCHED, s, "lvn_retest")
        if self.state == T.LVN_TOUCHED:
            self.force_transition(T.WAIT_AGGRESSION, s, "wait_aggression")
        if self.state == T.WAIT_AGGRESSION:
            if not (self._oob(s) and at_location(s, self.node)):
                self.aggression_streak = 0
                return None
            persisted, ev = self._aggression_persisted(s)
            if not persisted:
                return None
            cand = self._candidate(
                s,
                T.ENTRY_READY.value,
                {
                    "aggression": ev,
                    "out_of_balance": a.out_of_balance,
                    "impulse_id": a.impulse_id,
                    "displacement_zscore": a.displacement_zscore,
                    "impulse_efficiency": a.impulse_efficiency,
                },
            )
            if cand is None:
                self.reset(s, f"no_candidate:{self.last_rejection}")
                return None
            self.force_transition(T.ENTRY_READY, s, "directional_aggression_at_lvn")
            return cand
        return None


@dataclass
class MeanReversionMachine(MachineBase):
    state: MeanRevState = MeanRevState.BALANCED
    model: str = "MEAN_REVERSION"
    setup_type: SetupType = SetupType.MEAN_REVERSION

    def _graph(self) -> dict[Any, set[Any]]:
        return MEANREV_TRANSITIONS  # type: ignore[return-value]

    def update(self, s: MarketFeatureSnapshot) -> TradeCandidate | None:
        if not self._pre_update(s, "mean_reversion"):
            return None
        p = self.cfg["strategy"]
        a = s.auction
        M = MeanRevState
        if (
            self.state == M.BALANCED
            and a.breakout_side in {Direction.LONG, Direction.SHORT}
            and a.breakout_distance_ticks >= p["breakout_distance_ticks"]
        ):
            self._start_setup(s, _opposite(a.breakout_side))
            self.force_transition(M.BREAKOUT_ATTEMPT, s, "breakout_attempt")
        attempt_side = _opposite(self.direction)
        if self.state == M.BREAKOUT_ATTEMPT and a.breakout_side == attempt_side and a.acceptance_events < p["acceptance_event_count"]:
            self.force_transition(M.NO_ACCEPTANCE, s, "no_acceptance")
        if (
            self.state == M.NO_ACCEPTANCE
            and a.breakout_side == attempt_side
            and a.failed_auction
            and a.breakout_distance_ticks >= p["breakout_distance_ticks"]
        ):
            self.force_transition(M.FAILED_AUCTION, s, "failed_auction")
        if self.state == M.FAILED_AUCTION and a.reclaim_inside_balance:
            self.force_transition(M.VALUE_RECLAIM, s, "value_reclaim")
        if self.state == M.VALUE_RECLAIM and a.reclaim_leg_id and a.reclaim_leg_id not in self.traded_legs:
            self.setup_leg_id = a.reclaim_leg_id
            self.force_transition(M.RECLAIM_LEG_LOCKED, s, "reclaim_leg_locked")
        if self.state == M.RECLAIM_LEG_LOCKED and s.reclaim_profile is not None:
            self.node = choose_lvn(s, "reclaim_profile", self.direction, self.cfg)
            if self.node is not None:
                self.force_transition(M.RECLAIM_PROFILE, s, "qualifying_lvn")
        if self.state == M.RECLAIM_PROFILE:
            self.force_transition(M.WAIT_LVN_RETEST, s, "wait_retest")
        if self.state == M.WAIT_LVN_RETEST and self._invalidated(s):
            self.reset(s, "structural_invalidation_before_entry")
            return None
        if self.state == M.WAIT_LVN_RETEST:
            if lvn_touched(s, self.node):
                self.retest_seen = True
            if self.retest_seen and at_location(s, self.node) and s.rejection_speed >= p["rejection_speed_min"]:
                persisted, ev = self._aggression_persisted(s)
                if persisted:
                    self.force_transition(M.SNAPBACK_AGGRESSION, s, "snapback_aggression_at_lvn")
                    cand = self._candidate(
                        s,
                        M.ENTRY_READY.value,
                        {
                            "aggression": ev,
                            "failed_auction": a.failed_auction,
                            "reclaim": a.reclaim_inside_balance,
                            "rejection_speed": s.rejection_speed,
                            "failed_auction_extreme_ticks": a.failed_auction_extreme_ticks,
                        },
                    )
                    if cand is None:
                        self.reset(s, f"no_candidate:{self.last_rejection}")
                        return None
                    self.force_transition(M.ENTRY_READY, s, "entry")
                    return cand
            else:
                self.aggression_streak = 0
        return None
