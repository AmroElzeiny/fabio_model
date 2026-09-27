"""Event-driven execution simulator (Package 2 semantics on the reconstructed MBO book).

* Latency: an order submitted at ``t`` reaches the exchange at ``t + latency_ms`` and is matched
  against the book state at the first packet boundary at/after arrival (never an earlier state,
  never a later one).
* Market orders sweep displayed depth inside a protection band (``max_slippage_ticks`` from the
  touch at arrival); ``default_slippage_ticks`` is added adversely to every taker fill (Package 2
  semantics); the unfilled remainder is a partial fill (IOC) when ``allow_partial_fills``.
* Passive limit (target) orders join the back of the MBO queue; they fill only when traded
  volume at the price exceeds the displayed quantity with better priority, or when price trades
  through the limit. No candle-close fills exist anywhere.
* Stop-market orders trigger on a trade at/through the stop price and then sweep the book at
  the next packet boundary (gap-through fills are worse than the stop).
* Fees = (commission + exchange fee) per contract per fill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from fabio_mechanical.contracts import (
    Direction,
    EventAction,
    ExecutionIntent,
    ExecutionReport,
    Fill,
    MarketEvent,
    OrderPurpose,
    OrderStatus,
    OrderType,
    Side,
)
from fabio_mechanical.orderbook.book import MBOBook
from fabio_mechanical.strategy.target_stop import validate_stop_update


@dataclass
class SimOrder:
    intent: ExecutionIntent
    submitted_ns: int
    arrival_ns: int
    remaining: int
    seq: int
    status: OrderStatus = OrderStatus.NEW
    active: bool = False
    priority: int | None = None
    triggered: bool = False
    trigger_ticks: int | None = None
    fills: list[Fill] = field(default_factory=list)
    reject_reason: str | None = None
    reference_ticks: int | None = None
    tag: str = ""

    @property
    def order_id(self) -> str:
        return self.intent.intent_id

    @property
    def buy(self) -> bool:
        return self.intent.direction == Direction.LONG

    @property
    def done(self) -> bool:
        return self.status in {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}


@dataclass(frozen=True)
class FillEvent:
    order_id: str
    purpose: OrderPurpose
    tag: str
    fill: Fill


class ExecutionSimulator:
    def __init__(self, cfg: dict[str, Any]) -> None:
        e = cfg["execution"]
        self.latency_ms = int(e["latency_ms"])
        self.latency_ns = self.latency_ms * 1_000_000
        self.slippage_ticks = int(e["default_slippage_ticks"])
        self.band_ticks = int(e["max_slippage_ticks"])
        self.fee_per_contract = Decimal(str(e["commission_per_contract"])) + Decimal(str(e["fee_per_contract"]))
        self.max_gap_ticks = int(e["max_gap_ticks"])
        self.allow_partial = bool(e["allow_partial_fills"])
        self.queue_fill_fraction = float(e["queue_fill_fraction"])
        self.orders: dict[str, SimOrder] = {}
        self.fill_events: list[FillEvent] = []
        self.reports: list[ExecutionReport] = []
        self.last_trade_ticks: int | None = None
        self._seq = 0

    # ------------------------------------------------------------------ API
    def submit(self, intent: ExecutionIntent, now_ns: int, tag: str = "") -> SimOrder:
        self._seq += 1
        o = SimOrder(intent, now_ns, now_ns + self.latency_ns, intent.quantity, self._seq, tag=tag)
        if intent.order_type == OrderType.STOP:
            o.trigger_ticks = intent.trigger_price_ticks
        self.orders[o.order_id] = o
        return o

    def cancel(self, order_id: str, reason: str = "cancelled") -> None:
        o = self.orders.get(order_id)
        if o is None or o.done:
            return
        o.status = OrderStatus.CANCELLED
        o.reject_reason = reason
        self._finish(o)

    def set_remaining(self, order_id: str, quantity: int) -> None:
        """Server-side OCO resize (e.g. stop quantity after a partial target fill)."""
        o = self.orders.get(order_id)
        if o is None or o.done:
            return
        if quantity <= 0:
            self.cancel(order_id, "oco_filled")
        else:
            o.remaining = quantity

    def modify_stop(self, order_id: str, new_trigger: int) -> None:
        o = self.orders[order_id]
        assert o.trigger_ticks is not None
        # A long position's protective stop is a SELL stop (intent direction SHORT).
        position_dir = Direction.LONG if not o.buy else Direction.SHORT
        o.trigger_ticks = validate_stop_update(position_dir, o.trigger_ticks, new_trigger)

    def open_orders(self) -> list[SimOrder]:
        return [o for o in self.orders.values() if not o.done]

    def drain(self) -> tuple[list[FillEvent], list[ExecutionReport]]:
        fills, reports = self.fill_events, self.reports
        self.fill_events, self.reports = [], []
        return fills, reports

    # ------------------------------------------------------------------ event hooks
    def on_before_event(self, clock_ns: int, book: MBOBook, trade_safe: bool, at_boundary: bool) -> None:
        if not at_boundary:
            return
        for o in sorted((o for o in self.orders.values() if not o.active and not o.done), key=lambda x: (x.arrival_ns, x.seq)):
            if o.arrival_ns <= clock_ns:
                self._activate(o, clock_ns, book, trade_safe)

    def on_after_event(self, e: MarketEvent, book: MBOBook, at_boundary: bool) -> None:
        clock = e.clock_ns
        if e.action == EventAction.TRADE and e.price_ticks is not None and e.size > 0:
            self.last_trade_ticks = e.price_ticks
            self._on_trade(e.price_ticks, e.size, e.side, clock, book)
        if at_boundary:
            for o in sorted((o for o in self.orders.values() if o.triggered and not o.done), key=lambda x: x.seq):
                self._market(o, clock, book, band=False)

    def flush(self, clock_ns: int, book: MBOBook, trade_safe: bool) -> None:
        """End of data: every submitted order arrives now; triggered stops execute."""
        for o in sorted((o for o in self.orders.values() if not o.active and not o.done), key=lambda x: x.seq):
            self._activate(o, clock_ns, book, trade_safe)
        for o in sorted((o for o in self.orders.values() if o.triggered and not o.done), key=lambda x: x.seq):
            self._market(o, clock_ns, book, band=False)

    # ------------------------------------------------------------------ internals
    def _finish(self, o: SimOrder) -> None:
        filled = sum(f.quantity for f in o.fills)
        avg = (Decimal(sum(f.price_ticks * f.quantity for f in o.fills)) / Decimal(filled)) if filled else None
        self.reports.append(
            ExecutionReport(
                intent_id=o.order_id,
                status=o.status,
                fills=list(o.fills),
                rejected_reason=o.reject_reason,
                latency_ms=self.latency_ms,
                total_quantity=o.intent.quantity,
                filled_quantity=filled,
                average_fill_price_ticks=avg,
            )
        )

    def _reject(self, o: SimOrder, reason: str) -> None:
        o.status = OrderStatus.REJECTED
        o.reject_reason = reason
        self._finish(o)

    def _fill(self, o: SimOrder, clock: int, price: int, qty: int, slippage: int, liquidity: str) -> None:
        fill = Fill(
            timestamp_ns=clock,
            price_ticks=price,
            quantity=qty,
            fee=self.fee_per_contract * qty,
            slippage_ticks=slippage,
            liquidity=liquidity,  # type: ignore[arg-type]
        )
        o.fills.append(fill)
        o.remaining -= qty
        self.fill_events.append(FillEvent(o.order_id, o.intent.purpose, o.tag, fill))
        o.status = OrderStatus.FILLED if o.remaining <= 0 else OrderStatus.PARTIAL
        if o.remaining <= 0:
            self._finish(o)

    def _touch(self, book: MBOBook, buy: bool) -> int | None:
        return book.best_ask() if buy else book.best_bid()

    def _activate(self, o: SimOrder, clock: int, book: MBOBook, trade_safe: bool) -> None:
        o.active = True
        it = o.intent
        if it.purpose == OrderPurpose.ENTRY:
            if not trade_safe:
                return self._reject(o, "unsafe_data")
            touch = self._touch(book, o.buy)
            if touch is None:
                return self._reject(o, "no_liquidity")
            if it.reference_price_ticks is not None and abs(touch - it.reference_price_ticks) > self.max_gap_ticks:
                return self._reject(o, "gap_exceeds_limit")
            if it.target_price_ticks is not None and (touch >= it.target_price_ticks if o.buy else touch <= it.target_price_ticks):
                return self._reject(o, "target_already_reached")
            self._market(o, clock, book, band=True)
            return None
        if it.order_type == OrderType.MARKET:
            self._market(o, clock, book, band=False)
            return None
        if it.order_type == OrderType.LIMIT:
            assert it.limit_price_ticks is not None
            touch = self._touch(book, o.buy)
            marketable = touch is not None and (touch <= it.limit_price_ticks if o.buy else touch >= it.limit_price_ticks)
            if marketable:
                self._sweep(o, clock, book, worst=it.limit_price_ticks, limit=it.limit_price_ticks, reference=it.limit_price_ticks)
            if not o.done:
                o.priority = book.current_priority()
            return None
        if it.order_type == OrderType.STOP:
            trig = o.trigger_ticks
            assert trig is not None
            last = self.last_trade_ticks
            through = last is not None and (last >= trig if o.buy else last <= trig)
            if through:
                o.triggered = True
                self._market(o, clock, book, band=False)
        return None

    def _sweep(self, o: SimOrder, clock: int, book: MBOBook, worst: int | None, limit: int | None, reference: int | None) -> None:
        consumed = Side.ASK if o.buy else Side.BID
        for price, qty in book.sweep(consumed, o.remaining, worst):
            px = price + self.slippage_ticks if o.buy else price - self.slippage_ticks
            if limit is not None:
                px = min(px, limit) if o.buy else max(px, limit)
            ref = reference if reference is not None else price
            slip = (px - ref) if o.buy else (ref - px)
            self._fill(o, clock, px, qty, max(0, slip), "TAKER")
            if o.done:
                break

    def _market(self, o: SimOrder, clock: int, book: MBOBook, band: bool) -> None:
        touch = self._touch(book, o.buy)
        reference = o.trigger_ticks if o.trigger_ticks is not None else touch
        if touch is not None:
            worst = (touch + self.band_ticks if o.buy else touch - self.band_ticks) if band else None
            self._sweep(o, clock, book, worst=worst, limit=None, reference=reference)
        if o.done:
            return
        if band:
            if not o.fills:
                self._reject(o, "no_liquidity_within_protection_band")
            elif not self.allow_partial:
                self._reject(o, "insufficient_depth")
            else:
                o.status = OrderStatus.PARTIAL
                self._finish(o)
                o.status = OrderStatus.CANCELLED
                o.reject_reason = "ioc_remainder_cancelled"
            return
        # exits must complete: fall back to the last trade price (flagged by slippage)
        base = self.last_trade_ticks if self.last_trade_ticks is not None else reference
        if base is None:
            return
        px = base + self.slippage_ticks if o.buy else base - self.slippage_ticks
        ref = reference if reference is not None else base
        self._fill(o, clock, px, o.remaining, max(0, (px - ref) if o.buy else (ref - px)), "TAKER")

    def _on_trade(self, price: int, size: int, aggressor: Side, clock: int, book: MBOBook) -> None:
        for o in sorted((x for x in self.orders.values() if x.active and not x.done), key=lambda x: x.seq):
            it = o.intent
            if it.order_type == OrderType.LIMIT and not o.triggered:
                limit = it.limit_price_ticks
                assert limit is not None
                if o.buy:
                    if aggressor != Side.ASK:
                        continue
                    through, at = price < limit, price == limit
                    rest_side = Side.BID
                else:
                    if aggressor != Side.BID:
                        continue
                    through, at = price > limit, price == limit
                    rest_side = Side.ASK
                if through:
                    self._fill(o, clock, limit, o.remaining, 0, "MAKER")
                elif at:
                    if hasattr(book, "queue_ahead") and o.priority is not None:
                        ahead = book.queue_ahead(rest_side, limit, o.priority)
                    else:  # no order-level data: Package 2 fraction model
                        ahead = int(book.level_size(rest_side, limit) * (1 - self.queue_fill_fraction))
                    qty = min(o.remaining, max(0, size - ahead))
                    if qty > 0:
                        self._fill(o, clock, limit, qty, 0, "MAKER")
            elif it.order_type == OrderType.STOP and not o.triggered:
                trig = o.trigger_ticks
                assert trig is not None
                if (o.buy and price >= trig) or (not o.buy and price <= trig):
                    o.triggered = True
