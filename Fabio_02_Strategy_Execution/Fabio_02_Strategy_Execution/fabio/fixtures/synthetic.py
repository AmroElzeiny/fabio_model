from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from contracts.models import *

D=Decimal
BASE_TIME=datetime(2026,1,5,14,30,tzinfo=timezone.utc)

def node(low,high,rv=0.30,retests=0,age=2):return VolumeNode(low=D(str(low)),high=D(str(high)),relative_volume=rv,retests=retests,age_events=age)
def profile(ts,poc,vah,val,lvn):return VolumeProfileSnapshot(timestamp=ts,poc=D(str(poc)),vah=D(str(vah)),val=D(str(val)),lvn_zones=[lvn],hvn_zones=[])

def snapshot(i:int, price:float=101.0, direction:Direction=Direction.LONG, setup:str='trend', safe=True, stale=0, absorption=.1, aggression=True, instrument='MNQ'):
    ts=BASE_TIME+timedelta(seconds=i)
    tick=D('0.25'); px=D(str(price)); bid=px-D('0.25'); ask=px+D('0.25')
    if setup=='trend':
        long=direction==Direction.LONG
        bal_low=D('95') if long else D('105'); bal_high=D('100') if long else D('110'); bal_poc=D('97.5') if long else D('107.5')
        target=D('105') if long else D('100'); l=node('100.75','101.25') if long else node('103.75','104.25')
        a=AuctionFeatureSnapshot(balanced=False,out_of_balance=True,balance_low=bal_low,balance_high=bal_high,balance_poc=bal_poc,value_overlap=.2,poc_migration_ticks=6,directional_efficiency=.8,breakout_side=direction,breakout_distance_ticks=6,acceptance_events=4,displacement_zscore=1.8,structure_break=True,impulse_id='impulse-1',impulse_low=D('99') if long else D('104'),impulse_high=D('102') if long else D('111'),impulse_ticks=12,reclaim_inside_balance=False,failed_auction=False)
        vp=profile(ts,target,target+D('1'),target-D('1'),node(str(target-D('.25')),str(target+D('.25')))); ip=profile(ts,D('101') if long else D('104'),D('102'),D('100'),l); rp=None
        dz=1.6 if long else -1.6; cvd=.5 if long else -.5
    else:
        breakout=Direction.SHORT if direction==Direction.LONG else Direction.LONG
        bal_low=D('100');bal_high=D('110');bal_poc=D('105');
        l=node('101.75','102.25') if direction==Direction.LONG else node('107.75','108.25')
        a=AuctionFeatureSnapshot(balanced=True,out_of_balance=False,balance_low=bal_low,balance_high=bal_high,balance_poc=bal_poc,value_overlap=.8,poc_migration_ticks=1,directional_efficiency=.2,breakout_side=breakout,breakout_distance_ticks=5,acceptance_events=0,displacement_zscore=1.1,structure_break=False,impulse_id='reclaim-1',impulse_low=D('99') if direction==Direction.LONG else D('106'),impulse_high=D('104') if direction==Direction.LONG else D('111'),impulse_ticks=10,reclaim_inside_balance=True,failed_auction=True)
        vp=profile(ts,D('105'),D('110'),D('100'),node('104.75','105.25')); rp=profile(ts,D('102') if direction==Direction.LONG else D('108'),D('104'),D('101'),l); ip=None
        dz=1.6 if direction==Direction.LONG else -1.6;cvd=.5 if direction==Direction.LONG else -.5
    da=direction if aggression else Direction.FLAT
    fp=FootprintSnapshot(timestamp=ts,stacked_buy_levels=3 if direction==Direction.LONG else 0,stacked_sell_levels=3 if direction==Direction.SHORT else 0,levels=[])
    book=BookSnapshot(timestamp=ts,bids=[BookLevel(price=bid,size=D('10'))],asks=[BookLevel(price=ask,size=D('10'))],mbo_available=False)
    return MarketFeatureSnapshot(timestamp=ts,instrument=instrument,session='NEW_YORK' if setup=='trend' else 'LONDON',event_index=i,last_price=px,bid=bid,ask=ask,tick_size=tick,structure_direction=direction,auction=a,volume_profile=vp,impulse_profile=ip,reclaim_profile=rp,delta_zscore=dz,cvd_slope=cvd,big_print_percentile=.95,stacked_imbalance_ratio=3.0,stacked_levels=3,absorption_score=absorption,rejection_speed=.8,pulling_stacking_score=.7,directional_aggression=da,footprint=fp,book=book,data_quality_score=1.0 if safe else .2,trade_safe=safe,stale_ms=stale,market_regime='TREND' if setup=='trend' else 'BALANCE')

def valid_trend_long():
    return [snapshot(1,101.0,Direction.LONG,'trend'), snapshot(2,105.0,Direction.LONG,'trend')]
def valid_trend_short():
    return [snapshot(1,104.0,Direction.SHORT,'trend'), snapshot(2,100.0,Direction.SHORT,'trend')]
def failed_trend_no_aggression():return [snapshot(1,101.0,Direction.LONG,'trend',aggression=False)]
def valid_mean_reversion_long():return [snapshot(1,102.0,Direction.LONG,'mean'), snapshot(2,105.0,Direction.LONG,'mean')]
def valid_mean_reversion_short():return [snapshot(1,108.0,Direction.SHORT,'mean'), snapshot(2,105.0,Direction.SHORT,'mean')]
def lvn_touched_without_aggression():return [snapshot(1,101.0,Direction.LONG,'trend',aggression=False)]
def aggression_blocked_by_absorption():return [snapshot(1,101.0,Direction.LONG,'trend',absorption=.95)]
def bad_data():return [snapshot(1,101.0,Direction.LONG,'trend',safe=False)]
def stale_data():return [snapshot(1,101.0,Direction.LONG,'trend',stale=10000)]
def slippage_partial_fill():
    s=snapshot(1,101.0,Direction.LONG,'trend')
    return s.model_copy(update={'book':BookSnapshot(timestamp=s.timestamp,bids=[BookLevel(price=s.bid,size=D('1'))],asks=[BookLevel(price=s.ask,size=D('1'))],mbo_available=True)})
def combined_demo():
    # two independent sessions need monotonic IDs; second setup shifted to fresh engine use in CLI demos.
    return valid_trend_long()


def risk_lock():
    """A valid setup intended to be paired with a RiskEngine whose kill/loss lock is active."""
    return valid_trend_long()[0]

def failed_auction_fixture():
    return valid_mean_reversion_short()[0]
