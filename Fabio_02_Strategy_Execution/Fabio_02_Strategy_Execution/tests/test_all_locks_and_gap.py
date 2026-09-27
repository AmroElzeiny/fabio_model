from copy import deepcopy
from decimal import Decimal
from uuid import uuid4
from contracts.models import Direction, ExecutionIntent, OrderType, OrderStatus
from fabio.config import load_config
from fabio.fixtures.synthetic import valid_trend_long
from fabio.strategy import TrendMachine
from fabio.risk import RiskEngine
from fabio.accounting import INSTRUMENTS
from fabio.execution import ExecutionSimulator


def candidate_and_snapshot():
    cfg=load_config();s=valid_trend_long()[0];c=TrendMachine(cfg).update(s);return cfg,s,c

def evaluate_with(mutator):
    cfg,s,c=candidate_and_snapshot();r=RiskEngine(cfg);mutator(r,s,c,cfg);return r.evaluate(s,c,INSTRUMENTS['MNQ']['tick_value'])

def test_execution_error_lock():
    d=evaluate_with(lambda r,s,c,cfg:r.set_execution_error(True));assert not d.allowed and d.reason=='execution_error_lock'

def test_data_quality_lock():
    cfg,s,c=candidate_and_snapshot();bad=s.model_copy(update={'trade_safe':False});r=RiskEngine(cfg);d=r.evaluate(bad,c,INSTRUMENTS['MNQ']['tick_value']);assert d.reason=='data_quality_lock'

def test_weekly_loss_lock():
    def m(r,s,c,cfg):r.state.weekly_realized=Decimal('-5000')
    assert evaluate_with(m).reason=='max_weekly_loss'

def test_loss_streak_lock():
    def m(r,s,c,cfg):r.state.consecutive_losses=cfg['risk']['max_consecutive_losses']
    assert evaluate_with(m).reason=='stop_after_loss_streak'

def test_session_trade_cap():
    def m(r,s,c,cfg):r.state.session_trades=cfg['risk']['session_trade_cap']
    assert evaluate_with(m).reason=='session_trade_cap'

def test_cooldown_lock():
    def m(r,s,c,cfg):r.state.last_trade_event=s.event_index-1
    assert evaluate_with(m).reason=='cooldown'

def test_concurrent_exposure_lock():
    def m(r,s,c,cfg):r.state.open_exposure=Decimal(str(cfg['risk']['max_concurrent_exposure_fraction']))
    assert evaluate_with(m).reason=='max_concurrent_exposure'

def test_max_position_cap():
    cfg,s,c=candidate_and_snapshot();cfg=deepcopy(cfg);cfg['risk']['risk_fraction']=.5;cfg['risk']['max_risk_fraction']=.5;cfg['risk']['max_position_contracts']=3;r=RiskEngine(cfg);d=r.evaluate(s,c,INSTRUMENTS['MNQ']['tick_value']);assert d.allowed and d.quantity==3

def test_gap_rejection():
    cfg,s,c=candidate_and_snapshot();far=s.model_copy(update={'bid':Decimal('120'),'ask':Decimal('120.25'),'last_price':Decimal('120.25')})
    i=ExecutionIntent(intent_id=str(uuid4()),candidate_id='x',timestamp=s.timestamp,instrument='MNQ',direction=Direction.LONG,order_type=OrderType.MARKET,quantity=Decimal('1'),reference_price=Decimal('101'),stop_price=Decimal('100'),target_price=Decimal('105'))
    rep=ExecutionSimulator(cfg).execute(i,far);assert rep.status==OrderStatus.REJECTED and rep.rejected_reason=='gap_exceeds_limit'
