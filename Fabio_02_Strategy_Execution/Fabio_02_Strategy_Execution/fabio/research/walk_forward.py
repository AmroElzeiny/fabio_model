from __future__ import annotations
from copy import deepcopy
from fabio.backtest import BacktestEngine
from fabio.config import apply_strategy_params
from .metrics import compute_metrics
from .optimize import optimize

def walk_forward(base_cfg,windows,search_space,max_candidates=6):
    reports=[]
    for idx,(train_factory,val_factory) in enumerate(windows):
        opt=optimize(base_cfg,train_factory,search_space,'random',max_candidates,min_trades=1,seed=42+idx)
        selected=opt['selected'];cfg=base_cfg if not selected else apply_strategy_params(base_cfg,selected['params'])
        val=BacktestEngine(cfg).run(val_factory());reports.append({'window':idx,'selected_params':selected['params'] if selected else {},'train_metrics':selected['metrics'] if selected else {},'validation_metrics':compute_metrics(val['ledger'])})
    agg={'windows':len(reports),'validation_trades':sum(r['validation_metrics']['trades'] for r in reports),'validation_net_pnl':sum(r['validation_metrics']['net_pnl'] for r in reports)}
    return {'windows':reports,'aggregate':agg}
