from fabio_market_replay.contracts import EventAction, MarketEvent, Side
from fabio_market_replay.orderbook.book import MBOBook
from fabio_market_replay.synthetic import roll_fixture
from fabio_market_replay.replay.engine import ReplayEngine


def e(seq, action, side=Side.NONE, price=None, size=0, oid=None, snap=False, contract="NQH6"):
    return MarketEvent(ts_event_ns=seq*1000, sequence=seq, instrument="NQ", active_contract=contract,
                       action=action, side=side, price_ticks=price, size=size, order_id=oid, snapshot_complete=snap)


def test_add_cancel_modify_and_queue_position():
    b=MBOBook()
    b.apply(e(1, EventAction.SNAPSHOT, snap=True))
    b.apply(e(2, EventAction.ADD, Side.BID, 100, 5, 1))
    b.apply(e(3, EventAction.ADD, Side.BID, 100, 7, 2))
    assert b.snapshot().best_bid.size == 12
    assert b.queue_position(2) == {"orders_ahead": 1, "contracts_ahead": 5}
    b.apply(e(4, EventAction.CANCEL, Side.BID, 100, 2, 1))
    assert b.snapshot().best_bid.size == 10
    b.apply(e(5, EventAction.MODIFY, Side.BID, 99, 6, 2))
    levels = b.levels(Side.BID)
    assert [(x.price_ticks,x.size) for x in levels] == [(100,3),(99,6)]


def test_fill_is_non_book_mutating_per_databento_semantics():
    b=MBOBook(); b.apply(e(1, EventAction.SNAPSHOT, snap=True)); b.apply(e(2, EventAction.ADD, Side.ASK, 101, 10, 9))
    b.apply(e(3, EventAction.FILL, Side.ASK, 101, 4, 9))
    assert b.snapshot().best_ask.size == 10


def test_dom_imbalance_and_microprice():
    b=MBOBook(); b.apply(e(1, EventAction.SNAPSHOT, snap=True))
    b.apply(e(2, EventAction.ADD, Side.BID, 100, 20, 1)); b.apply(e(3, EventAction.ADD, Side.ASK, 101, 10, 2))
    s=b.snapshot()
    assert s.spread_ticks == 1
    assert s.dom_imbalance > 0
    assert 100 < s.microprice_ticks < 101


def test_pulling_stacking_replenishment():
    b=MBOBook(); b.apply(e(1, EventAction.SNAPSHOT, snap=True))
    b.apply(e(2, EventAction.ADD, Side.BID, 100, 10, 1)); b.apply(e(3, EventAction.CANCEL, Side.BID, 100, 5, 1))
    b.apply(e(4, EventAction.ADD, Side.BID, 100, 4, 2))
    m=b.liquidity_metrics()
    assert m["bid_stacking_volume"] == 14
    assert m["bid_pulling_volume"] == 5
    assert m["bid_replenishment"] == 4


def test_contract_roll_clears_book_and_reinitializes():
    eng=ReplayEngine(); eng.replay(roll_fixture())
    assert eng.book.active_contract == "NQM6"
    assert eng.book.snapshot().best_bid.price_ticks == 80100


def test_clear_reset_removes_all_displayed_orders():
    b=MBOBook(); b.apply(e(1, EventAction.SNAPSHOT, snap=True)); b.apply(e(2, EventAction.ADD, Side.BID, 100, 5, 1))
    b.apply(e(3, EventAction.CLEAR))
    assert b.snapshot().best_bid is None and len(b.orders) == 0 and not b.initialized
