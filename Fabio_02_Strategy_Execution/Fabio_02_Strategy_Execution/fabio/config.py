from __future__ import annotations
import copy, hashlib, json
from pathlib import Path
from typing import Any
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "configs"

class FrozenInvariantError(RuntimeError): pass


def load_yaml(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))

def load_config() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "interpretation.yaml")

def load_invariants() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "fabio_invariants.yaml")

def load_search_space() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "search_space.yaml")

def canonical_hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()

def config_hash(config: dict[str, Any] | None = None) -> str:
    config = load_config() if config is None else config
    return canonical_hash(config)

def invariant_hash() -> str:
    return canonical_hash(load_invariants())

def apply_strategy_params(config: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Apply only whitelisted search-space dimensions; invariants cannot be touched."""
    allowed = set(load_search_space()["parameters"])
    unknown = set(params) - allowed
    if unknown:
        raise FrozenInvariantError(f"Optimizer attempted non-tunable keys: {sorted(unknown)}")
    out = copy.deepcopy(config)
    out.setdefault("strategy", {}).update(params)
    return out
