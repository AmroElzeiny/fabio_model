from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path
from decimal import Decimal
from contracts.schema import schema_sha256, export_schema
from fabio.config import load_config, load_search_space, config_hash
from fabio.backtest import BacktestEngine
from fabio.fixtures.synthetic import valid_trend_long, valid_mean_reversion_short
from fabio.research import optimize, walk_forward, robustness_matrix, monte_carlo, ablation_report, freeze_for_oos
from fabio.reporting import export_json, export_ledger_csv

ROOT=Path(__file__).resolve().parents[1]

def _small_space():
    s=load_search_space();keys=['aggression_delta_zscore','stop_buffer_ticks'];return {'parameters':{k:s['parameters'][k] for k in keys}}

def main(argv=None):
    p=argparse.ArgumentParser(prog='fabio');sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('schema-hash'); sub.add_parser('demo-backtest');sub.add_parser('demo-optimize');sub.add_parser('demo-wfo');sub.add_parser('demo-robustness');sub.add_parser('demo-monte-carlo');sub.add_parser('demo-ablation');sub.add_parser('self-check')
    a=p.parse_args(argv);cfg=load_config()
    if a.cmd=='schema-hash':
        print(schema_sha256());return 0
    if a.cmd=='demo-backtest':
        r=BacktestEngine(cfg).run(valid_trend_long());print(json.dumps({'ending_equity':str(r['ending_equity']),'trades':len(r['ledger']),'config_hash':config_hash(cfg)},indent=2));return 0
    if a.cmd=='demo-optimize':
        r=optimize(cfg,valid_trend_long,_small_space(),'grid',12,1);print(json.dumps(r,indent=2,default=str));return 0
    if a.cmd=='demo-wfo':
        r=walk_forward(cfg,[(valid_trend_long,valid_trend_long),(valid_mean_reversion_short,valid_mean_reversion_short)],_small_space(),4);print(json.dumps(r,indent=2,default=str));return 0
    if a.cmd=='demo-robustness':
        r=robustness_matrix(cfg,valid_trend_long);print(json.dumps({'scenarios':len(r),'first':r[0]},indent=2,default=str));return 0
    if a.cmd=='demo-monte-carlo':
        bt=BacktestEngine(cfg).run(valid_trend_long());pnls=[float(x['net_pnl']) for x in bt['ledger']];print(json.dumps(monte_carlo(pnls,100,42),indent=2));return 0
    if a.cmd=='demo-ablation':
        print(json.dumps(ablation_report(cfg,valid_trend_long),indent=2,default=str));return 0
    if a.cmd=='self-check':
        out=ROOT/'sample_reports';out.mkdir(exist_ok=True);export_schema(out/'contract_schema.json')
        bt=BacktestEngine(cfg).run(valid_trend_long());export_json(out/'synthetic_backtest.json',bt);export_ledger_csv(out/'trade_ledger.csv',bt['ledger'])
        opt=optimize(cfg,valid_trend_long,_small_space(),'grid',12,1);export_json(out/'optimization.json',opt)
        wfo=walk_forward(cfg,[(valid_trend_long,valid_trend_long),(valid_mean_reversion_short,valid_mean_reversion_short)],_small_space(),4);export_json(out/'walk_forward.json',wfo)
        rob=robustness_matrix(cfg,valid_trend_long);export_json(out/'robustness.json',rob)
        pnls=[float(x['net_pnl']) for x in bt['ledger']];mc=monte_carlo(pnls,200,42);export_json(out/'monte_carlo.json',mc)
        export_json(out/'ablation.json',ablation_report(cfg,valid_trend_long))
        freeze_for_oos(out/'oos_lock_manifest.json',cfg,'standalone-package-build',schema_sha256())
        export_json(out/'self_check.json',{'schema_sha256':schema_sha256(),'config_hash':config_hash(cfg),'ai_enabled':cfg['AI_ENABLED'],'backtest_trades':len(bt['ledger']),'optimization_candidates':len(opt['candidates']),'walk_forward_windows':wfo['aggregate']['windows'],'robustness_scenarios':len(rob),'monte_carlo_iterations':mc['iterations']})
        print(json.dumps(json.loads((out/'self_check.json').read_text()),indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
