from copy import deepcopy
import json
import pytest
from fabio.config import load_config, load_search_space
from fabio.fixtures.synthetic import valid_trend_long, valid_mean_reversion_short
from fabio.backtest import BacktestEngine, LookaheadError
from fabio.research import optimize, walk_forward, robustness_matrix, monte_carlo, ablation_report
from fabio.research.locking import freeze_for_oos,verify_oos_lock,assert_optimization_allowed,OOSLockError
from contracts.schema import schema_sha256

def small_space():
    s=load_search_space();return {'parameters':{k:s['parameters'][k] for k in ['aggression_delta_zscore','stop_buffer_ticks']}}

def test_backtest_trade_and_deterministic_replay():
    cfg=load_config();a=BacktestEngine(cfg).run(valid_trend_long());b=BacktestEngine(cfg).run(valid_trend_long())
    assert len(a['ledger'])==1;assert a['ending_equity']==b['ending_equity'];assert a['ledger'][0]['net_pnl']==b['ledger'][0]['net_pnl']

def test_no_lookahead_ordering():
    cfg=load_config();s=valid_trend_long();bad=[s[1],s[0]]
    with pytest.raises(LookaheadError):BacktestEngine(cfg).run(bad)

def test_optimizer_and_plateau():
    r=optimize(load_config(),valid_trend_long,small_space(),'grid',8,1);assert r['selected'] is not None;assert r['plateau'];assert all('metrics' in x for x in r['candidates'])

def test_walk_forward_frozen_validation():
    r=walk_forward(load_config(),[(valid_trend_long,valid_trend_long)],small_space(),4);assert r['aggregate']['windows']==1

def test_robustness_matrix_contains_cost_latency_and_jitter():
    r=robustness_matrix(load_config(),valid_trend_long);assert len(r)>=33;assert any(x['scenario'].get('latency_ms')==250 for x in r)

def test_monte_carlo_determinism():
    a=monte_carlo([100,-50,80,-40],100,7);b=monte_carlo([100,-50,80,-40],100,7);assert a==b;assert a['bootstrap_modes']==['trade_order','block']

def test_ablation_runs():
    r=ablation_report(load_config(),valid_trend_long);assert len(r['ablations'])==7

def test_oos_lock(tmp_path):
    cfg=load_config();p=tmp_path/'lock.json';freeze_for_oos(p,cfg,'test-commit',schema_sha256());assert verify_oos_lock(p,cfg)
    changed=deepcopy(cfg);changed['strategy']['stop_buffer_ticks']=3
    with pytest.raises(OOSLockError):verify_oos_lock(p,changed)
    with pytest.raises(OOSLockError):assert_optimization_allowed('LOCKED_OOS')
