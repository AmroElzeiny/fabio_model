from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

from fabio_mechanical.backtest import BacktestEngine
from fabio_mechanical.contracts import EventAction, MarketEvent, Side
from fabio_mechanical.replay import ReplayEngine
from fabio_mechanical.risk import RiskEngine
from fabio_mechanical.strategy import TrendMachine

MR = "mean_reversion_short"


def _entry_time(res: Any) -> int:
    return min(f["timestamp_ns"] for f in res.fills if f["purpose"] == "ENTRY")


def test_corrupted_book_locks_trading(scenario: Any, base_cfg: dict[str, Any], backtest: Any) -> None:
    events = list(scenario(MR))
    k = next(i for i, e in enumerate(events) if e.clock_ns > _entry_time(backtest(MR)) - 120_000_000_000)
    e = events[k]
    crossed = MarketEvent(
        ts_event_ns=e.ts_event_ns,
        ts_recv_ns=e.ts_recv_ns,
        sequence=e.sequence,
        instrument="NQ",
        active_contract="NQH6",
        action=EventAction.ADD,
        side=Side.BID,
        price_ticks=90_000,
        size=1,
        order_id=999_999_999,
        provider_flags=128,
    )
    events.insert(k, crossed)
    res = BacktestEngine(copy.deepcopy(base_cfg)).run(events)
    assert res.ledger == [] and res.summary["trade_safe_at_end"] is False
    assert "crossed_or_locked_book" in res.summary["data_quality_reasons"]
    dq = [r for r in res.journal if r["type"] == "DATA_QUALITY"]
    assert dq and dq[0]["trade_safe"] is False


def test_checksum_mismatch_locks_trading(scenario: Any, base_cfg: dict[str, Any]) -> None:
    eng = BacktestEngine(copy.deepcopy(base_cfg))
    eng.replay.quality.mark_checksum_mismatch("manifest_checksum_mismatch:test.dbn")
    res = eng.run(scenario(MR))
    assert res.ledger == [] and res.candidates == [] and not res.summary["trade_safe_at_end"]


def test_stale_data_blocks_strategy_and_risk(scenario: Any, base_cfg: dict[str, Any]) -> None:
    eng = ReplayEngine(base_cfg)
    for e in scenario("trend_long")[:3000]:
        eng.process(e)
    stale = eng.snapshot_at(eng.last_clock_ns + 60_000_000_000)
    assert stale.stale_ms >= 60_000 and stale.data_quality.stale_stream and stale.trade_safe
    m = TrendMachine(base_cfg)
    assert m.update(stale) is None and m.state.value == "BALANCED"
    from .fixtures.snapshots import valid_trend_long

    c = TrendMachine(base_cfg).update(valid_trend_long())
    assert c is not None
    stale_snap = valid_trend_long().model_copy(update={"stale_ms": 10_000})
    assert RiskEngine(base_cfg).evaluate(stale_snap, c, Decimal("5")).reason == "stale_data_lock"


def test_contract_roll_resets_state_and_stays_safe(scenario: Any, base_cfg: dict[str, Any]) -> None:
    eng = BacktestEngine(copy.deepcopy(base_cfg))
    res = eng.run(scenario("roll"))
    assert res.counters.get("contract_rolls") == 1 and eng.replay.contract_rolls == 1
    assert eng.replay.book.active_contract == "NQM6" and eng.replay.book.initialized
    assert res.summary["trade_safe_at_end"] is True
    assert eng.replay.structure.contract == "NQM6"
    assert all(b.active_contract == "NQM6" for b in eng.replay.structure.balance_areas())


def test_open_position_is_flattened_on_roll(scenario: Any, base_cfg: dict[str, Any], backtest: Any) -> None:
    events = list(scenario(MR))
    k = next(i for i, e in enumerate(events) if e.clock_ns > _entry_time(backtest(MR)) + 1_000_000_000)
    e = events[k]
    roll = MarketEvent(
        ts_event_ns=e.ts_event_ns,
        ts_recv_ns=e.ts_recv_ns,
        sequence=0,
        instrument="NQ",
        active_contract="NQM6",
        action=EventAction.CONTRACT_ROLL,
    )
    res = BacktestEngine(copy.deepcopy(base_cfg)).run(events[:k] + [roll])
    assert len(res.ledger) == 1 and res.ledger[0]["exit_reason"] == "CONTRACT_ROLL"


def test_kill_switch_and_risk_locks_in_pipeline(scenario: Any, base_cfg: dict[str, Any], backtest: Any) -> None:
    eng = BacktestEngine(copy.deepcopy(base_cfg))
    eng.risk.set_kill_switch(True)
    res = eng.run(scenario(MR))
    assert res.ledger == [] and res.fills == [] and res.counters.get("risk_reject:kill_switch", 0) >= 1
    assert {c["outcome"] for c in res.candidates} == {"risk_reject:kill_switch"}
    events = scenario(MR)
    k = next(i for i, e in enumerate(events) if e.clock_ns > _entry_time(backtest(MR)) + 1_000_000_000)
    eng2 = BacktestEngine(copy.deepcopy(base_cfg))
    for e in events[:k]:
        eng2.process_event(e)
    assert eng2.position is not None
    eng2.kill()
    res2 = eng2.finish()
    assert res2.ledger[0]["exit_reason"] == "KILL_SWITCH" and eng2.risk.state.kill_switch


def test_flatten_at_session_end(scenario: Any, base_cfg: dict[str, Any]) -> None:
    cfg = copy.deepcopy(base_cfg)
    cfg["risk"]["flatten_time"] = "09:42"  # the trend entry fills at ~09:41 ET
    res = BacktestEngine(cfg).run(scenario("trend_long"))
    assert len(res.ledger) == 1 and res.ledger[0]["exit_reason"] == "SESSION_END"


def test_phase_filter_hides_disallowed_days_from_the_strategy(scenario: Any, base_cfg: dict[str, Any]) -> None:
    res = BacktestEngine(copy.deepcopy(base_cfg), day_filter=lambda day: False).run(scenario(MR))
    assert res.ledger == [] and res.candidates == [] and res.counters["snapshots_outside_phase"] == res.counters["snapshots"]
    assert res.state_transitions == []
