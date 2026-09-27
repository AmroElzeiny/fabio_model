"""Raw synthetic MBO -> book -> trades -> Delta/CVD -> profiles/LVN -> auction state -> FSM -> candidate
-> risk -> simulated fills -> exit. No mocks anywhere after the raw Databento-shaped records."""

from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fabio_mechanical.backtest import BacktestEngine
from fabio_mechanical.ingest.synthetic import SCENARIOS

TREND_PATH = [
    "VALID_BREAKOUT",
    "DISPLACEMENT",
    "STRUCTURE_BREAK",
    "IMPULSE_LOCKED",
    "IMPULSE_PROFILE_READY",
    "WAIT_LVN_RETEST",
    "LVN_TOUCHED",
    "WAIT_AGGRESSION",
    "ENTRY_READY",
    "ACTIVE_TRADE",
    "EXIT",
]
MR_PATH = [
    "BREAKOUT_ATTEMPT",
    "NO_ACCEPTANCE",
    "FAILED_AUCTION",
    "VALUE_RECLAIM",
    "RECLAIM_LEG_LOCKED",
    "RECLAIM_PROFILE",
    "WAIT_LVN_RETEST",
    "SNAPBACK_AGGRESSION",
    "ENTRY_READY",
    "ACTIVE_TRADE",
    "EXIT",
]


def path_to_exit(transitions: list[dict[str, Any]], model: str) -> list[str]:
    """States of the setup that produced the (first) trade, from its setup start to EXIT."""
    rows = [t for t in transitions if t["model"] == model]
    end = next(i for i, t in enumerate(rows) if t["to_state"] == "EXIT")
    start = max(i for i, t in enumerate(rows[: end + 1]) if t["from_state"] == "BALANCED")
    return [t["to_state"] for t in rows[start : end + 1]]


@pytest.mark.parametrize("name,direction", [("trend_long", "LONG"), ("trend_short", "SHORT")])
def test_trend_trade_from_raw_mbo(backtest: Any, name: str, direction: str) -> None:
    res = backtest(name)
    assert len(res.ledger) == 1
    t = res.ledger[0]
    assert (t["setup_type"], t["direction"], t["exit_reason"]) == ("TREND", direction, "TARGET")
    assert path_to_exit(res.state_transitions, "TREND") == TREND_PATH
    # target is the prior balance POC (80240 above for longs, 80081 below for shorts)
    assert t["target_ticks"] == (80240 if direction == "LONG" else 80081)
    assert Decimal(t["net_pnl"]) > 0 and Decimal(t["r_multiple"]) > 1
    assert res.summary["trade_safe_at_end"] is True and res.summary["ai_enabled"] is False


@pytest.mark.parametrize("name,direction", [("mean_reversion_short", "SHORT"), ("mean_reversion_long", "LONG")])
def test_mean_reversion_trade_from_raw_mbo(backtest: Any, name: str, direction: str) -> None:
    res = backtest(name)
    assert len(res.ledger) == 1
    t = res.ledger[0]
    assert (t["setup_type"], t["direction"], t["exit_reason"]) == ("MEAN_REVERSION", direction, "TARGET")
    assert path_to_exit(res.state_transitions, "MEAN_REVERSION") == MR_PATH
    assert t["session"] == "LONDON"


def test_no_trade_without_aggression_or_structure(backtest: Any) -> None:
    res = backtest("trend_long_no_aggression")
    assert res.ledger == [] and res.candidates == []
    reached = [t for t in res.state_transitions if t["model"] == "TREND" and t["to_state"] == "WAIT_AGGRESSION"]
    assert reached, "the LVN was retested (location) but aggression never confirmed"
    assert backtest("quiet_balance").candidates == []


def test_execution_costs_latency_and_audit_trail(backtest: Any, base_cfg: dict[str, Any]) -> None:
    res = backtest("trend_long")
    t = res.ledger[0]
    entry_fills = [f for f in res.fills if f["purpose"] == "ENTRY"]
    exit_fills = [f for f in res.fills if f["purpose"] == "TARGET"]
    assert entry_fills and exit_fills
    assert all(f["liquidity"] == "TAKER" and f["slippage_ticks"] >= 1 for f in entry_fills)
    assert all(f["liquidity"] == "MAKER" and f["slippage_ticks"] == 0 for f in exit_fills)
    fee = Decimal(str(base_cfg["execution"]["commission_per_contract"])) + Decimal(str(base_cfg["execution"]["fee_per_contract"]))
    assert Decimal(t["fees"]) == fee * t["quantity"] * 2
    assert Decimal(t["net_pnl"]) == Decimal(t["gross_pnl"]) - Decimal(t["fees"])
    cand = res.candidates[0]
    latency_ns = base_cfg["execution"]["latency_ms"] * 1_000_000
    assert min(f["timestamp_ns"] for f in entry_fills) >= cand["timestamp_ns"] + latency_ns
    kinds = {r["type"] for r in res.journal}
    assert {
        "CANDIDATE",
        "DECISION",
        "RISK",
        "ORDER_SUBMIT",
        "ORDER_REPORT",
        "FILL",
        "POSITION_OPEN",
        "POSITION_CLOSE",
        "STATE_TRANSITION",
    } <= kinds
    c = next(r for r in res.journal if r["type"] == "CANDIDATE")
    assert c["evidence"]["aggression"]["ok"] is True and all(c["evidence"]["aggression"]["checks"].values())
    assert c["decision_snapshot"]["market_feature_sha256"] and c["decision_snapshot"]["ai_enabled"] is False
    assert next(r for r in res.journal if r["type"] == "RISK")["allowed"] is True


def test_dbn_file_ingestion_matches_in_memory(tmp_path: Path, base_cfg: dict[str, Any], backtest: Any) -> None:
    pytest.importorskip("databento")
    from fabio_mechanical.ingest.sources import chain_events, open_sources
    from fabio_mechanical.ingest.synthetic import write_dbn, write_definition_dbn

    m = SCENARIOS["mean_reversion_short"]()
    data = write_dbn(m.records, tmp_path / "glbx-mdp3-20260113.mbo.dbn")
    write_definition_dbn(list(m.resolver().contracts.values()), tmp_path / "glbx-mdp3-20260113.definition.dbn", m.records[0]["ts_recv"])
    sources = open_sources([data])
    res = BacktestEngine(copy.deepcopy(base_cfg)).run(chain_events(sources))
    ref = backtest("mean_reversion_short")
    assert res.ledger == ref.ledger and res.state_transitions == ref.state_transitions
