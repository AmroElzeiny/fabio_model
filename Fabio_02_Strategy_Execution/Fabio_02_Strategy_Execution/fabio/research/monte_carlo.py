from __future__ import annotations
import random, statistics, math

def _percentile(xs,p):
    if not xs:return 0.0
    xs=sorted(xs);i=(len(xs)-1)*p;lo=math.floor(i);hi=math.ceil(i)
    return xs[lo] if lo==hi else xs[lo]*(hi-i)+xs[hi]*(i-lo)

def monte_carlo(pnls:list[float],iterations=500,seed=42,block_size=3,initial_equity=100000.0):
    rng=random.Random(seed);dds=[];streaks=[];terms=[];recoveries=[]
    if not pnls:return {'seed':seed,'iterations':iterations,'dd_p50':0,'dd_p95':0,'longest_loss_streak_p95':0,'terminal_equity_p05':initial_equity,'risk_of_ruin_fraction':0,'recovery_time_p95':0}
    for _ in range(iterations):
        # alternate ordinary and block bootstrap to cover both requested modes.
        if _%2==0: seq=[rng.choice(pnls) for _ in pnls]
        else:
            seq=[]
            while len(seq)<len(pnls):
                start=rng.randrange(len(pnls));seq.extend([pnls[(start+j)%len(pnls)] for j in range(block_size)])
            seq=seq[:len(pnls)]
        eq=peak=initial_equity;maxdd=0;cur=ls=0;last_peak_idx=0;max_recovery=0
        for i,p in enumerate(seq):
            eq+=p
            if eq>=peak:peak=eq;last_peak_idx=i
            else:max_recovery=max(max_recovery,i-last_peak_idx)
            maxdd=max(maxdd,(peak-eq)/peak if peak else 1)
            if p<0:cur+=1;ls=max(ls,cur)
            else:cur=0
        dds.append(maxdd);streaks.append(ls);terms.append(eq);recoveries.append(max_recovery)
    return {'seed':seed,'iterations':iterations,'bootstrap_modes':['trade_order','block'],'dd_p50':_percentile(dds,.5),'dd_p95':_percentile(dds,.95),'longest_loss_streak_p95':_percentile(streaks,.95),'terminal_equity_p05':_percentile(terms,.05),'risk_of_ruin_fraction':sum(x<=0 for x in terms)/iterations,'recovery_time_p95':_percentile(recoveries,.95)}
