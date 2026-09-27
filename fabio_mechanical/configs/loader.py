"""Configuration loading, validation, hashing and the optimizer parameter gate."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .validation import (
    DataParams,
    ExecutionParams,
    Interpretation,
    Invariants,
    ResearchParams,
    RiskParams,
    SearchSpace,
    StrategyParams,
)

CONFIG_DIR = Path(__file__).resolve().parent
FILES = {
    "invariants": "fabio_invariants.yaml",
    "interpretation": "interpretation.yaml",
    "search_space": "search_space.yaml",
    "risk": "risk.yaml",
    "execution": "execution.yaml",
    "data": "data.yaml",
    "research": "research.yaml",
}


class ConfigError(ValueError):
    pass


class FrozenInvariantError(RuntimeError):
    """Raised when anything tries to change a frozen invariant or a non-tunable key."""


def load_yaml(path: str | Path) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping")
    return data


def canonical_hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _dir(config_dir: str | Path | None) -> Path:
    return Path(config_dir) if config_dir is not None else CONFIG_DIR


def load_invariants(config_dir: str | Path | None = None) -> dict[str, Any]:
    inv = load_yaml(_dir(config_dir) / FILES["invariants"])
    try:
        Invariants.model_validate(inv)
    except ValidationError as exc:
        raise ConfigError(f"invalid fabio_invariants.yaml: {exc}") from exc
    return inv


def load_search_space(config_dir: str | Path | None = None) -> dict[str, Any]:
    space = load_yaml(_dir(config_dir) / FILES["search_space"])
    try:
        SearchSpace.model_validate(space)
    except ValidationError as exc:
        raise ConfigError(f"invalid search_space.yaml: {exc}") from exc
    return space


def load_config(config_dir: str | Path | None = None) -> dict[str, Any]:
    """Return the effective (merged, validated) runtime configuration dictionary."""
    d = _dir(config_dir)
    cfg = load_yaml(d / FILES["interpretation"])
    for section in ("risk", "execution", "data", "research"):
        cfg[section] = load_yaml(d / FILES[section])
    validate_config(cfg, load_invariants(d), load_search_space(d))
    return cfg


def validate_config(
    cfg: dict[str, Any],
    invariants: dict[str, Any] | None = None,
    search_space: dict[str, Any] | None = None,
    *,
    allow_experimental: bool = False,
) -> None:
    interp = {k: cfg.get(k) for k in ("version", "AI_ENABLED", "strategy", "structure", "market", "experimental")}
    try:
        Interpretation.model_validate(interp)
        RiskParams.model_validate(cfg["risk"])
        ExecutionParams.model_validate(cfg["execution"])
        DataParams.model_validate(cfg["data"])
        ResearchParams.model_validate(cfg["research"])
    except (ValidationError, KeyError) as exc:
        raise ConfigError(f"invalid configuration: {exc}") from exc
    if cfg.get("AI_ENABLED") is not False:
        raise ConfigError("AI_ENABLED must be false in FABIO_MECHANICAL_V1")
    if not allow_experimental:
        on = [k for k, v in cfg["experimental"].items() if v]
        if on:
            raise ConfigError(f"experimental extensions must be OFF in the baseline: {on}")
    if invariants is not None and search_space is not None:
        validate_search_space(search_space, invariants, cfg)


def validate_search_space(space: dict[str, Any], invariants: dict[str, Any], cfg: dict[str, Any]) -> None:
    params = space["parameters"]
    tunable = set(StrategyParams.model_fields) - {"session_windows", "data_quality_min", "stale_data_ms_max"}
    unknown = set(params) - tunable
    if unknown:
        raise ConfigError(f"search space contains non-tunable keys: {sorted(unknown)}")
    frozen = set(invariants.get("rules", {})) | set(invariants)
    overlap = set(params) & frozen
    if overlap:
        raise ConfigError(f"search space overlaps frozen invariants: {sorted(overlap)}")
    for key, spec in params.items():
        baseline = cfg["strategy"][key]
        if not (float(spec["min"]) - 1e-12 <= float(baseline) <= float(spec["max"]) + 1e-12):
            raise ConfigError(f"baseline {key}={baseline} lies outside its search range")


def config_hash(cfg: dict[str, Any] | None = None) -> str:
    return canonical_hash(load_config() if cfg is None else cfg)


def invariant_hash(invariants: dict[str, Any] | None = None) -> str:
    return canonical_hash(load_invariants() if invariants is None else invariants)


def search_space_hash(space: dict[str, Any] | None = None) -> str:
    return canonical_hash(load_search_space() if space is None else space)


def apply_strategy_params(config: dict[str, Any], params: dict[str, Any], search_space: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a copy of ``config`` with whitelisted search-space dimensions applied.

    Frozen invariants, risk, execution, data and research settings can never be touched:
    any key outside ``search_space.yaml`` raises :class:`FrozenInvariantError`, as does any
    value outside the declared bounds.
    """
    space = search_space if search_space is not None else load_search_space()
    allowed = space["parameters"]
    unknown = set(params) - set(allowed)
    if unknown:
        raise FrozenInvariantError(f"Optimizer attempted non-tunable keys: {sorted(unknown)}")
    for key, value in params.items():
        spec = allowed[key]
        if not (float(spec["min"]) - 1e-9 <= float(value) <= float(spec["max"]) + 1e-9):
            raise FrozenInvariantError(f"{key}={value} outside search bounds [{spec['min']}, {spec['max']}]")
    out = copy.deepcopy(config)
    for key, value in params.items():
        current = out["strategy"][key]
        out["strategy"][key] = int(round(value)) if isinstance(current, int) and not isinstance(current, bool) else float(value)
    validate_config(out, allow_experimental=True)
    return out


def with_overrides(config: dict[str, Any], section: str, **values: Any) -> dict[str, Any]:
    """Explicit, validated override helper for research scenarios (never for invariants)."""
    if section not in {"execution", "risk", "research", "data", "structure", "market", "experimental"}:
        raise FrozenInvariantError(f"section {section} cannot be overridden this way")
    out = copy.deepcopy(config)
    out[section].update(values)
    validate_config(out, allow_experimental=True)
    return out
