from __future__ import annotations
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import random
from fabio.backtest import BacktestEngine
from .metrics import compute_metrics

def _mutate_stream(stream,jitter=0.0,level_error_ticks=0,miss_prob=0.0,seed=42,session=None,volatility=None,regime=None,news=None,instrument=None,time_shift_days=0):
    rng=random.Random(seed);out=[]
    for s in stream:
        if rng.random()<miss_prob:continue
        upd={}
        if level_error_ticks:
            shift=s.tick_size*Decimal(str(rng.choice([-level_error_ticks,level_error_ticks])));upd['last_price']=s.last_price+shift;upd['bid']=s.bid+shift;upd['ask']=s.ask+shift
        if jitter:
            a=s.auction.model_copy(update={'displacement_zscore':s.auction.displacement_zscore*(1+rng.uniform(-jitter,jitter))});upd['auction']=a
        if session is not None: upd['session']=session
        if volatility is not None: upd['volatility_regime']=volatility
        if regime is not None: upd['market_regime']=regime
        if news is not None: upd['news_day']=news
        if instrument is not None: upd['instrument']=instrument
        if time_shift_days: upd['timestamp']=s.timestamp+timedelta(days=time_shift_days)
        out.append(s.model_copy(update=upd))
    return [s.model_copy(update={'event_index':i+1}) for i,s in enumerate(out)]

def _run(cfg,stream):
    res=BacktestEngine(cfg).run(stream);return compute_metrics(res['ledger'])

def robustness_matrix(base_cfg,stream_factory):
    scenarios=[]
    # Transaction-cost and latency grid.
    for fee_mult in [1,2]:
      for slip_mult in [1,2,3]:
       for latency in [0,10,50,100,250]:
        cfg=deepcopy(base_cfg);cfg['execution']['commission_per_contract']*=fee_mult;cfg['execution']['default_slippage_ticks']*=slip_mult;cfg['execution']['latency_ms']=latency
        scenarios.append({'scenario':{'fees_x':fee_mult,'slippage_x':slip_mult,'latency_ms':latency},'metrics':_run(cfg,stream_factory())})
    # Data/rule perturbations.
    perturbations=[
        ('threshold_jitter',{'jitter':.05}),('level_error_plusminus_1_tick',{'level_error_ticks':1}),('missed_events_10pct',{'miss_prob':.10}),
        ('session_NEW_YORK',{'session':'NEW_YORK'}),('session_LONDON',{'session':'LONDON'}),
        ('volatility_LOW',{'volatility':'LOW'}),('volatility_HIGH',{'volatility':'HIGH'}),
        ('regime_TREND',{'regime':'TREND'}),('regime_BALANCE',{'regime':'BALANCE'}),
        ('news_day_true',{'news':True}),('news_day_false',{'news':False}),
        ('roll_date_minus_1d',{'time_shift_days':-1}),('roll_date_plus_1d',{'time_shift_days':1}),
        ('instrument_NQ',{'instrument':'NQ'}),('instrument_MNQ',{'instrument':'MNQ'}),
    ]
    for name,kwargs in perturbations:
        scenarios.append({'scenario':{name:kwargs},'metrics':_run(base_cfg,_mutate_stream(stream_factory(),**kwargs))})
    return scenarios
