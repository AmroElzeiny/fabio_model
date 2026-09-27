from __future__ import annotations
import itertools, random
from copy import deepcopy
from fabio.backtest import BacktestEngine
from fabio.config import apply_strategy_params, load_search_space
from .metrics import compute_metrics

DEFAULT_WEIGHTS={'expectancy':0.30,'profit_factor':0.20,'sharpe':0.15,'calmar':0.15,'max_drawdown':-0.20}

def explicit_score(m:dict,weights=DEFAULT_WEIGHTS)->float:
    # Transparent, bounded transformation; all raw metrics are also retained.
    import math
    def cap(v,lo,hi):return max(lo,min(hi,float(v)))
    vals={'expectancy':math.tanh(float(m['expectancy'])/100.0),'profit_factor':math.tanh(cap(m['profit_factor'],0,10)/3),'sharpe':math.tanh(float(m['sharpe'])/3),'calmar':math.tanh(cap(m['calmar'], -10,10)/3),'max_drawdown':cap(m['max_drawdown'],0,1)}
    return sum(weights[k]*vals[k] for k in weights)

def _values(spec):
    vals=[];x=spec['min'];step=spec['step'];mx=spec['max']
    while x<=mx+1e-12:
        vals.append(round(x,10));x+=step
    return vals

def generate_candidates(space:dict, mode='grid', max_candidates=20, seed=42):
    items=list(space['parameters'].items())
    # Smoke/default optimization only explores first 2 dimensions unless caller supplies reduced space.
    lists=[(k,_values(v)) for k,v in items]
    combos=[dict(zip([k for k,_ in lists],vals)) for vals in itertools.product(*[v for _,v in lists])] if len(lists)<=4 else []
    if mode=='random' or not combos:
        rng=random.Random(seed); out=[]
        for _ in range(max_candidates):out.append({k:rng.choice(vals) for k,vals in lists})
        return out
    return combos[:max_candidates]

def optimize(base_cfg:dict,stream_factory,search_space:dict|None=None,mode='random',max_candidates=12,min_trades=1,seed=42):
    space=search_space or load_search_space(); rows=[]
    for params in generate_candidates(space,mode,max_candidates,seed):
        cfg=apply_strategy_params(base_cfg,params);res=BacktestEngine(cfg).run(stream_factory());m=compute_metrics(res['ledger']);eligible=m['trades']>=min_trades
        rows.append({'params':params,'metrics':m,'eligible':eligible,'score':explicit_score(m) if eligible else float('-inf')})
    eligible=[r for r in rows if r['eligible']];eligible.sort(key=lambda r:(r['score'],-r['metrics']['max_drawdown']),reverse=True)
    selected=eligible[0] if eligible else None
    # plateau: candidates within 5% score of best.
    plateau=[]
    if selected:
        floor=selected['score']-max(abs(selected['score'])*.05,.01);plateau=[r for r in eligible if r['score']>=floor]
    return {'selection_method':'explicit multi-metric weighted score; raw metrics retained','weights':DEFAULT_WEIGHTS,'selected':selected,'plateau':plateau,'candidates':rows}
