from __future__ import annotations
from fabio.backtest import BacktestEngine
from .metrics import compute_metrics

def ablate_stream(stream,feature):
    out=[]
    for s in stream:
        updates={}
        if feature=='CVD': updates['cvd_slope']=0.0
        elif feature=='Big Prints': updates['big_print_percentile']=0.0
        elif feature=='footprint imbalance': updates.update(stacked_imbalance_ratio=1.0,stacked_levels=0)
        elif feature=='absorption': updates['absorption_score']=0.0
        elif feature=='DOM features': updates['pulling_stacking_score']=0.0
        elif feature=='LVN-quality ranking':
            if s.impulse_profile: updates['impulse_profile']=s.impulse_profile.model_copy(update={'lvn_zones':list(reversed(s.impulse_profile.lvn_zones))})
            if s.reclaim_profile: updates['reclaim_profile']=s.reclaim_profile.model_copy(update={'lvn_zones':list(reversed(s.reclaim_profile.lvn_zones))})
        elif feature=='acceptance filter':
            updates['auction']=s.auction.model_copy(update={'acceptance_events':0})
        out.append(s.model_copy(update=updates))
    return out

def ablation_report(cfg,stream_factory):
    features=['CVD','Big Prints','footprint imbalance','absorption','LVN-quality ranking','acceptance filter','DOM features']
    base=compute_metrics(BacktestEngine(cfg).run(stream_factory())['ledger']);rows=[]
    for f in features:rows.append({'removed':f,'metrics':compute_metrics(BacktestEngine(cfg).run(ablate_stream(stream_factory(),f))['ledger'])})
    return {'baseline':base,'ablations':rows}
