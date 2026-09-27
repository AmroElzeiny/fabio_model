from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from contracts.models import *

@dataclass(frozen=True)
class ExecutionSettings:
    latency_ms:int=10
    default_slippage_ticks:int=1
    commission_per_contract:Decimal=Decimal('2.10')
    fee_per_contract:Decimal=Decimal('0')
    queue_fill_fraction:Decimal=Decimal('0.5')
    max_gap_ticks:int=20
    allow_partial_fills:bool=True

class ExecutionSimulator:
    """Deterministic single-event fill simulator.

    Market/marketable-limit orders consume visible depth. Passive limits can only fill
    when the current snapshot also shows opposite-side aggression at the touch; their
    fill capacity is queue-approximated, and MBO snapshots subtract explicit queue-ahead.
    """
    def __init__(self,cfg:dict):
        e=cfg['execution']; self.settings=ExecutionSettings(
            latency_ms=int(e['latency_ms']),default_slippage_ticks=int(e['default_slippage_ticks']),commission_per_contract=Decimal(str(e['commission_per_contract'])),fee_per_contract=Decimal(str(e['fee_per_contract'])),queue_fill_fraction=Decimal(str(e['queue_fill_fraction'])),max_gap_ticks=int(e['max_gap_ticks']),allow_partial_fills=bool(e['allow_partial_fills']))

    def _report(self,intent,status,fills=None,reason=None):
        fills=fills or [];filled=sum((f.quantity for f in fills),Decimal('0'))
        avg=(sum((f.price*f.quantity for f in fills),Decimal('0'))/filled) if filled else None
        return ExecutionReport(intent_id=intent.intent_id,status=status,fills=fills,rejected_reason=reason,latency_ms=self.settings.latency_ms,total_quantity=intent.quantity,filled_quantity=filled,average_fill_price=avg)

    def _passive_limit_fill(self,intent:ExecutionIntent,s:MarketFeatureSnapshot):
        if s.book is None:return self._report(intent,OrderStatus.NEW)
        long=intent.direction==Direction.LONG
        at_touch=(intent.limit_price==s.bid if long else intent.limit_price==s.ask)
        incoming=(s.directional_aggression==Direction.SHORT if long else s.directional_aggression==Direction.LONG)
        if not (at_touch and incoming):return self._report(intent,OrderStatus.NEW)
        levels=s.book.bids if long else s.book.asks
        level=next((lv for lv in levels if lv.price==intent.limit_price),None)
        if level is None:return self._report(intent,OrderStatus.NEW)
        if s.book.mbo_available:
            capacity=max(Decimal('0'),level.size-level.queue_ahead)
        else:
            capacity=max(Decimal('0'),level.size*self.settings.queue_fill_fraction)
        filled=min(intent.quantity,capacity)
        if filled<=0:return self._report(intent,OrderStatus.NEW)
        if filled<intent.quantity and not self.settings.allow_partial_fills:
            return self._report(intent,OrderStatus.NEW)
        fee=(self.settings.commission_per_contract+self.settings.fee_per_contract)*filled
        f=Fill(timestamp=s.timestamp+timedelta(milliseconds=self.settings.latency_ms),price=intent.limit_price,quantity=filled,fee=fee,slippage_ticks=Decimal('0'))
        return self._report(intent,OrderStatus.FILLED if filled==intent.quantity else OrderStatus.PARTIAL,[f])

    def execute(self,intent:ExecutionIntent,s:MarketFeatureSnapshot)->ExecutionReport:
        q=intent.quantity
        if q<=0:return self._report(intent,OrderStatus.REJECTED,reason='quantity<=0')
        if not s.trade_safe:return self._report(intent,OrderStatus.REJECTED,reason='unsafe_data')
        long=intent.direction==Direction.LONG;side_mult=Decimal('1') if long else Decimal('-1')
        touch=s.ask if long else s.bid
        if intent.reference_price is not None:
            gap_ticks=abs((touch-intent.reference_price)/s.tick_size)
            if gap_ticks>self.settings.max_gap_ticks:return self._report(intent,OrderStatus.REJECTED,reason='gap_exceeds_limit')
        marketable=True
        if intent.order_type==OrderType.LIMIT:
            if intent.limit_price is None:return self._report(intent,OrderStatus.REJECTED,reason='missing_limit_price')
            marketable = intent.limit_price>=s.ask if long else intent.limit_price<=s.bid
            if not marketable:return self._passive_limit_fill(intent,s)

        slip=Decimal(str(min(self.settings.default_slippage_ticks,intent.max_slippage_ticks)))
        fills=[];remaining=q
        if s.book:
            levels=sorted((s.book.asks if long else s.book.bids),key=lambda lv:lv.price,reverse=not long)
            for lv in levels:
                if remaining<=0:break
                if intent.order_type==OrderType.LIMIT:
                    if long and lv.price>intent.limit_price:continue
                    if (not long) and lv.price<intent.limit_price:continue
                qty=min(remaining,lv.size)
                if qty<=0:continue
                px=lv.price+side_mult*slip*s.tick_size
                if intent.order_type==OrderType.LIMIT:
                    px=min(px,intent.limit_price) if long else max(px,intent.limit_price)
                fee=(self.settings.commission_per_contract+self.settings.fee_per_contract)*qty
                fills.append(Fill(timestamp=s.timestamp+timedelta(milliseconds=self.settings.latency_ms),price=px,quantity=qty,fee=fee,slippage_ticks=slip));remaining-=qty
        else:
            px=touch+side_mult*slip*s.tick_size
            if intent.order_type==OrderType.LIMIT:
                px=min(px,intent.limit_price) if long else max(px,intent.limit_price)
            fee=(self.settings.commission_per_contract+self.settings.fee_per_contract)*q
            fills=[Fill(timestamp=s.timestamp+timedelta(milliseconds=self.settings.latency_ms),price=px,quantity=q,fee=fee,slippage_ticks=slip)];remaining=Decimal('0')

        filled=q-remaining
        if filled<=0:return self._report(intent,OrderStatus.NEW)
        if remaining>0 and not self.settings.allow_partial_fills:return self._report(intent,OrderStatus.REJECTED,reason='insufficient_depth')
        return self._report(intent,OrderStatus.FILLED if remaining<=0 else OrderStatus.PARTIAL,fills)
