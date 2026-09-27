from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from contracts.models import Direction

INSTRUMENTS={
 'NQ':{'tick_size':Decimal('0.25'),'tick_value':Decimal('5.00'),'multiplier':Decimal('20')},
 'MNQ':{'tick_size':Decimal('0.25'),'tick_value':Decimal('0.50'),'multiplier':Decimal('2')},
}

@dataclass
class Position:
    instrument:str; direction:Direction; quantity:Decimal; entry:Decimal; stop:Decimal; target:Decimal; fees:Decimal=Decimal('0')

@dataclass
class TradeLedgerEntry:
    instrument:str; direction:Direction; quantity:Decimal; entry:Decimal; exit:Decimal; gross_pnl:Decimal; fees:Decimal; net_pnl:Decimal; r_multiple:Decimal; exit_reason:str; mfe_ticks:Decimal; mae_ticks:Decimal; setup_type:str; candidate_id:str; config_hash:str

class Portfolio:
    def __init__(self,initial_equity:Decimal=Decimal('100000')):
        self.cash=initial_equity;self.equity=initial_equity;self.peak=initial_equity;self.max_drawdown=Decimal('0');self.realized=Decimal('0');self.unrealized=Decimal('0');self.positions:list[Position]=[];self.ledger:list[TradeLedgerEntry]=[]
    def _tick_value(self,inst):return INSTRUMENTS[inst]['tick_value']
    def pnl(self,p:Position,price:Decimal)->Decimal:
        ticks=(price-p.entry)/INSTRUMENTS[p.instrument]['tick_size']; ticks=ticks if p.direction==Direction.LONG else -ticks
        return ticks*self._tick_value(p.instrument)*p.quantity
    def mark(self,price_by_instrument:dict[str,Decimal]):
        self.unrealized=sum((self.pnl(p,price_by_instrument[p.instrument]) for p in self.positions if p.instrument in price_by_instrument),Decimal('0'))
        self.equity=self.cash+self.unrealized; self.peak=max(self.peak,self.equity); dd=(self.peak-self.equity)/self.peak if self.peak else Decimal('0'); self.max_drawdown=max(self.max_drawdown,dd)
    def open(self,p:Position): self.positions.append(p); self.cash-=p.fees; self.realized-=p.fees
    def close(self,p:Position,exit_price:Decimal,exit_fees:Decimal,reason:str,setup_type:str,candidate_id:str,config_hash:str,mfe_ticks:Decimal=Decimal('0'),mae_ticks:Decimal=Decimal('0')):
        gross=self.pnl(p,exit_price); net=gross-p.fees-exit_fees
        risk_ticks=abs((p.entry-p.stop)/INSTRUMENTS[p.instrument]['tick_size']); risk_cash=risk_ticks*self._tick_value(p.instrument)*p.quantity
        r=net/risk_cash if risk_cash else Decimal('0')
        self.cash+=gross-exit_fees;self.realized+=gross-exit_fees;self.positions.remove(p);self.equity=self.cash
        le=TradeLedgerEntry(p.instrument,p.direction,p.quantity,p.entry,exit_price,gross,p.fees+exit_fees,net,r,reason,mfe_ticks,mae_ticks,setup_type,candidate_id,config_hash);self.ledger.append(le);return le
