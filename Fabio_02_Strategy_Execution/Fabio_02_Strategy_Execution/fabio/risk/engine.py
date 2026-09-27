from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from contracts.models import Direction, MarketFeatureSnapshot, TradeCandidate

@dataclass
class RiskState:
    equity: Decimal
    day_start_equity: Decimal
    week_start_equity: Decimal
    daily_realized: Decimal = Decimal('0')
    weekly_realized: Decimal = Decimal('0')
    consecutive_losses: int = 0
    open_exposure: Decimal = Decimal('0')
    session_trades: int = 0
    last_trade_event: int = -10_000
    kill_switch: bool = False
    execution_error_lock: bool = False

@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    quantity: Decimal = Decimal('0')
    risk_amount: Decimal = Decimal('0')

class RiskEngine:
    def __init__(self, cfg: dict, initial_equity: Decimal = Decimal('100000')):
        self.cfg=cfg
        self.state=RiskState(initial_equity,initial_equity,initial_equity)
        self.seen_candidates:set[str]=set()

    def reset_day(self):
        self.state.day_start_equity=self.state.equity; self.state.daily_realized=Decimal('0'); self.state.session_trades=0
    def reset_week(self):
        self.state.week_start_equity=self.state.equity; self.state.weekly_realized=Decimal('0')
    def set_kill_switch(self,on:bool=True): self.state.kill_switch=on
    def set_execution_error(self,on:bool=True): self.state.execution_error_lock=on

    def _locks(self,s:MarketFeatureSnapshot,c:TradeCandidate)->str|None:
        r=self.cfg['risk']; st=self.state
        if st.kill_switch:return 'kill_switch'
        if st.execution_error_lock:return 'execution_error_lock'
        if not s.trade_safe:return 'data_quality_lock'
        if s.stale_ms>self.cfg['strategy']['stale_data_ms_max']:return 'stale_data_lock'
        if c.candidate_id in self.seen_candidates:return 'duplicate_order_guard'
        if -st.daily_realized >= st.day_start_equity*Decimal(str(r['max_daily_loss_fraction'])):return 'max_daily_loss'
        if r.get('weekly_loss_lock_enabled',True) and -st.weekly_realized >= st.week_start_equity*Decimal(str(r['max_weekly_loss_fraction'])):return 'max_weekly_loss'
        if st.consecutive_losses>=r['max_consecutive_losses']:return 'stop_after_loss_streak'
        if st.session_trades>=r['session_trade_cap']:return 'session_trade_cap'
        if s.event_index-st.last_trade_event<r['cooldown_events']:return 'cooldown'
        return None

    def evaluate(self,s:MarketFeatureSnapshot,c:TradeCandidate,tick_value:Decimal)->RiskDecision:
        reason=self._locks(s,c)
        if reason:return RiskDecision(False,reason)
        risk_frac=min(Decimal(str(self.cfg['risk']['risk_fraction'])),Decimal(str(self.cfg['risk']['max_risk_fraction'])))
        risk_amount=self.state.equity*risk_frac
        stop_ticks=abs((c.entry_reference-c.stop_price)/s.tick_size)
        if stop_ticks<=0:return RiskDecision(False,'invalid_stop')
        per_contract=stop_ticks*tick_value
        qty=(risk_amount/per_contract).to_integral_value(rounding=ROUND_DOWN)
        qty=max(Decimal('0'),min(qty,Decimal(str(self.cfg['risk']['max_position_contracts']))))
        if qty<1:return RiskDecision(False,'insufficient_equity_for_min_contract')
        exposure=(per_contract*qty)/self.state.equity
        if Decimal(str(exposure))+self.state.open_exposure>Decimal(str(self.cfg['risk']['max_concurrent_exposure_fraction'])):
            return RiskDecision(False,'max_concurrent_exposure')
        self.seen_candidates.add(c.candidate_id); self.state.session_trades+=1; self.state.last_trade_event=s.event_index; self.state.open_exposure+=Decimal(str(exposure))
        return RiskDecision(True,'allowed',qty,risk_amount)

    def on_trade_closed(self,pnl:Decimal, released_exposure:Decimal=Decimal('0')):
        st=self.state; st.equity+=pnl; st.daily_realized+=pnl; st.weekly_realized+=pnl; st.open_exposure=max(Decimal('0'),st.open_exposure-released_exposure)
        st.consecutive_losses = st.consecutive_losses+1 if pnl<0 else 0
