"""Feature ablations (Package 2): remove one evidence family at a time via snapshot transforms."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fabio_mechanical.backtest.engine import SnapshotTransform
from fabio_mechanical.contracts import MarketFeatureSnapshot

from .datasets import DatasetLike
from .metrics import compute_metrics
from .runner import run_dataset

FEATURES = ["CVD", "Big Prints", "footprint imbalance", "absorption", "LVN-quality ranking", "acceptance filter", "DOM features"]


def ablation_transform(feature: str) -> SnapshotTransform:
    def t(s: MarketFeatureSnapshot) -> MarketFeatureSnapshot:
        upd: dict[str, Any] = {}
        if feature == "CVD":
            upd["cvd_slope_normalized"] = 0.0
        elif feature == "Big Prints":
            upd.update(big_buy_print_percentile=0.0, big_sell_print_percentile=0.0)
        elif feature == "footprint imbalance":
            upd["footprint"] = s.footprint.model_copy(
                update={"levels": [], "stacked_buy_imbalance_levels": 0, "stacked_sell_imbalance_levels": 0}
            )
        elif feature == "absorption":
            upd["absorption_score"] = 0.0
        elif feature == "LVN-quality ranking":
            if s.impulse_profile is not None:
                upd["impulse_profile"] = s.impulse_profile.model_copy(update={"lvn_zones": list(reversed(s.impulse_profile.lvn_zones))})
            if s.reclaim_profile is not None:
                upd["reclaim_profile"] = s.reclaim_profile.model_copy(update={"lvn_zones": list(reversed(s.reclaim_profile.lvn_zones))})
        elif feature == "acceptance filter":
            upd["auction"] = s.auction.model_copy(update={"acceptance_events": 0})
        elif feature == "DOM features":
            upd["pulling_stacking_score"] = 0.0
        else:
            raise KeyError(feature)
        return s.model_copy(update=upd)

    return t


def ablation_report(cfg: dict[str, Any], dataset: DatasetLike, day_filter: Callable[[str], bool] | None = None) -> dict[str, Any]:
    base = compute_metrics(run_dataset(cfg, dataset, day_filter=day_filter).ledger)
    rows = []
    for f in FEATURES:
        res = run_dataset(cfg, dataset, transform=ablation_transform(f), day_filter=day_filter)
        rows.append({"removed": f, "metrics": compute_metrics(res.ledger)})
    return {"baseline": base, "ablations": rows}
