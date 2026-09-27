"""NQ/MNQ accounting (Package 2): realized/unrealized PnL, fees, R multiples, MFE/MAE, drawdown.

Prices are integer ticks; PnL = ticks x tick_value x contracts (exact Decimal arithmetic).
Every fill carries its own fee, so partial entries/exits are accounted exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from fabio_mechanical.contracts import Direction, Fill
from fabio_mechanical.data.instruments import InstrumentSpec


@dataclass
class Position:
    position_id: str
    instrument: str
    active_contract: str
    direction: Direction
    spec: InstrumentSpec
    setup_type: str
    candidate_id: str
    config_hash: str
    stop_ticks: int
    target_ticks: int
    entry_reference_ticks: int
    opened_ns: int
    trading_day: str
    session: str
    evidence: dict[str, Any] = field(default_factory=dict)
    market_regime: str = ""
    volatility_regime: str = ""
    news_day: str = ""
    quantity: int = 0
    entry_qty: int = 0
    entry_notional_ticks: int = 0
    entry_fees: Decimal = Decimal("0")
    exit_qty: int = 0
    exit_notional_ticks: int = 0
    exit_fees: Decimal = Decimal("0")
    realized_gross: Decimal = Decimal("0")
    entry_slippage_ticks: int = 0
    exit_slippage_ticks: int = 0
    mfe_ticks: int = 0
    mae_ticks: int = 0
    exit_reasons: list[str] = field(default_factory=list)
    risk_exposure: Decimal = Decimal("0")
    closed_ns: int | None = None

    @property
    def sign(self) -> int:
        return 1 if self.direction == Direction.LONG else -1

    @property
    def entry_avg_ticks(self) -> Decimal:
        return Decimal(self.entry_notional_ticks) / Decimal(self.entry_qty) if self.entry_qty else Decimal("0")

    @property
    def exit_avg_ticks(self) -> Decimal:
        return Decimal(self.exit_notional_ticks) / Decimal(self.exit_qty) if self.exit_qty else Decimal("0")

    def unrealized(self, price_ticks: int) -> Decimal:
        if not self.quantity:
            return Decimal("0")
        return (Decimal(price_ticks) - self.entry_avg_ticks) * self.sign * self.quantity * self.spec.tick_value

    def mark_excursion(self, price_ticks: int) -> None:
        if not self.entry_qty:
            return
        move = (Decimal(price_ticks) - self.entry_avg_ticks) * self.sign
        self.mfe_ticks = max(self.mfe_ticks, int(move))
        self.mae_ticks = min(self.mae_ticks, int(move))


@dataclass
class TradeLedgerEntry:
    trade_id: str
    instrument: str
    active_contract: str
    direction: str
    setup_type: str
    quantity: int
    entry_time_ns: int
    exit_time_ns: int
    entry_price: Decimal
    exit_price: Decimal
    entry_ticks: Decimal
    exit_ticks: Decimal
    entry_reference_ticks: int
    stop_ticks: int
    target_ticks: int
    stop_price: Decimal
    target_price: Decimal
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
    r_multiple: Decimal
    exit_reason: str
    exit_reasons: str
    mfe_ticks: int
    mae_ticks: int
    entry_slippage_ticks: int
    exit_slippage_ticks: int
    candidate_id: str
    config_hash: str
    trading_day: str
    session: str
    market_regime: str
    volatility_regime: str
    news_day: str


class Portfolio:
    def __init__(self, initial_equity: Decimal = Decimal("100000")) -> None:
        self.initial_equity = initial_equity
        self.cash = initial_equity
        self.equity = initial_equity
        self.peak = initial_equity
        self.max_drawdown = Decimal("0")
        self.realized = Decimal("0")
        self.unrealized = Decimal("0")
        self.fees_paid = Decimal("0")
        self.positions: list[Position] = []
        self.ledger: list[TradeLedgerEntry] = []

    def open(self, pos: Position) -> None:
        self.positions.append(pos)

    def add_entry_fill(self, pos: Position, fill: Fill, touch_slippage: int) -> None:
        pos.quantity += fill.quantity
        pos.entry_qty += fill.quantity
        pos.entry_notional_ticks += fill.price_ticks * fill.quantity
        pos.entry_fees += fill.fee
        pos.entry_slippage_ticks += touch_slippage * fill.quantity
        self.cash -= fill.fee
        self.fees_paid += fill.fee
        self.realized -= fill.fee

    def add_exit_fill(self, pos: Position, fill: Fill, reason: str, touch_slippage: int) -> TradeLedgerEntry | None:
        qty = min(fill.quantity, pos.quantity)
        gross = (Decimal(fill.price_ticks) - pos.entry_avg_ticks) * pos.sign * qty * pos.spec.tick_value
        pos.quantity -= qty
        pos.exit_qty += qty
        pos.exit_notional_ticks += fill.price_ticks * qty
        pos.exit_fees += fill.fee
        pos.exit_slippage_ticks += touch_slippage * qty
        pos.realized_gross += gross
        if not pos.exit_reasons or pos.exit_reasons[-1] != reason:
            pos.exit_reasons.append(reason)
        self.cash += gross - fill.fee
        self.fees_paid += fill.fee
        self.realized += gross - fill.fee
        if pos.quantity == 0:
            pos.closed_ns = fill.timestamp_ns
            return self._close(pos)
        return None

    def _close(self, pos: Position) -> TradeLedgerEntry:
        self.positions.remove(pos)
        exact = Decimal((pos.exit_notional_ticks - pos.entry_notional_ticks) * pos.sign) * pos.spec.tick_value
        adjust = exact - pos.realized_gross  # removes rounding of provisional partial-exit PnL
        self.cash += adjust
        self.realized += adjust
        pos.realized_gross = exact
        fees = pos.entry_fees + pos.exit_fees
        net = pos.realized_gross - fees
        risk_ticks = abs(Decimal(pos.entry_reference_ticks) - Decimal(pos.stop_ticks))
        risk_cash = risk_ticks * pos.spec.tick_value * pos.entry_qty
        r = (net / risk_cash).quantize(Decimal("0.0001")) if risk_cash else Decimal("0")
        entry = TradeLedgerEntry(
            trade_id=pos.position_id,
            instrument=pos.instrument,
            active_contract=pos.active_contract,
            direction=pos.direction.value,
            setup_type=pos.setup_type,
            quantity=pos.entry_qty,
            entry_time_ns=pos.opened_ns,
            exit_time_ns=pos.closed_ns or pos.opened_ns,
            entry_price=pos.spec.ticks_to_price(pos.entry_avg_ticks).quantize(Decimal("0.0001")),
            exit_price=pos.spec.ticks_to_price(pos.exit_avg_ticks).quantize(Decimal("0.0001")),
            entry_ticks=pos.entry_avg_ticks.quantize(Decimal("0.0001")),
            exit_ticks=pos.exit_avg_ticks.quantize(Decimal("0.0001")),
            entry_reference_ticks=pos.entry_reference_ticks,
            stop_ticks=pos.stop_ticks,
            target_ticks=pos.target_ticks,
            stop_price=pos.spec.ticks_to_price(pos.stop_ticks),
            target_price=pos.spec.ticks_to_price(pos.target_ticks),
            gross_pnl=pos.realized_gross,
            fees=fees,
            net_pnl=net,
            r_multiple=r,
            exit_reason=pos.exit_reasons[-1] if pos.exit_reasons else "",
            exit_reasons="+".join(pos.exit_reasons),
            mfe_ticks=pos.mfe_ticks,
            mae_ticks=pos.mae_ticks,
            entry_slippage_ticks=pos.entry_slippage_ticks,
            exit_slippage_ticks=pos.exit_slippage_ticks,
            candidate_id=pos.candidate_id,
            config_hash=pos.config_hash,
            trading_day=pos.trading_day,
            session=pos.session,
            market_regime=pos.market_regime,
            volatility_regime=pos.volatility_regime,
            news_day=pos.news_day,
        )
        self.ledger.append(entry)
        return entry

    def mark(self, price_ticks: int | None) -> None:
        if price_ticks is not None:
            self.unrealized = sum((p.unrealized(price_ticks) for p in self.positions), Decimal("0"))
        self.equity = self.cash + self.unrealized
        self.peak = max(self.peak, self.equity)
        dd = (self.peak - self.equity) / self.peak if self.peak else Decimal("0")
        self.max_drawdown = max(self.max_drawdown, dd)
