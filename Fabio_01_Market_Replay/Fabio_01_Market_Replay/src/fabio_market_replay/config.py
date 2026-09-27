from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


@dataclass(slots=True)
class EngineConfig:
    tick_size: float = 0.25
    top_n_depth: int = 10
    value_area_fraction: float = 0.70
    hvn_quantile: float = 0.75
    lvn_quantile: float = 0.25
    imbalance_ratio: float = 3.0
    stacked_levels: int = 3
    big_print_percentile: float = 0.95
    rolling_trade_window: int = 50
    stale_ns: int = 5_000_000_000
    max_sequence_gap: int = 1_000_000
    session_name: str = "CME_GLOBEX"
    ny_session_start: str = "09:30"
    ny_session_end: str = "16:00"
    timezone: str = "America/New_York"
    absorption_price_progress_floor_ticks: float = 1.0
    absorption_ratio_scale: float = 20.0
    iceberg_replenishment_scale: float = 10.0
    known_contract_prefixes: tuple[str, ...] = ("NQ", "MNQ")
    market_state: dict[str, float] = field(default_factory=lambda: {
        "balance_efficiency_max": 0.35,
        "imbalance_efficiency_min": 0.60,
        "range_expansion_min": 1.20,
        "delta_persistence_min": 0.60,
    })

    @classmethod
    def from_file(cls, path: str | Path) -> "EngineConfig":
        p = Path(path)
        raw = p.read_text(encoding="utf-8")
        data: dict[str, Any]
        if p.suffix.lower() in {".yaml", ".yml"}:
            if yaml is None:
                raise RuntimeError("PyYAML is required to read YAML config")
            data = yaml.safe_load(raw) or {}
        else:
            data = json.loads(raw)
        if "known_contract_prefixes" in data:
            data["known_contract_prefixes"] = tuple(data["known_contract_prefixes"])
        return cls(**data)
