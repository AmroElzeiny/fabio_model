from copy import deepcopy
from datetime import datetime, timezone
from fabio_market_replay.contracts import EventAction, MarketEvent, Side, snapshot_hash
from fabio_market_replay.quality.engine import DataQualityEngine
from fabio_market_replay.replay.engine import ReplayEngine
from fabio_market_replay.synthetic import known_fixture
from fabio_market_replay.market.session import session_label


def base(seq, ts=None, action=EventAction.SNAPSHOT, snap=False):
    return MarketEvent(ts_event_ns=ts if ts is not None else seq*1000, sequence=seq, instrument="NQ", active_contract="NQH6",
                       action=action, snapshot_complete=snap)


def test_quality_out_of_order_timestamp_duplicate_and_missing_snapshot():
    q=DataQualityEngine()
    add=MarketEvent(ts_event_ns=2000, sequence=2, instrument="NQ", active_contract="NQH6", action=EventAction.ADD,
                    side=Side.BID, price_ticks=100, size=1, order_id=1)
    q.observe_event(add)
    q.observe_event(base(1,ts=1000))
    q.observe_event(base(1,ts=1000))
    assert q.flags.missing_snapshot and q.flags.out_of_order and q.flags.timestamp_regression and q.flags.duplicated_event
    assert not q.flags.trade_safe


def test_crossed_book_locks_trade_safe():
    eng=ReplayEngine()
    events=[base(1,snap=True),
            MarketEvent(ts_event_ns=2000,sequence=2,instrument="NQ",active_contract="NQH6",action=EventAction.ADD,side=Side.BID,price_ticks=101,size=2,order_id=1),
            MarketEvent(ts_event_ns=3000,sequence=3,instrument="NQ",active_contract="NQH6",action=EventAction.ADD,side=Side.ASK,price_ticks=100,size=2,order_id=2)]
    eng.replay(events)
    assert eng.quality.flags.crossed_book and not eng.quality.flags.trade_safe


def test_replay_determinism_and_serialization_round_trip():
    r1=ReplayEngine().replay(known_fixture()).snapshots
    r2=ReplayEngine().replay(deepcopy(known_fixture())).snapshots
    assert [snapshot_hash(x) for x in r1] == [snapshot_hash(x) for x in r2]
    cls=type(r1[-1]); restored=cls.model_validate_json(r1[-1].model_dump_json())
    assert restored == r1[-1]


def test_no_lookahead_prefix_invariance():
    events=known_fixture()
    full=ReplayEngine().replay(events).snapshots
    prefix=ReplayEngine().replay(events[:15]).snapshots
    assert [snapshot_hash(x) for x in full[:len(prefix)]] == [snapshot_hash(x) for x in prefix]


def test_dst_session_handling_us_spring_and_fall():
    # 09:30 New York is 13:30 UTC after spring DST and 14:30 UTC after fall switch.
    spring=int(datetime(2026,3,10,13,30,tzinfo=timezone.utc).timestamp()*1e9)
    fall=int(datetime(2026,11,10,14,30,tzinfo=timezone.utc).timestamp()*1e9)
    assert session_label(spring) == "NY_RTH"
    assert session_label(fall) == "NY_RTH"


def test_session_boundary_resets_session_flow_state():
    events=known_fixture()[:7]
    last=events[-1]
    events.append(MarketEvent(ts_event_ns=last.ts_event_ns+1000,sequence=last.sequence+1,instrument="NQ",active_contract="NQH6",action=EventAction.SESSION_BOUNDARY))
    eng=ReplayEngine(); eng.replay(events)
    assert eng.flow.state.cvd == 0 and eng.profile.volume_by_price == {}
