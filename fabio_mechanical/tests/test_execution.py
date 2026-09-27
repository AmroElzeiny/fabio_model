from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from fabio_mechanical.contracts import Direction, EventAction, ExecutionIntent, MarketEvent, OrderPurpose, OrderStatus, OrderType, Side
from fabio_mechanical.execution import ExecutionSimulator, FuturesExecutionAdapter, LiveTradingDisabledError, MT5ExecutionAdapter
from fabio_mechanical.orderbook.book import MBOBook
from fabio_mechanical.strategy import StopWideningError

MS = 1_000_000
_oid = [1000]


def ev(
    t_ms: float, action: EventAction, side: Side = Side.NONE, price: int | None = None, size: int = 0, oid: int | None = None
) -> MarketEvent:
    return MarketEvent(
        ts_event_ns=int(t_ms * MS),
        sequence=int(t_ms * 10) + 1,
        instrument="NQ",
        active_contract="NQH6",
        action=action,
        side=side,
        price_ticks=price,
        size=size,
        order_id=oid,
        snapshot_complete=action == EventAction.SNAPSHOT,
    )


def add(book: MBOBook, t_ms: float, side: Side, price: int, size: int) -> int:
    _oid[0] += 1
    book.apply(ev(t_ms, EventAction.ADD, side, price, size, _oid[0]))
    return _oid[0]


def make_book(bids: dict[int, list[int]], asks: dict[int, list[int]]) -> MBOBook:
    b = MBOBook()
    b.apply(ev(0, EventAction.SNAPSHOT))
    for side, levels in ((Side.BID, bids), (Side.ASK, asks)):
        for p, sizes in levels.items():
            for s in sizes:
                add(b, 0, side, p, s)
    return b


def intent(
    direction: Direction = Direction.LONG,
    qty: int = 2,
    purpose: OrderPurpose = OrderPurpose.ENTRY,
    otype: OrderType = OrderType.MARKET,
    **kw: Any,
) -> ExecutionIntent:
    return ExecutionIntent(
        intent_id=f"i{_oid[0]}{purpose.value}{kw.get('limit_price_ticks', '')}",
        candidate_id="c",
        timestamp_ns=0,
        instrument="NQ",
        active_contract="NQH6",
        direction=direction,
        purpose=purpose,
        order_type=otype,
        quantity=qty,
        **kw,
    )


def test_latency_matches_against_the_book_at_arrival(cfg: dict[str, Any]) -> None:
    sim = ExecutionSimulator(cfg)
    book = make_book({100: [10]}, {101: [10]})
    o = sim.submit(intent(qty=2, reference_price_ticks=101), now_ns=0)
    assert o.arrival_ns == 10 * MS
    sim.on_before_event(5 * MS, book, True, True)
    assert not o.active
    book.apply(ev(8, EventAction.CANCEL, Side.ASK, 101, 0, _oid[0]))  # the ask moves away during the latency window
    add(book, 8, Side.ASK, 102, 10)
    sim.on_before_event(12 * MS, book, True, True)
    fills, reports = sim.drain()
    assert [f.fill.price_ticks for f in fills] == [103]  # 102 touch at arrival + 1 tick slippage
    assert fills[0].fill.slippage_ticks == 1 and fills[0].fill.timestamp_ns == 12 * MS
    assert reports[0].status == OrderStatus.FILLED and reports[0].latency_ms == 10


def test_depth_sweep_partial_fill_and_protection_band(cfg: dict[str, Any]) -> None:
    sim = ExecutionSimulator(cfg)
    book = make_book({100: [10]}, {101: [2], 102: [1], 130: [50]})
    sim.submit(intent(qty=5), now_ns=0)
    sim.on_before_event(20 * MS, book, True, True)
    fills, reports = sim.drain()
    assert [(f.fill.price_ticks, f.fill.quantity) for f in fills] == [(102, 2), (103, 1)]  # 130 lies outside the 10-tick band
    assert reports[0].status == OrderStatus.PARTIAL and reports[0].filled_quantity == 3
    cfg["execution"]["allow_partial_fills"] = False
    sim2 = ExecutionSimulator(cfg)
    sim2.submit(intent(qty=5), now_ns=0)
    sim2.on_before_event(20 * MS, make_book({100: [10]}, {101: [2]}), True, True)
    _, reports = sim2.drain()
    assert reports[-1].status == OrderStatus.REJECTED and reports[-1].rejected_reason == "insufficient_depth"


def test_entry_guards_gap_unsafe_target(cfg: dict[str, Any]) -> None:
    for kw, safe, reason in (
        ({"reference_price_ticks": 101}, True, "gap_exceeds_limit"),
        ({}, False, "unsafe_data"),
        ({"target_price_ticks": 125}, True, "target_already_reached"),
    ):
        sim = ExecutionSimulator(cfg)
        sim.submit(intent(**kw), now_ns=0)
        sim.on_before_event(20 * MS, make_book({129: [10]}, {130: [10]}), safe, True)
        _, reports = sim.drain()
        assert reports[0].status == OrderStatus.REJECTED and reports[0].rejected_reason == reason


def test_passive_target_respects_mbo_queue_and_trade_through(cfg: dict[str, Any]) -> None:
    sim = ExecutionSimulator(cfg)
    book = make_book({100: [10]}, {101: [5], 105: [10]})
    ahead_oid = _oid[0]
    o = sim.submit(intent(Direction.SHORT, 5, OrderPurpose.TARGET, OrderType.LIMIT, limit_price_ticks=105), now_ns=0)
    sim.on_before_event(20 * MS, book, True, True)
    assert o.active and o.priority is not None and not o.fills
    add(book, 21, Side.ASK, 105, 7)  # joins BEHIND our order
    trade = ev(22, EventAction.TRADE, Side.BID, 105, 6)
    book.apply(trade)
    sim.on_after_event(trade, book, True)
    assert not o.fills  # 10 lots ahead of us
    book.apply(ev(23, EventAction.CANCEL, Side.ASK, 105, 6, ahead_oid))  # the fill's book update: 4 left ahead
    trade2 = ev(24, EventAction.TRADE, Side.BID, 105, 8)
    book.apply(trade2)
    sim.on_after_event(trade2, book, True)
    assert [(f.price_ticks, f.quantity, f.liquidity) for f in o.fills] == [(105, 4, "MAKER")]
    trade3 = ev(25, EventAction.TRADE, Side.BID, 106, 1)  # traded through our price
    sim.on_after_event(trade3, book, True)
    assert o.status == OrderStatus.FILLED and sum(f.quantity for f in o.fills) == 5 and o.fills[-1].slippage_ticks == 0


def test_stop_triggers_on_trade_and_fills_through_the_gap(cfg: dict[str, Any]) -> None:
    sim = ExecutionSimulator(cfg)
    book = make_book({99: [3], 96: [10]}, {100: [10]})
    stop = sim.submit(intent(Direction.SHORT, 4, OrderPurpose.STOP, OrderType.STOP, trigger_price_ticks=98), now_ns=0)
    sim.on_before_event(20 * MS, book, True, True)
    assert stop.active and not stop.triggered
    book.apply(ev(30, EventAction.CANCEL, Side.BID, 99, 0, _oid[0] - 2))  # 99 bid pulled
    trade = ev(31, EventAction.TRADE, Side.ASK, 97, 1)
    sim.on_after_event(trade, book, False)
    assert stop.triggered and not stop.fills  # executes at the packet boundary
    sim.on_after_event(ev(31.5, EventAction.NONE), book, True)
    assert [(f.price_ticks, f.quantity) for f in stop.fills] == [(95, 4)]  # bid 96 - 1 tick slippage, 3 ticks worse than stop
    assert stop.fills[0].slippage_ticks == 3
    with pytest.raises(StopWideningError):
        sim2 = ExecutionSimulator(cfg)
        s2 = sim2.submit(intent(Direction.SHORT, 1, OrderPurpose.STOP, OrderType.STOP, trigger_price_ticks=98), now_ns=0)
        sim2.modify_stop(s2.order_id, 97)  # long position stop moved down = widening


def test_fees_per_contract_and_oco_resize(cfg: dict[str, Any]) -> None:
    sim = ExecutionSimulator(cfg)
    sim.submit(intent(qty=3), now_ns=0)
    sim.on_before_event(20 * MS, make_book({100: [10]}, {101: [10]}), True, True)
    fills, _ = sim.drain()
    assert fills[0].fill.fee == Decimal("2.10") * 3
    stop = sim.submit(intent(Direction.SHORT, 3, OrderPurpose.STOP, OrderType.STOP, trigger_price_ticks=90), now_ns=0)
    sim.set_remaining(stop.order_id, 1)
    assert stop.remaining == 1
    sim.set_remaining(stop.order_id, 0)
    assert stop.status == OrderStatus.CANCELLED


def test_live_adapters_are_disabled() -> None:
    for adapter in (MT5ExecutionAdapter(), FuturesExecutionAdapter()):
        with pytest.raises(LiveTradingDisabledError):
            adapter.submit(intent(), 0)
