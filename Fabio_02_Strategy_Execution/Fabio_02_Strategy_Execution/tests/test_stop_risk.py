from decimal import Decimal
import pytest
from contracts.models import Direction
from fabio.config import load_config
from fabio.fixtures.synthetic import valid_trend_long, stale_data
from fabio.strategy import TrendMachine, validate_stop_update, StopWideningError
from fabio.risk import RiskEngine
from fabio.accounting import INSTRUMENTS

def test_stop_buffer_and_no_widen():
    cfg=load_config();s=valid_trend_long()[0];c=TrendMachine(cfg).update(s);assert c.stop_price < c.entry_reference
    with pytest.raises(StopWideningError):validate_stop_update(Direction.LONG,Decimal('100'),Decimal('99'))
    assert validate_stop_update(Direction.LONG,Decimal('100'),Decimal('101'))==Decimal('101')

def test_position_sizing_and_duplicate_guard():
    cfg=load_config();s=valid_trend_long()[0];c=TrendMachine(cfg).update(s);r=RiskEngine(cfg)
    d=r.evaluate(s,c,INSTRUMENTS['MNQ']['tick_value']);assert d.allowed and d.quantity>=1
    d2=r.evaluate(s,c,INSTRUMENTS['MNQ']['tick_value']);assert not d2.allowed and d2.reason=='duplicate_order_guard'

def test_stale_data_lock():
    cfg=load_config();s=stale_data()[0]
    # candidate built from safe snapshot, then evaluated against stale one
    good=valid_trend_long()[0];c=TrendMachine(cfg).update(good);r=RiskEngine(cfg);d=r.evaluate(s,c,INSTRUMENTS['MNQ']['tick_value'])
    assert not d.allowed and d.reason=='stale_data_lock'

def test_daily_loss_lock():
    cfg=load_config();s=valid_trend_long()[0];c=TrendMachine(cfg).update(s);r=RiskEngine(cfg);r.state.daily_realized=Decimal('-2500')
    d=r.evaluate(s,c,INSTRUMENTS['MNQ']['tick_value']);assert not d.allowed and d.reason=='max_daily_loss'

def test_kill_switch():
    cfg=load_config();s=valid_trend_long()[0];c=TrendMachine(cfg).update(s);r=RiskEngine(cfg);r.set_kill_switch(True)
    assert r.evaluate(s,c,INSTRUMENTS['MNQ']['tick_value']).reason=='kill_switch'
