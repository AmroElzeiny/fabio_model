"""Configuration package: YAML files plus loading/validation/hashing code."""

from .loader import (
    CONFIG_DIR,
    ConfigError,
    FrozenInvariantError,
    apply_strategy_params,
    canonical_hash,
    config_hash,
    invariant_hash,
    load_config,
    load_invariants,
    load_search_space,
    search_space_hash,
    validate_config,
    with_overrides,
)

__all__ = [
    "CONFIG_DIR",
    "ConfigError",
    "FrozenInvariantError",
    "apply_strategy_params",
    "canonical_hash",
    "config_hash",
    "invariant_hash",
    "load_config",
    "load_invariants",
    "load_search_space",
    "search_space_hash",
    "validate_config",
    "with_overrides",
]
