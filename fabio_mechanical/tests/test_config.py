from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from fabio_mechanical.configs import (
    CONFIG_DIR,
    ConfigError,
    FrozenInvariantError,
    apply_strategy_params,
    config_hash,
    invariant_hash,
    load_config,
    load_invariants,
    load_search_space,
    validate_config,
    with_overrides,
)
from fabio_mechanical.configs.validation import StrategyParams


def test_config_loads_validates_and_hashes_deterministically() -> None:
    a, b = load_config(), load_config()
    assert config_hash(a) == config_hash(b) == config_hash(copy.deepcopy(a))
    changed = copy.deepcopy(a)
    changed["strategy"]["stop_buffer_ticks"] = 3
    assert config_hash(changed) != config_hash(a)
    assert a["AI_ENABLED"] is False
    assert all(v is False for v in a["experimental"].values())
    assert set(a) >= {"strategy", "structure", "market", "risk", "execution", "data", "research"}


def test_ai_enabled_is_rejected(cfg: dict[str, Any]) -> None:
    cfg["AI_ENABLED"] = True
    with pytest.raises(ConfigError):
        validate_config(cfg)


def test_experimental_extensions_are_off_in_baseline(cfg: dict[str, Any]) -> None:
    for key in cfg["experimental"]:
        assert key.startswith("experimental_")
    cfg["experimental"]["experimental_early_exit_enabled"] = True
    with pytest.raises(ConfigError):
        validate_config(cfg)
    validate_config(cfg, allow_experimental=True)


def test_optimizer_cannot_touch_invariants_or_non_tunables(cfg: dict[str, Any]) -> None:
    before = invariant_hash()
    for bad in ({"never_widen_stop": False}, {"risk_fraction": 0.05}, {"latency_ms": 0}, {"session_windows": {}}, {"AI_ENABLED": True}):
        with pytest.raises(FrozenInvariantError):
            apply_strategy_params(cfg, bad)
    with pytest.raises(FrozenInvariantError):
        apply_strategy_params(cfg, {"stop_buffer_ticks": 99})  # outside declared bounds
    out = apply_strategy_params(cfg, {"stop_buffer_ticks": 3, "aggression_delta_zscore": 1.5})
    assert out["strategy"]["stop_buffer_ticks"] == 3 and out["risk"] == cfg["risk"]
    assert cfg["strategy"]["stop_buffer_ticks"] == 2  # input untouched
    assert invariant_hash() == before
    with pytest.raises(FrozenInvariantError):
        with_overrides(cfg, "strategy", stop_buffer_ticks=1)


def test_search_space_is_disjoint_from_invariants_and_contains_baseline() -> None:
    space = load_search_space()
    inv = load_invariants()
    cfg = load_config()
    assert not set(space["parameters"]) & set(inv["rules"])
    assert set(space["parameters"]) <= set(StrategyParams.model_fields)
    for k, spec in space["parameters"].items():
        assert spec["min"] <= cfg["strategy"][k] <= spec["max"], k
        assert {"definition", "units", "rationale"} <= set(spec)
    assert inv["frozen"] is True and inv["rules"]["never_widen_stop"] is True and inv["rules"]["ai_enabled"] is False


def test_invalid_session_label_and_overlapping_phases_rejected(cfg: dict[str, Any]) -> None:
    bad = copy.deepcopy(cfg)
    bad["strategy"]["session_windows"]["trend"] = ["NEW_YORK"]  # Package 2 label, renamed to NY_RTH
    with pytest.raises(ConfigError):
        validate_config(bad)
    bad = copy.deepcopy(cfg)
    bad["research"]["phases"] = {
        "development": {"start": "2026-01-01", "end": "2026-02-01"},
        "validation": None,
        "oos": {"start": "2026-01-20", "end": "2026-03-01"},
    }
    with pytest.raises(ConfigError):
        validate_config(bad)


def test_alternate_config_dir(tmp_path: Path) -> None:
    d = tmp_path / "cfg"
    shutil.copytree(CONFIG_DIR, d, ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    risk = yaml.safe_load((d / "risk.yaml").read_text(encoding="utf-8"))
    risk["session_trade_cap"] = 2
    (d / "risk.yaml").write_text(yaml.safe_dump(risk), encoding="utf-8")
    cfg = load_config(d)
    assert cfg["risk"]["session_trade_cap"] == 2
    assert config_hash(cfg) != config_hash(load_config())
