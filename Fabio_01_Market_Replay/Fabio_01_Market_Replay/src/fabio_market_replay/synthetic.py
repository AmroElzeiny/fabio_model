from __future__ import annotations

from datetime import datetime, timezone

from fabio_market_replay.contracts import EventAction, MarketEvent, Side


def known_fixture() -> list[MarketEvent]:
    """Small deterministic NQ fixture with snapshot, queue changes, trades, replenishment and roll boundary."""
    base = int(datetime(2026, 1, 12, 14, 30, tzinfo=timezone.utc).timestamp() * 1e9)
    inst = "NQ"; contract = "NQH6"
    rows: list[MarketEvent] = []
    def ev(i: int, action: EventAction, side: Side = Side.NONE, price: int | None = None,
           size: int = 0, order: int | None = None, snapshot_complete: bool = False,
           active: str = contract) -> None:
        rows.append(MarketEvent(ts_event_ns=base + i * 1_000_000, sequence=i + 1, instrument=inst,
                                active_contract=active, action=action, side=side, price_ticks=price,
                                size=size, order_id=order, snapshot_complete=snapshot_complete))
    ev(0, EventAction.SNAPSHOT, snapshot_complete=True)
    ev(1, EventAction.ADD, Side.BID, 80000, 10, 1)
    ev(2, EventAction.ADD, Side.BID, 79999, 20, 2)
    ev(3, EventAction.ADD, Side.ASK, 80001, 12, 3)
    ev(4, EventAction.ADD, Side.ASK, 80002, 22, 4)
    ev(5, EventAction.TRADE, Side.BID, 80001, 5)
    ev(6, EventAction.FILL, Side.ASK, 80001, 5, 3)
    ev(7, EventAction.TRADE, Side.BID, 80001, 4)
    ev(8, EventAction.FILL, Side.ASK, 80001, 4, 3)
    ev(9, EventAction.CANCEL, Side.ASK, 80001, 3, 3)
    ev(10, EventAction.ADD, Side.ASK, 80001, 6, 5)  # replenishment evidence
    ev(11, EventAction.TRADE, Side.ASK, 80000, 7)
    ev(12, EventAction.FILL, Side.BID, 80000, 7, 1)
    ev(13, EventAction.MODIFY, Side.BID, 80000, 8, 1)
    ev(14, EventAction.TRADE, Side.BID, 80001, 10)
    ev(15, EventAction.TRADE, Side.BID, 80002, 15)
    ev(16, EventAction.TRADE, Side.BID, 80003, 20)
    ev(17, EventAction.TRADE, Side.ASK, 80002, 3)
    ev(18, EventAction.TRADE, Side.BID, 80003, 25)
    return rows


def roll_fixture() -> list[MarketEvent]:
    events = known_fixture()[:5]
    last = events[-1]
    ts = last.ts_event_ns + 1_000_000
    seq = last.sequence + 1
    events.append(MarketEvent(ts_event_ns=ts, sequence=seq, instrument="NQ", active_contract="NQM6",
                              action=EventAction.CONTRACT_ROLL))
    events.append(MarketEvent(ts_event_ns=ts+1_000_000, sequence=seq+1, instrument="NQ", active_contract="NQM6",
                              action=EventAction.SNAPSHOT, snapshot_complete=True))
    events.append(MarketEvent(ts_event_ns=ts+2_000_000, sequence=seq+2, instrument="NQ", active_contract="NQM6",
                              action=EventAction.ADD, side=Side.BID, price_ticks=80100, size=9, order_id=101))
    return events
