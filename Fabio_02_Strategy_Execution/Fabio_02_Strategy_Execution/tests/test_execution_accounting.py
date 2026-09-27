from decimal import Decimal
from uuid import uuid4
from contracts.models import *
from fabio.config import load_config
from fabio.fixtures.synthetic import valid_trend_long, slippage_partial_fill
from fabio.execution import ExecutionSimulator
from fabio.accounting import Portfolio, Position

def intent(q='2'):
    s=valid_trend_long()[0]
    return ExecutionIntent(intent_id=str(uuid4()),candidate_id='c',timestamp=s.timestamp,instrument='MNQ',direction=Direction.LONG,order_type=OrderType.MARKET,quantity=Decimal(q),stop_price=Decimal('100'),target_price=Decimal('105'))

def test_slippage_and_latency():
    cfg=load_config();s=valid_trend_long()[0];r=ExecutionSimulator(cfg).execute(intent(),s)
    assert r.status==OrderStatus.FILLED;assert r.latency_ms==10;assert r.average_fill_price>s.ask

def test_partial_fill_depth_and_mbo_queue():
    cfg=load_config();s=slippage_partial_fill();r=ExecutionSimulator(cfg).execute(intent('4'),s)
    assert r.status==OrderStatus.PARTIAL;assert Decimal('0')<r.filled_quantity<Decimal('4')

def test_reject_unsafe():
    cfg=load_config();s=valid_trend_long()[0].model_copy(update={'trade_safe':False});r=ExecutionSimulator(cfg).execute(intent(),s);assert r.status==OrderStatus.REJECTED

def test_accounting_pnl_nq_mnq():
    p=Portfolio(Decimal('100000'));pos=Position('MNQ',Direction.LONG,Decimal('2'),Decimal('100'),Decimal('99'),Decimal('102'),Decimal('2'))
    p.open(pos);le=p.close(pos,Decimal('101'),Decimal('2'),'TARGET','TREND','c','h')
    # 4 ticks * 0.5 * 2 = 4 gross; net subtracts both entry and exit fees in ledger
    assert le.gross_pnl==Decimal('4');assert le.net_pnl==Decimal('0')
