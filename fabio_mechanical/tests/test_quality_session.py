from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from fabio_mechanical.contracts import EventAction, MarketEvent, Side
from fabio_mechanical.ingest.local import iter_jsonl_events
from fabio_mechanical.market.session import SessionCalendar, session_label, trading_day
from fabio_mechanical.quality.engine import F_SNAPSHOT, DataQualityEngine
from fabio_mechanical.replay import ReplayEngine

FIXTURE = Path(__file__).parent / "fixtures" / "known_session.jsonl"


def base(
    seq: int, ts: int | None = None, action: EventAction = EventAction.SNAPSHOT, snap: bool = False, flags: int | None = None
) -> MarketEvent:
    return MarketEvent(
        ts_event_ns=ts if ts is not None else seq * 1000,
        sequence=seq,
        instrument="NQ",
        active_contract="NQH6",
        action=action,
        snapshot_complete=snap,
        provider_flags=flags,
    )


def ns(y: int, mo: int, d: int, h: int, mi: int) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp()) * 1_000_000_000


def test_quality_out_of_order_timestamp_duplicate_and_missing_snapshot() -> None:
    q = DataQualityEngine()
    q.observe_event(
        MarketEvent(
            ts_event_ns=2000,
            sequence=2,
            instrument="NQ",
            active_contract="NQH6",
            action=EventAction.ADD,
            side=Side.BID,
            price_ticks=100,
            size=1,
            order_id=1,
        )
    )
    q.observe_event(base(1, ts=1000))
    q.observe_event(base(1, ts=1000))
    f = q.flags
    assert f.missing_snapshot and f.out_of_order and f.timestamp_regression and f.duplicated_event and not f.trade_safe


def test_databento_snapshot_records_are_exempt_and_clear_resets() -> None:
    q = DataQualityEngine()
    q.observe_event(base(5000, ts=10_000))
    q.observe_event(
        MarketEvent(
            ts_event_ns=5,
            ts_recv_ns=10_001,
            sequence=0,
            instrument="NQ",
            active_contract="NQH6",
            action=EventAction.CLEAR,
            provider_flags=F_SNAPSHOT,
        )
    )
    q.observe_event(
        MarketEvent(
            ts_event_ns=6,
            ts_recv_ns=10_002,
            sequence=0,
            instrument="NQ",
            active_contract="NQH6",
            action=EventAction.ADD,
            side=Side.BID,
            price_ticks=100,
            size=1,
            order_id=7,
            provider_flags=F_SNAPSHOT,
        )
    )
    # snapshot records carry old ts_event/sequence 0 but ts_recv (clock) is monotonic; no severe flag
    assert q.trade_safe, q.flags.reasons
    q.observe_event(
        MarketEvent(
            ts_event_ns=10_003,
            sequence=5001,
            instrument="NQ",
            active_contract="NQH6",
            action=EventAction.ADD,
            side=Side.ASK,
            price_ticks=101,
            size=1,
            order_id=8,
        )
    )
    assert not q.trade_safe and q.flags.missing_snapshot  # update before the snapshot-complete marker


def test_dedupe_window_is_bounded() -> None:
    q = DataQualityEngine(dedupe_window=10)
    for i in range(100):
        q.observe_event(base(i + 1, ts=i + 1))
    assert len(q._seen) <= 11 and q.trade_safe


def test_stale_is_transient_not_latched() -> None:
    q = DataQualityEngine(stale_ns=1_000)
    q.observe_event(base(1, ts=1000, snap=True))
    assert q.check_stale(5_000).stale_stream and q.trade_safe
    assert q.stale_ms(3_000_000_000) == 2999
    q.observe_event(base(2, ts=6_000))
    assert not q.check_stale(6_500).stale_stream


def test_crossed_book_locks_trade_safe() -> None:
    eng = ReplayEngine()
    events = [
        base(1, snap=True),
        MarketEvent(
            ts_event_ns=2000,
            sequence=2,
            instrument="NQ",
            active_contract="NQH6",
            action=EventAction.ADD,
            side=Side.BID,
            price_ticks=101,
            size=2,
            order_id=1,
        ),
        MarketEvent(
            ts_event_ns=3000,
            sequence=3,
            instrument="NQ",
            active_contract="NQH6",
            action=EventAction.ADD,
            side=Side.ASK,
            price_ticks=100,
            size=2,
            order_id=2,
        ),
    ]
    eng.replay(events)
    assert eng.quality.flags.crossed_book and not eng.quality.trade_safe


def test_replay_determinism_prefix_invariance_and_roundtrip() -> None:
    from fabio_mechanical.contracts import snapshot_hash

    events = list(iter_jsonl_events(FIXTURE))
    r1 = ReplayEngine().replay(events).snapshots
    r2 = ReplayEngine().replay(list(iter_jsonl_events(FIXTURE))).snapshots
    assert r1 and [snapshot_hash(x) for x in r1] == [snapshot_hash(x) for x in r2]
    prefix = ReplayEngine().replay(events[:15]).snapshots
    assert [snapshot_hash(x) for x in r1[: len(prefix)]] == [snapshot_hash(x) for x in prefix]
    restored = type(r1[-1]).model_validate_json(r1[-1].model_dump_json())
    assert restored == r1[-1]


def test_session_boundary_resets_session_flow_state() -> None:
    events = list(iter_jsonl_events(FIXTURE))[:7]
    last = events[-1]
    events.append(
        MarketEvent(
            ts_event_ns=last.ts_event_ns + 1000,
            sequence=last.sequence + 1,
            instrument="NQ",
            active_contract="NQH6",
            action=EventAction.SESSION_BOUNDARY,
        )
    )
    eng = ReplayEngine()
    eng.replay(events)
    assert eng.flow.state.cvd == 0 and eng.profile.volume_by_price == {} and eng.session_boundaries == 1


def test_ny_session_dst_and_london_labels() -> None:
    # 09:30 New York = 13:30 UTC in EDT (after the March switch) and 14:30 UTC in EST
    assert session_label(ns(2026, 3, 10, 13, 30)) == "NY_RTH"
    assert session_label(ns(2026, 3, 6, 13, 30)) == "LONDON"  # still EST: 08:30 ET, London session
    assert session_label(ns(2026, 11, 10, 14, 30)) == "NY_RTH"
    assert session_label(ns(2026, 11, 10, 13, 30)) == "LONDON"
    # London 08:00 local: 08:00 UTC in winter (GMT), 07:00 UTC in summer (BST)
    assert session_label(ns(2026, 1, 13, 8, 0)) == "LONDON" and session_label(ns(2026, 1, 13, 7, 59)) == "GLOBEX_OVERNIGHT"
    assert session_label(ns(2026, 7, 14, 7, 0)) == "LONDON" and session_label(ns(2026, 7, 14, 6, 59)) == "GLOBEX_OVERNIGHT"
    assert session_label(ns(2026, 1, 13, 22, 30)) == "MAINTENANCE"  # 17:30 ET
    assert session_label(ns(2026, 1, 13, 21, 30)) == "POST_RTH"


def test_cme_trading_day_boundaries_across_dst() -> None:
    assert trading_day(ns(2026, 1, 13, 22, 59)) == date(2026, 1, 13)  # 17:59 ET
    assert trading_day(ns(2026, 1, 13, 23, 0)) == date(2026, 1, 14)  # 18:00 ET -> next trading day
    assert trading_day(ns(2026, 7, 14, 22, 0)) == date(2026, 7, 15)  # 18:00 EDT
    cal = SessionCalendar.from_config(None)
    day, start, end = cal.trading_day_bounds(ns(2026, 3, 8, 12, 0))  # contains the 2026-03-08 US DST switch
    assert day == date(2026, 3, 8) and (end - start) == 23 * 3600 * 10**9
    day, start, end = cal.trading_day_bounds(ns(2026, 3, 9, 15, 0))
    assert day == date(2026, 3, 9) and (end - start) == 24 * 3600 * 10**9
