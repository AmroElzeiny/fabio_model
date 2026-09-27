import pytest
from contracts.models import Direction, SetupType
from fabio.config import load_config
from fabio.fixtures.synthetic import *
from fabio.strategy import TrendMachine, MeanReversionMachine, TrendState, MeanRevState, InvalidTransition

def test_trend_long_all_transitions_and_candidate():
    m=TrendMachine(load_config());c=m.update(valid_trend_long()[0]);assert c is not None;assert c.direction==Direction.LONG;assert c.setup_type==SetupType.TREND
    assert m.history==[x.value for x in [TrendState.BALANCED,TrendState.VALID_BREAKOUT,TrendState.DISPLACEMENT,TrendState.STRUCTURE_BREAK,TrendState.IMPULSE_LOCKED,TrendState.IMPULSE_PROFILE_READY,TrendState.WAIT_LVN_RETEST,TrendState.LVN_TOUCHED,TrendState.WAIT_AGGRESSION,TrendState.ENTRY_READY]]

def test_trend_short_candidate():
    m=TrendMachine(load_config());c=m.update(valid_trend_short()[0]);assert c and c.direction==Direction.SHORT

def test_meanrev_long_all_transitions():
    m=MeanReversionMachine(load_config());c=m.update(valid_mean_reversion_long()[0]);assert c and c.direction==Direction.LONG
    assert m.history[-1]==MeanRevState.ENTRY_READY.value

def test_meanrev_short_candidate():
    m=MeanReversionMachine(load_config());c=m.update(valid_mean_reversion_short()[0]);assert c and c.direction==Direction.SHORT

def test_invalid_transition_rejected():
    with pytest.raises(InvalidTransition):TrendMachine(load_config()).force_transition(TrendState.DISPLACEMENT)

def test_no_entry_without_aggression():
    m=TrendMachine(load_config());assert m.update(lvn_touched_without_aggression()[0]) is None;assert m.state==TrendState.WAIT_AGGRESSION

def test_no_entry_unsafe_data():
    m=TrendMachine(load_config());assert m.update(bad_data()[0]) is None;assert m.state==TrendState.BALANCED

def test_absorption_blocks_trade():
    m=TrendMachine(load_config());assert m.update(aggression_blocked_by_absorption()[0]) is None

def test_targets():
    t=TrendMachine(load_config()).update(valid_trend_long()[0]);assert t.target_price==valid_trend_long()[0].volume_profile.poc
    r=MeanReversionMachine(load_config()).update(valid_mean_reversion_short()[0]);assert r.target_price==valid_mean_reversion_short()[0].auction.balance_poc

def test_session_windows_enforced():
    cfg=load_config();s=valid_trend_long()[0].model_copy(update={'session':'LONDON'});m=TrendMachine(cfg);assert m.update(s) is None and m.state==TrendState.BALANCED
