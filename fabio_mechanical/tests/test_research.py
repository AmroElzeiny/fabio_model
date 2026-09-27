from __future__ import annotations

import copy
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from fabio_mechanical.configs import config_hash, invariant_hash, load_search_space
from fabio_mechanical.ingest.sources import GeneratedSource
from fabio_mechanical.research.ablation import FEATURES, ablation_report
from fabio_mechanical.research.datasets import synthetic_dataset
from fabio_mechanical.research.locking import (
    OOSLockError,
    ProtocolError,
    check_sources,
    freeze_baseline,
    freeze_for_oos,
    phase_filter,
    require_frozen_baseline,
    verify_oos_lock,
)
from fabio_mechanical.research.monte_carlo import monte_carlo
from fabio_mechanical.research.optimize import generate_candidates, optimize
from fabio_mechanical.research.robustness import robustness_matrix
from fabio_mechanical.research.walk_forward import build_windows, walk_forward

from .conftest import events_for

pytestmark = pytest.mark.slow


def mr_dataset(day: date | None = None) -> list[GeneratedSource]:
    return [
        GeneratedSource(
            factory=lambda: events_for("mean_reversion_short"), spec={"scenario": "mean_reversion_short"}, trading_day=day, name="mr"
        )
    ]


def small_space() -> dict[str, Any]:
    s = load_search_space()
    return {"parameters": {k: s["parameters"][k] for k in ("aggression_delta_zscore", "stop_buffer_ticks")}}


def test_candidate_generation_is_reproducible_and_includes_baseline(base_cfg: dict[str, Any]) -> None:
    space = load_search_space()
    base = {k: base_cfg["strategy"][k] for k in space["parameters"]}
    a = generate_candidates(space, "random", 15, 7, baseline=base)
    b = generate_candidates(space, "random", 15, 7, baseline=base)
    c = generate_candidates(space, "random", 15, 8, baseline=base)
    assert a == b and a != c and a[0] == base and len(a) == 15 and len({str(x) for x in a}) == 15


def test_optimizer_retains_all_candidates_and_never_touches_invariants(base_cfg: dict[str, Any]) -> None:
    inv_before, cfg_before = invariant_hash(), config_hash(base_cfg)
    res = optimize(copy.deepcopy(base_cfg), mr_dataset(), small_space(), "grid", 4, 1, 42)
    assert res["candidates_evaluated"] == len(res["candidates"]) == 4
    assert res["candidates"][0]["is_baseline"] and res["candidates"][0]["config_hash"] == cfg_before
    assert all("metrics" in r and "score" in r and "params" in r for r in res["candidates"])
    assert res["selected"] is None or res["selected"] in res["candidates"]
    assert all(p in res["candidates"] for p in res["plateau"])
    assert set(res["parameter_sensitivity"]) <= {"aggression_delta_zscore", "stop_buffer_ticks"}
    assert invariant_hash() == inv_before and config_hash(base_cfg) == cfg_before
    again = optimize(copy.deepcopy(base_cfg), mr_dataset(), small_space(), "grid", 4, 1, 42)
    assert [r["metrics"] for r in again["candidates"]] == [r["metrics"] for r in res["candidates"]]


def test_walk_forward_freezes_train_params_for_validation(base_cfg: dict[str, Any]) -> None:
    days = synthetic_dataset(3, mix=("mean_reversion_short", "mean_reversion_long", "quiet_balance"))
    assert [s.trading_day for s in days] == [date(2026, 1, 13), date(2026, 1, 14), date(2026, 1, 15)]
    wins = build_windows(days, 1, 1, 1)
    assert [[s.trading_day for s in tr] + [s.trading_day for s in va] for tr, va in wins] == [
        [date(2026, 1, 13), date(2026, 1, 14)],
        [date(2026, 1, 14), date(2026, 1, 15)],
    ]
    res = walk_forward(copy.deepcopy(base_cfg), days, small_space(), 1, 1, 1, 2, 42)
    assert res["aggregate"]["windows"] == 2
    for w in res["windows"]:
        assert w["validation_days"][0] > w["train_days"][-1] and len(w["frozen_config_hash"]) == 64


def test_robustness_monte_carlo_and_ablation(base_cfg: dict[str, Any]) -> None:
    rob = robustness_matrix(copy.deepcopy(base_cfg), mr_dataset(), fee_multipliers=[1], slippage_multipliers=[1, 3], latencies=[10])
    names = [s["scenario"] for s in rob["scenarios"]]
    assert len(names) == 2 + 6 and {"fees_x": 1, "slippage_x": 3, "latency_ms": 10} in names
    assert {"name": "threshold_jitter_5pct"} in names and {"name": "missed_snapshots_10pct"} in names
    assert "session" in rob["tag_breakdown"]
    mc1, mc2 = monte_carlo([100, -50, 80, -40], 100, 7), monte_carlo([100, -50, 80, -40], 100, 7)
    assert mc1 == mc2 and mc1["bootstrap_modes"] == ["trade_order", "block"] and 0 <= mc1["dd_p95"] <= 1
    abl = ablation_report(copy.deepcopy(base_cfg), mr_dataset())
    assert [r["removed"] for r in abl["ablations"]] == FEATURES
    by = {r["removed"]: r["metrics"]["trades"] for r in abl["ablations"]}
    assert by["CVD"] == 0 and by["DOM features"] == abl["baseline"]["trades"]


def test_oos_lock_is_write_once_and_detects_changes(tmp_path: Path, base_cfg: dict[str, Any]) -> None:
    p = tmp_path / "lock.json"
    freeze_for_oos(p, base_cfg, "test-commit", dataset_sha256="abc")
    assert verify_oos_lock(p, base_cfg)
    with pytest.raises(OOSLockError):
        freeze_for_oos(p, base_cfg, "test-commit")
    changed = copy.deepcopy(base_cfg)
    changed["strategy"]["stop_buffer_ticks"] = 3
    with pytest.raises(OOSLockError):
        verify_oos_lock(p, changed)


def test_baseline_must_be_frozen_before_optimization(tmp_path: Path, base_cfg: dict[str, Any]) -> None:
    p = tmp_path / "baseline.json"
    with pytest.raises(ProtocolError):
        require_frozen_baseline(p, base_cfg)
    freeze_baseline(p, base_cfg, {"trades": 1}, "dataset", {"package_version": "x"})
    assert require_frozen_baseline(p, base_cfg)["config_hash"] == config_hash(base_cfg)
    tweaked = copy.deepcopy(base_cfg)
    tweaked["strategy"]["aggression_delta_zscore"] = 1.25
    with pytest.raises(ProtocolError):
        require_frozen_baseline(p, tweaked)
    exp = copy.deepcopy(base_cfg)
    exp["experimental"]["experimental_early_exit_enabled"] = True
    with pytest.raises(ProtocolError):
        freeze_baseline(tmp_path / "b2.json", exp, {}, "d", {})


def test_optimizer_cannot_see_oos_days(base_cfg: dict[str, Any]) -> None:
    cfg = copy.deepcopy(base_cfg)
    with pytest.raises(ProtocolError):
        phase_filter(cfg, ["development"])  # phases must be configured first
    cfg["research"]["phases"] = {
        "development": {"start": "2026-01-01", "end": "2026-01-12"},
        "validation": None,
        "oos": {"start": "2026-01-13", "end": "2026-01-31"},
    }
    with pytest.raises(OOSLockError):
        check_sources(cfg, mr_dataset(date(2026, 1, 13)), ["development"])
    allowed = phase_filter(cfg, ["development"])
    assert allowed("2026-01-12") and not allowed("2026-01-13")
    res = optimize(cfg, mr_dataset(), small_space(), "grid", 2, 0, 42, day_filter=allowed)
    assert all(r["metrics"]["trades"] == 0 for r in res["candidates"])  # the OOS day never reached the strategy
