from __future__ import annotations

from typing import Any

from fabio_mechanical.contracts import EventAction, MarketEvent, Side
from fabio_mechanical.orderbook.book import MBOBook
from fabio_mechanical.orderbook.mbp10 import MBP10Book
from fabio_mechanical.replay import ReplayEngine


def e(
    seq: int,
    action: EventAction,
    side: Side = Side.NONE,
    price: int | None = None,
    size: int = 0,
    oid: int | None = None,
    snap: bool = False,
    contract: str = "NQH6",
) -> MarketEvent:
    return MarketEvent(
        ts_event_ns=seq * 1000,
        sequence=seq,
        instrument="NQ",
        active_contract=contract,
        action=action,
        side=side,
        price_ticks=price,
        size=size,
        order_id=oid,
        snapshot_complete=snap,
    )


def seeded() -> MBOBook:
    b = MBOBook()
    b.apply(e(1, EventAction.SNAPSHOT, snap=True))
    return b


def test_add_cancel_modify_and_queue_position() -> None:
    b = seeded()
    b.apply(e(2, EventAction.ADD, Side.BID, 100, 5, 1))
    b.apply(e(3, EventAction.ADD, Side.BID, 100, 7, 2))
    assert b.snapshot().best_bid is not None and b.snapshot().best_bid.size == 12
    assert b.queue_position(2) == {"orders_ahead": 1, "contracts_ahead": 5}
    b.apply(e(4, EventAction.CANCEL, Side.BID, 100, 2, 1))
    assert b.snapshot().best_bid.size == 10  # type: ignore[union-attr]
    b.apply(e(5, EventAction.MODIFY, Side.BID, 99, 6, 2))
    assert [(x.price_ticks, x.size) for x in b.levels(Side.BID)] == [(100, 3), (99, 6)]


def test_modify_priority_rules_follow_databento() -> None:
    b = seeded()
    b.apply(e(2, EventAction.ADD, Side.ASK, 101, 5, 1))
    b.apply(e(3, EventAction.ADD, Side.ASK, 101, 5, 2))
    b.apply(e(4, EventAction.MODIFY, Side.ASK, 101, 3, 1))  # size decrease keeps priority
    assert b.queue_position(1) == {"orders_ahead": 0, "contracts_ahead": 0}
    b.apply(e(5, EventAction.MODIFY, Side.ASK, 101, 9, 1))  # size increase loses priority
    assert b.queue_position(1) == {"orders_ahead": 1, "contracts_ahead": 5}
    b.apply(e(6, EventAction.MODIFY, Side.ASK, 101, 0, 2))  # modify to zero removes
    assert 2 not in b.orders and b.level_size(Side.ASK, 101) == 9


def test_fill_is_non_book_mutating_and_attributes_execution() -> None:
    b = seeded()
    b.apply(e(2, EventAction.ADD, Side.ASK, 101, 10, 9))
    b.apply(e(3, EventAction.TRADE, Side.BID, 101, 4))
    b.apply(e(4, EventAction.FILL, Side.ASK, 101, 4, 9))
    assert b.snapshot().best_ask.size == 10  # type: ignore[union-attr]
    b.apply(e(5, EventAction.CANCEL, Side.ASK, 101, 4, 9))  # the cancel that accompanies the fill
    m = b.liquidity_metrics()
    assert b.snapshot().best_ask.size == 6  # type: ignore[union-attr]
    assert m["ask_executed_volume"] == 4 and m["ask_pulling_volume"] == 0


def test_dom_imbalance_microprice_and_queue_ahead() -> None:
    b = seeded()
    b.apply(e(2, EventAction.ADD, Side.BID, 100, 20, 1))
    b.apply(e(3, EventAction.ADD, Side.ASK, 101, 10, 2))
    s = b.snapshot()
    assert s.spread_ticks == 1 and s.dom_imbalance > 0 and s.microprice_ticks is not None and 100 < s.microprice_ticks < 101
    pri = b.current_priority()
    b.apply(e(4, EventAction.ADD, Side.ASK, 101, 4, 3))  # joins behind a virtual order placed at ``pri``
    assert b.queue_ahead(Side.ASK, 101, pri) == 10
    assert b.sweep(Side.ASK, 12) == [(101, 12)]


def test_pulling_stacking_replenishment() -> None:
    b = seeded()
    b.apply(e(2, EventAction.ADD, Side.BID, 100, 10, 1))
    b.apply(e(3, EventAction.CANCEL, Side.BID, 100, 5, 1))
    b.apply(e(4, EventAction.ADD, Side.BID, 100, 4, 2))
    m = b.liquidity_metrics()
    assert m["bid_stacking_volume"] == 14 and m["bid_pulling_volume"] == 5 and m["bid_replenishment"] == 4


def test_contract_roll_clears_book_and_reinitializes() -> None:
    from pathlib import Path

    from fabio_mechanical.ingest.local import iter_jsonl_events

    events = list(iter_jsonl_events(Path(__file__).parent / "fixtures" / "known_session.jsonl"))[:5]
    last = events[-1]
    ts, seq = last.ts_event_ns + 1_000_000, last.sequence + 1
    events += [
        MarketEvent(ts_event_ns=ts, sequence=seq, instrument="NQ", active_contract="NQM6", action=EventAction.CONTRACT_ROLL),
        MarketEvent(
            ts_event_ns=ts + 1,
            sequence=seq + 1,
            instrument="NQ",
            active_contract="NQM6",
            action=EventAction.SNAPSHOT,
            snapshot_complete=True,
        ),
        MarketEvent(
            ts_event_ns=ts + 2,
            sequence=seq + 2,
            instrument="NQ",
            active_contract="NQM6",
            action=EventAction.ADD,
            side=Side.BID,
            price_ticks=80100,
            size=9,
            order_id=101,
        ),
    ]
    eng = ReplayEngine()
    eng.replay(events)
    assert eng.book.active_contract == "NQM6" and eng.contract_rolls == 1
    assert eng.book.snapshot().best_bid.price_ticks == 80100  # type: ignore[union-attr]
    assert eng.quality.trade_safe


def test_clear_reset_removes_all_displayed_orders() -> None:
    b = seeded()
    b.apply(e(2, EventAction.ADD, Side.BID, 100, 5, 1))
    b.apply(e(3, EventAction.CLEAR))
    assert b.snapshot().best_bid is None and len(b.orders) == 0 and not b.initialized


def test_mbp10_fallback_book() -> None:
    rec: dict[str, Any] = {
        "ts_event": 1,
        "sequence": 2,
        "bid_px_00": 20_000_000_000_000,
        "bid_sz_00": 12,
        "bid_ct_00": 3,
        "ask_px_00": 20_000_250_000_000,
        "ask_sz_00": 8,
        "ask_ct_00": 2,
    }
    b = MBP10Book()
    b.apply_record(rec, instrument="NQ", active_contract="NQH6")
    s = b.snapshot()
    assert s.best_bid.price_ticks == 80000 and s.best_ask.price_ticks == 80001  # type: ignore[union-attr]
    assert s.queue_metrics["queue_available"] == 0.0 and s.mbo_available is False
