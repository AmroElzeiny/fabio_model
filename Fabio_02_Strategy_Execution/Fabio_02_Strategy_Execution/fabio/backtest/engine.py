from __future__ import annotations
from dataclasses import asdict
from decimal import Decimal
from uuid import uuid4
from contracts.models import *
from fabio.accounting import Portfolio, Position, INSTRUMENTS
from fabio.execution import ExecutionSimulator, HistoricalReplayExecutionAdapter
from fabio.risk import RiskEngine
from fabio.strategy import TrendMachine, MeanReversionMachine, TrendState, MeanRevState
from fabio.strategy.target_stop import should_early_exit

class LookaheadError(RuntimeError): pass

class BacktestEngine:
    def __init__(self,cfg:dict,initial_equity:Decimal=Decimal('100000')):
        self.cfg=cfg; self.portfolio=Portfolio(initial_equity); self.risk=RiskEngine(cfg,initial_equity)
        self.exec=HistoricalReplayExecutionAdapter(ExecutionSimulator(cfg)); self.trend=TrendMachine(cfg); self.meanrev=MeanReversionMachine(cfg)
        self.event_journal=[]; self.state_log=[]; self.equity_curve=[]; self._last_event=-1; self._last_ts=None
        self.open_meta:dict[int,dict]={}

    def _validate_order(self,s:MarketFeatureSnapshot):
        if s.event_index<=self._last_event: raise LookaheadError('event_index must strictly increase')
        if self._last_ts is not None and s.timestamp<self._last_ts: raise LookaheadError('timestamps must be monotonic')
        self._last_event=s.event_index; self._last_ts=s.timestamp

    def _record_state(self,s):
        self.state_log.append({'event_index':s.event_index,'timestamp':s.timestamp.isoformat(),'trend':self.trend.state.value,'mean_reversion':self.meanrev.state.value})

    def _exit_checks(self,s:MarketFeatureSnapshot):
        for p in list(self.portfolio.positions):
            meta=self.open_meta[id(p)]; direction=p.direction; px=s.last_price
            if direction==Direction.LONG:
                hit_stop=px<=p.stop; hit_target=px>=p.target
                mfe=max(meta['mfe'],(px-p.entry)/s.tick_size); mae=min(meta['mae'],(px-p.entry)/s.tick_size)
            else:
                hit_stop=px>=p.stop; hit_target=px<=p.target
                mfe=max(meta['mfe'],(p.entry-px)/s.tick_size); mae=min(meta['mae'],(p.entry-px)/s.tick_size)
            meta['mfe']=mfe; meta['mae']=mae
            early=should_early_exit(s,direction,self.cfg)
            if not (hit_stop or hit_target or early): continue
            reason=ExitReason.STOP.value if hit_stop else (ExitReason.TARGET.value if hit_target else ExitReason.EARLY_EXIT.value)
            exit_px=p.stop if hit_stop else (p.target if hit_target else px)
            exit_fee=Decimal(str(self.cfg['execution']['commission_per_contract']))*p.quantity
            le=self.portfolio.close(p,exit_px,exit_fee,reason,meta['setup_type'],meta['candidate_id'],meta['config_hash'],Decimal(str(mfe)),Decimal(str(abs(mae))))
            exposure=Decimal(str(meta['risk_exposure'])); self.risk.on_trade_closed(le.net_pnl,exposure); del self.open_meta[id(p)]
            if meta['setup_type']==SetupType.TREND.value and self.trend.state==TrendState.ACTIVE_TRADE:self.trend.force_transition(TrendState.EXIT)
            if meta['setup_type']==SetupType.MEAN_REVERSION.value and self.meanrev.state==MeanRevState.ACTIVE_TRADE:self.meanrev.force_transition(MeanRevState.EXIT)
            self.event_journal.append({'event_index':s.event_index,'type':'EXIT','reason':reason,'price':str(exit_px),'pnl':str(le.net_pnl),'candidate_id':meta['candidate_id']})

    def _process_candidate(self,s,candidate,machine):
        tick_value=INSTRUMENTS.get(s.instrument,INSTRUMENTS['MNQ'])['tick_value']
        rd=self.risk.evaluate(s,candidate,tick_value)
        self.event_journal.append({'event_index':s.event_index,'type':'RISK','candidate_id':candidate.candidate_id,'allowed':rd.allowed,'reason':rd.reason})
        if not rd.allowed:return
        intent=ExecutionIntent(intent_id=str(uuid4()),candidate_id=candidate.candidate_id,timestamp=s.timestamp,instrument=s.instrument,direction=candidate.direction,order_type=OrderType.MARKET,quantity=rd.quantity,reference_price=candidate.entry_reference,stop_price=candidate.stop_price,target_price=candidate.target_price)
        rep=self.exec.submit(intent,s)
        self.event_journal.append({'event_index':s.event_index,'type':'EXECUTION','candidate_id':candidate.candidate_id,'status':rep.status.value,'filled':str(rep.filled_quantity),'avg':str(rep.average_fill_price) if rep.average_fill_price else None})
        if rep.status not in {OrderStatus.FILLED,OrderStatus.PARTIAL} or rep.filled_quantity<=0:return
        fees=sum((f.fee for f in rep.fills),Decimal('0')); p=Position(s.instrument,candidate.direction,rep.filled_quantity,rep.average_fill_price,candidate.stop_price,candidate.target_price,fees)
        self.portfolio.open(p); risk_cash=abs((p.entry-p.stop)/s.tick_size)*tick_value*p.quantity
        exposure=risk_cash/self.risk.state.equity if self.risk.state.equity else Decimal('0')
        self.open_meta[id(p)]={'setup_type':candidate.setup_type.value,'candidate_id':candidate.candidate_id,'config_hash':candidate.decision_snapshot.config_hash,'mfe':Decimal('0'),'mae':Decimal('0'),'risk_exposure':exposure,'evidence':candidate.evidence}
        if candidate.setup_type==SetupType.TREND: machine.force_transition(TrendState.ACTIVE_TRADE)
        else: machine.force_transition(MeanRevState.ACTIVE_TRADE)

    def run(self,stream:list[MarketFeatureSnapshot]):
        for s in stream:
            self._validate_order(s); self._exit_checks(s)
            if not self.portfolio.positions:
                c=self.trend.update(s)
                if c:self._process_candidate(s,c,self.trend)
                if not self.portfolio.positions:
                    c=self.meanrev.update(s)
                    if c:self._process_candidate(s,c,self.meanrev)
            self.portfolio.mark({s.instrument:s.last_price})
            self.equity_curve.append({'event_index':s.event_index,'timestamp':s.timestamp.isoformat(),'equity':str(self.portfolio.equity),'drawdown':str((self.portfolio.peak-self.portfolio.equity)/self.portfolio.peak if self.portfolio.peak else 0)})
            self._record_state(s)
        return self.result()

    def result(self):
        return {'ledger':[asdict(x) for x in self.portfolio.ledger],'events':self.event_journal,'equity_curve':self.equity_curve,'state_transitions':self.state_log,'ending_equity':self.portfolio.equity,'max_drawdown':self.portfolio.max_drawdown}
