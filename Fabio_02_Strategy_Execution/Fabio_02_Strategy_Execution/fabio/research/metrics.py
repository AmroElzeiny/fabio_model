from __future__ import annotations
import math, statistics
from decimal import Decimal

def _f(x): return float(x) if not isinstance(x,float) else x

def compute_metrics(ledger:list, initial_equity:float=100000.0)->dict:
    pnls=[_f(x['net_pnl'] if isinstance(x,dict) else x.net_pnl) for x in ledger]
    rs=[_f(x['r_multiple'] if isinstance(x,dict) else x.r_multiple) for x in ledger]
    mfes=[_f(x.get('mfe_ticks',0) if isinstance(x,dict) else x.mfe_ticks) for x in ledger]
    maes=[_f(x.get('mae_ticks',0) if isinstance(x,dict) else x.mae_ticks) for x in ledger]
    n=len(pnls); wins=[x for x in pnls if x>0]; losses=[x for x in pnls if x<0]
    net=sum(pnls); expectancy=net/n if n else 0.0
    pf=(sum(wins)/abs(sum(losses))) if losses else (math.inf if wins else 0.0)
    wr=len(wins)/n if n else 0.0
    avg_win=sum(wins)/len(wins) if wins else 0.0; avg_loss=sum(losses)/len(losses) if losses else 0.0
    eq=initial_equity;peak=eq;maxdd=0.0;rets=[];longest=cur=0
    for p in pnls:
        prev=eq;eq+=p; rets.append(p/prev if prev else 0);peak=max(peak,eq);maxdd=max(maxdd,(peak-eq)/peak if peak else 0)
        if p<0:cur+=1;longest=max(longest,cur)
        else:cur=0
    mean=statistics.fmean(rets) if rets else 0; std=statistics.pstdev(rets) if len(rets)>1 else 0
    downside=math.sqrt(statistics.fmean([min(0,r)**2 for r in rets])) if rets else 0
    sharpe=mean/std*math.sqrt(max(n,1)) if std else 0.0; sortino=mean/downside*math.sqrt(max(n,1)) if downside else 0.0
    net_return=net/initial_equity;calmar=net_return/maxdd if maxdd else (math.inf if net_return>0 else 0.0)
    tail=sorted(pnls)[:max(1,math.ceil(n*.05))] if pnls else []
    return {'trades':n,'net_pnl':net,'net_return':net_return,'expectancy':expectancy,'profit_factor':pf,'median_r':statistics.median(rs) if rs else 0.0,'max_drawdown':maxdd,'sharpe':sharpe,'sortino':sortino,'calmar':calmar,'win_rate':wr,'average_win':avg_win,'average_loss':avg_loss,'tail_loss_mean':statistics.fmean(tail) if tail else 0.0,'longest_loss_streak':longest,'average_mfe_ticks':statistics.fmean(mfes) if mfes else 0.0,'average_mae_ticks':statistics.fmean(maes) if maes else 0.0,'trades_per_month':float(n),'monthly_stability':1.0 if n and net>0 else 0.0,'regime_stability':1.0 if n and net>0 else 0.0,'cost_sensitivity':None}
