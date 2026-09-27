from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4
from contracts.models import *
from fabio.config import config_hash
from .common import directional_aggression_ok, is_trade_safe, choose_lvn, lvn_touched, session_allowed
from .target_stop import target_for, stop_for

class InvalidTransition(RuntimeError): pass

class TrendState(str, Enum):
    BALANCED="BALANCED"; VALID_BREAKOUT="VALID_BREAKOUT"; DISPLACEMENT="DISPLACEMENT"; STRUCTURE_BREAK="STRUCTURE_BREAK"; IMPULSE_LOCKED="IMPULSE_LOCKED"; IMPULSE_PROFILE_READY="IMPULSE_PROFILE_READY"; WAIT_LVN_RETEST="WAIT_LVN_RETEST"; LVN_TOUCHED="LVN_TOUCHED"; WAIT_AGGRESSION="WAIT_AGGRESSION"; ENTRY_READY="ENTRY_READY"; ACTIVE_TRADE="ACTIVE_TRADE"; EXIT="EXIT"

class MeanRevState(str, Enum):
    BALANCED="BALANCED"; BREAKOUT_ATTEMPT="BREAKOUT_ATTEMPT"; NO_ACCEPTANCE="NO_ACCEPTANCE"; FAILED_AUCTION="FAILED_AUCTION"; VALUE_RECLAIM="VALUE_RECLAIM"; RECLAIM_LEG_LOCKED="RECLAIM_LEG_LOCKED"; RECLAIM_PROFILE="RECLAIM_PROFILE"; WAIT_LVN_RETEST="WAIT_LVN_RETEST"; SNAPBACK_AGGRESSION="SNAPBACK_AGGRESSION"; ENTRY_READY="ENTRY_READY"; ACTIVE_TRADE="ACTIVE_TRADE"; EXIT="EXIT"

@dataclass
class MachineBase:
    cfg: dict
    history: list[str] = field(default_factory=list)
    direction: Direction = Direction.FLAT
    age: int = 0
    node: VolumeNode | None = None

    def _record(self, state):
        name = state.value if hasattr(state, "value") else str(state)
        if not self.history or self.history[-1] != name:
            self.history.append(name)

    def _expired(self):
        return self.age > self.cfg["strategy"]["setup_expiry_events"]

@dataclass
class TrendMachine(MachineBase):
    state: TrendState = TrendState.BALANCED

    def __post_init__(self): self._record(self.state)

    def force_transition(self, new_state: TrendState):
        allowed = {
            TrendState.BALANCED:{TrendState.VALID_BREAKOUT}, TrendState.VALID_BREAKOUT:{TrendState.DISPLACEMENT},
            TrendState.DISPLACEMENT:{TrendState.STRUCTURE_BREAK}, TrendState.STRUCTURE_BREAK:{TrendState.IMPULSE_LOCKED},
            TrendState.IMPULSE_LOCKED:{TrendState.IMPULSE_PROFILE_READY}, TrendState.IMPULSE_PROFILE_READY:{TrendState.WAIT_LVN_RETEST},
            TrendState.WAIT_LVN_RETEST:{TrendState.LVN_TOUCHED}, TrendState.LVN_TOUCHED:{TrendState.WAIT_AGGRESSION},
            TrendState.WAIT_AGGRESSION:{TrendState.ENTRY_READY}, TrendState.ENTRY_READY:{TrendState.ACTIVE_TRADE}, TrendState.ACTIVE_TRADE:{TrendState.EXIT}, TrendState.EXIT:set()
        }
        if new_state not in allowed[self.state]: raise InvalidTransition(f"{self.state}->{new_state}")
        self.state = new_state; self._record(new_state)

    def update(self, s: MarketFeatureSnapshot) -> TradeCandidate | None:
        self.age += 1
        p=self.cfg["strategy"]; a=s.auction
        if self._expired() and self.state not in {TrendState.BALANCED,TrendState.ACTIVE_TRADE,TrendState.EXIT}:
            self.state=TrendState.BALANCED; self.direction=Direction.FLAT; self.node=None; self.age=0; self._record(self.state)
        if not is_trade_safe(s,self.cfg) or not session_allowed(s,"trend",self.cfg): return None
        if self.state==TrendState.BALANCED:
            if a.out_of_balance and a.breakout_side in {Direction.LONG,Direction.SHORT} and a.breakout_distance_ticks>=p["breakout_distance_ticks"]:
                self.direction=a.breakout_side; self.force_transition(TrendState.VALID_BREAKOUT)
        if self.state==TrendState.VALID_BREAKOUT and a.displacement_zscore>=p["displacement_zscore_min"]:
            self.force_transition(TrendState.DISPLACEMENT)
        if self.state==TrendState.DISPLACEMENT and a.structure_break:
            self.force_transition(TrendState.STRUCTURE_BREAK)
        if self.state==TrendState.STRUCTURE_BREAK and a.impulse_id and a.impulse_ticks>=p["impulse_min_ticks"]:
            self.force_transition(TrendState.IMPULSE_LOCKED)
        if self.state==TrendState.IMPULSE_LOCKED and s.impulse_profile is not None:
            self.node=choose_lvn(s,"impulse_profile",self.direction,self.cfg)
            if self.node: self.force_transition(TrendState.IMPULSE_PROFILE_READY)
        if self.state==TrendState.IMPULSE_PROFILE_READY:
            self.force_transition(TrendState.WAIT_LVN_RETEST)
        if self.state==TrendState.WAIT_LVN_RETEST and a.out_of_balance and lvn_touched(s,self.node):
            self.force_transition(TrendState.LVN_TOUCHED)
        if self.state==TrendState.LVN_TOUCHED:
            self.force_transition(TrendState.WAIT_AGGRESSION)
        if self.state==TrendState.WAIT_AGGRESSION and a.out_of_balance and directional_aggression_ok(s,self.direction,self.cfg):
            self.force_transition(TrendState.ENTRY_READY)
            return self._candidate(s)
        return None

    def _candidate(self,s):
        assert self.node
        stop=stop_for(s,SetupType.TREND,self.direction,self.node,self.cfg); target=target_for(s,SetupType.TREND,self.direction)
        evidence={"state_history":list(self.history),"lvn":self.node.model_dump(mode="json"),"delta_zscore":s.delta_zscore,"cvd_slope":s.cvd_slope,"big_print_percentile":s.big_print_percentile,"absorption_score":s.absorption_score,"out_of_balance":s.auction.out_of_balance}
        d=DecisionSnapshot(timestamp=s.timestamp,instrument=s.instrument,setup_type=SetupType.TREND,direction=self.direction,state=self.state.value,evidence=evidence,parameter_version=self.cfg.get("version","unknown"),config_hash=config_hash(self.cfg),trade_safe=s.trade_safe)
        return TradeCandidate(candidate_id=str(uuid4()),setup_type=SetupType.TREND,direction=self.direction,timestamp=s.timestamp,instrument=s.instrument,entry_reference=s.last_price,stop_price=stop,target_price=target,evidence=evidence,decision_snapshot=d)

@dataclass
class MeanReversionMachine(MachineBase):
    state: MeanRevState = MeanRevState.BALANCED

    def __post_init__(self): self._record(self.state)

    def force_transition(self,new_state:MeanRevState):
        allowed={MeanRevState.BALANCED:{MeanRevState.BREAKOUT_ATTEMPT},MeanRevState.BREAKOUT_ATTEMPT:{MeanRevState.NO_ACCEPTANCE},MeanRevState.NO_ACCEPTANCE:{MeanRevState.FAILED_AUCTION},MeanRevState.FAILED_AUCTION:{MeanRevState.VALUE_RECLAIM},MeanRevState.VALUE_RECLAIM:{MeanRevState.RECLAIM_LEG_LOCKED},MeanRevState.RECLAIM_LEG_LOCKED:{MeanRevState.RECLAIM_PROFILE},MeanRevState.RECLAIM_PROFILE:{MeanRevState.WAIT_LVN_RETEST},MeanRevState.WAIT_LVN_RETEST:{MeanRevState.SNAPBACK_AGGRESSION},MeanRevState.SNAPBACK_AGGRESSION:{MeanRevState.ENTRY_READY},MeanRevState.ENTRY_READY:{MeanRevState.ACTIVE_TRADE},MeanRevState.ACTIVE_TRADE:{MeanRevState.EXIT},MeanRevState.EXIT:set()}
        if new_state not in allowed[self.state]: raise InvalidTransition(f"{self.state}->{new_state}")
        self.state=new_state; self._record(new_state)

    def update(self,s:MarketFeatureSnapshot)->TradeCandidate|None:
        self.age+=1; p=self.cfg["strategy"]; a=s.auction
        if self._expired() and self.state not in {MeanRevState.BALANCED,MeanRevState.ACTIVE_TRADE,MeanRevState.EXIT}:
            self.state=MeanRevState.BALANCED;self.direction=Direction.FLAT;self.node=None;self.age=0;self._record(self.state)
        if not is_trade_safe(s,self.cfg) or not session_allowed(s,"mean_reversion",self.cfg): return None
        if self.state==MeanRevState.BALANCED and a.breakout_side in {Direction.LONG,Direction.SHORT} and a.breakout_distance_ticks>=p["breakout_distance_ticks"]:
            self.direction=Direction.SHORT if a.breakout_side==Direction.LONG else Direction.LONG; self.force_transition(MeanRevState.BREAKOUT_ATTEMPT)
        if self.state==MeanRevState.BREAKOUT_ATTEMPT and a.acceptance_events < p["acceptance_event_count"]:
            self.force_transition(MeanRevState.NO_ACCEPTANCE)
        if self.state==MeanRevState.NO_ACCEPTANCE and a.failed_auction:
            self.force_transition(MeanRevState.FAILED_AUCTION)
        if self.state==MeanRevState.FAILED_AUCTION and a.reclaim_inside_balance:
            self.force_transition(MeanRevState.VALUE_RECLAIM)
        if self.state==MeanRevState.VALUE_RECLAIM and a.impulse_id:
            self.force_transition(MeanRevState.RECLAIM_LEG_LOCKED)
        if self.state==MeanRevState.RECLAIM_LEG_LOCKED and s.reclaim_profile is not None:
            self.node=choose_lvn(s,"reclaim_profile",self.direction,self.cfg)
            if self.node:self.force_transition(MeanRevState.RECLAIM_PROFILE)
        if self.state==MeanRevState.RECLAIM_PROFILE:self.force_transition(MeanRevState.WAIT_LVN_RETEST)
        if self.state==MeanRevState.WAIT_LVN_RETEST and lvn_touched(s,self.node) and s.rejection_speed>=p["rejection_speed_min"]:
            if directional_aggression_ok(s,self.direction,self.cfg):
                self.force_transition(MeanRevState.SNAPBACK_AGGRESSION)
        if self.state==MeanRevState.SNAPBACK_AGGRESSION:
            self.force_transition(MeanRevState.ENTRY_READY); return self._candidate(s)
        return None

    def _candidate(self,s):
        assert self.node
        stop=stop_for(s,SetupType.MEAN_REVERSION,self.direction,self.node,self.cfg);target=target_for(s,SetupType.MEAN_REVERSION,self.direction)
        evidence={"state_history":list(self.history),"lvn":self.node.model_dump(mode="json"),"failed_auction":s.auction.failed_auction,"reclaim":s.auction.reclaim_inside_balance,"delta_zscore":s.delta_zscore,"absorption_score":s.absorption_score}
        d=DecisionSnapshot(timestamp=s.timestamp,instrument=s.instrument,setup_type=SetupType.MEAN_REVERSION,direction=self.direction,state=self.state.value,evidence=evidence,parameter_version=self.cfg.get("version","unknown"),config_hash=config_hash(self.cfg),trade_safe=s.trade_safe)
        return TradeCandidate(candidate_id=str(uuid4()),setup_type=SetupType.MEAN_REVERSION,direction=self.direction,timestamp=s.timestamp,instrument=s.instrument,entry_reference=s.last_price,stop_price=stop,target_price=target,evidence=evidence,decision_snapshot=d)
