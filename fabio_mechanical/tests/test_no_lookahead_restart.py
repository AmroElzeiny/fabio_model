from __future__ import annotations

import copy
import pickle
from typing import Any

import pytest

from fabio_mechanical.backtest import BacktestEngine, LookaheadError
from fabio_mechanical.contracts import snapshot_hash
from fabio_mechanical.journal import MemoryJournal
from fabio_mechanical.replay import ReplayEngine

SCEN = "mean_reversion_short"


def _drop_end(rows: list[dict[str, Any]], cut_ns: int) -> list[dict[str, Any]]:
    return [r for r in rows if r["timestamp_ns"] < cut_ns]


def test_replay_and_backtest_are_deterministic(scenario: Any, base_cfg: dict[str, Any], backtest: Any) -> None:
    events = scenario(SCEN)
    a = backtest(SCEN)
    b = BacktestEngine(copy.deepcopy(base_cfg)).run(events)
    assert a.ledger == b.ledger and a.fills == b.fills and a.state_transitions == b.state_transitions
    assert [c["candidate_id"] for c in a.candidates] == [c["candidate_id"] for c in b.candidates]
    s1 = [snapshot_hash(s) for s in ReplayEngine(base_cfg).iter_snapshots(events[:6000])]
    s2 = [snapshot_hash(s) for s in ReplayEngine(copy.deepcopy(base_cfg)).iter_snapshots(events[:6000])]
    assert s1 == s2 and len(s1) > 100


def test_end_to_end_prefix_invariance_no_lookahead(scenario: Any, base_cfg: dict[str, Any], backtest: Any) -> None:
    """Anything decided or filled before time T is identical whether or not data after T exists."""
    events = scenario(SCEN)
    full = backtest(SCEN)
    entry_ts = min(f["timestamp_ns"] for f in full.fills if f["purpose"] == "ENTRY")
    k = next(i for i, e in enumerate(events) if e.clock_ns > entry_ts + 2_000_000_000)  # cut while the trade is open
    cut_ns = events[k - 1].clock_ns  # the prefix run flattens at END_OF_DATA at exactly this time
    pre = BacktestEngine(copy.deepcopy(base_cfg)).run(events[:k])
    assert _drop_end(pre.state_transitions, cut_ns) == _drop_end(full.state_transitions, cut_ns)
    assert _drop_end(pre.fills, cut_ns) == _drop_end(full.fills, cut_ns)
    assert pre.candidates == [c for c in full.candidates if c["timestamp_ns"] < cut_ns]
    # the open trade is closed at END_OF_DATA in the prefix run, never with future information
    assert pre.ledger and pre.ledger[0]["exit_reason"] == "END_OF_DATA"
    full_snaps = [snapshot_hash(s) for s in ReplayEngine(base_cfg).iter_snapshots(events[:k])]
    pre_snaps = [snapshot_hash(s) for s in ReplayEngine(copy.deepcopy(base_cfg)).iter_snapshots(events[: k // 2])]
    assert full_snaps[: len(pre_snaps)] == pre_snaps


def test_out_of_order_snapshots_raise_lookahead(scenario: Any, base_cfg: dict[str, Any]) -> None:
    state: dict[str, Any] = {"first": None}

    def replay_old(s: Any) -> Any:
        if state["first"] is None:
            state["first"] = s
            return s
        return state["first"]  # feeding an older snapshot again = time travel

    with pytest.raises(LookaheadError):
        BacktestEngine(copy.deepcopy(base_cfg), snapshot_transform=replay_old).run(scenario(SCEN)[:5000])


def test_restart_from_checkpoint_is_consistent(scenario: Any, base_cfg: dict[str, Any], backtest: Any) -> None:
    events = scenario(SCEN)
    full = backtest(SCEN)
    k = int(len(events) * 0.8)  # the MR trade happens around here: checkpoint near the live setup
    first = BacktestEngine(copy.deepcopy(base_cfg), journal=MemoryJournal())
    for e in events[:k]:
        first.process_event(e)
    blob = pickle.dumps(first)
    restored = pickle.loads(blob)
    for e in events[k:]:
        restored.process_event(e)
    res = restored.finish()
    assert res.ledger == full.ledger and res.fills == full.fills and res.state_transitions == full.state_transitions
