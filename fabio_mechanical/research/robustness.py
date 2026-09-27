"""Robustness matrix (Package 2 scenarios on the event-driven pipeline).

* cost/latency grid: fee x slippage multipliers x latency (execution overrides only);
* threshold jitter: every search-space interpretation perturbed by up to +/-5% (seeded, clipped);
* level error: price inputs seen by the strategy shifted +/-1 tick (execution uses the true book);
* missed snapshots: 10% of decision updates dropped;
* session relabel: forces the session label (gating sensitivity);
* instrument: the same data valued as NQ or MNQ;
* tag breakdown: baseline trades split by session/setup/regime/volatility/news at entry.
"""

from __future__ import annotations

import copy
import random
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from fabio_mechanical.backtest.engine import SnapshotTransform
from fabio_mechanical.configs import apply_strategy_params, load_search_space, with_overrides
from fabio_mechanical.contracts import MarketFeatureSnapshot

from .datasets import DatasetLike
from .metrics import compute_metrics
from .runner import run_dataset


def _metrics(
    cfg: dict[str, Any], dataset: DatasetLike, transform: SnapshotTransform | None = None, day_filter: Callable[[str], bool] | None = None
) -> dict[str, Any]:
    res = run_dataset(cfg, dataset, transform=transform, day_filter=day_filter)
    return compute_metrics(res.ledger, float(cfg["risk"]["initial_equity"]))


def level_error_transform(ticks: int, seed: int) -> SnapshotTransform:
    rng = random.Random(seed)

    def t(s: MarketFeatureSnapshot) -> MarketFeatureSnapshot:
        shift = rng.choice((-ticks, ticks))
        upd: dict[str, Any] = {}
        for f in ("last_trade_ticks", "best_bid_ticks", "best_ask_ticks"):
            v = getattr(s, f)
            if v is not None:
                upd[f] = v + shift
        return s.model_copy(update=upd)

    return t


def drop_transform(prob: float, seed: int) -> SnapshotTransform:
    rng = random.Random(seed)

    def t(s: MarketFeatureSnapshot) -> MarketFeatureSnapshot | None:
        return None if rng.random() < prob else s

    return t


def session_transform(label: str) -> SnapshotTransform:
    def t(s: MarketFeatureSnapshot) -> MarketFeatureSnapshot:
        return s.model_copy(update={"session": label})

    return t


def jitter_config(cfg: dict[str, Any], space: dict[str, Any], frac: float, seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    params: dict[str, Any] = {}
    for k, spec in sorted(space["parameters"].items()):
        base = float(cfg["strategy"][k])
        v = base * (1 + rng.uniform(-frac, frac))
        v = max(float(spec["min"]), min(float(spec["max"]), v))
        params[k] = v
    return apply_strategy_params(cfg, params, space)


def robustness_matrix(
    base_cfg: dict[str, Any],
    dataset: DatasetLike,
    *,
    fee_multipliers: list[float] | None = None,
    slippage_multipliers: list[int] | None = None,
    latencies: list[int] | None = None,
    seed: int = 42,
    day_filter: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    r = base_cfg["research"]["robustness"]
    fee_multipliers = fee_multipliers or list(r["fee_multipliers"])
    slippage_multipliers = slippage_multipliers or list(r["slippage_multipliers"])
    latencies = latencies or list(r["latency_ms"])
    ex = base_cfg["execution"]
    scenarios: list[dict[str, Any]] = []
    for fm in fee_multipliers:
        for sm in slippage_multipliers:
            for lat in latencies:
                cfg = with_overrides(
                    base_cfg,
                    "execution",
                    commission_per_contract=float(Decimal(str(ex["commission_per_contract"])) * Decimal(str(fm))),
                    fee_per_contract=float(Decimal(str(ex["fee_per_contract"])) * Decimal(str(fm))),
                    default_slippage_ticks=int(ex["default_slippage_ticks"]) * int(sm),
                    latency_ms=int(lat),
                )
                scenarios.append(
                    {
                        "scenario": {"fees_x": fm, "slippage_x": sm, "latency_ms": lat},
                        "metrics": _metrics(cfg, dataset, day_filter=day_filter),
                    }
                )
    space = load_search_space()
    perturbations: list[tuple[str, dict[str, Any], SnapshotTransform | None]] = [
        ("threshold_jitter_5pct", jitter_config(base_cfg, space, 0.05, seed), None),
        ("level_error_plusminus_1_tick", base_cfg, level_error_transform(1, seed)),
        ("missed_snapshots_10pct", base_cfg, drop_transform(0.10, seed)),
        ("session_forced_NY_RTH", base_cfg, session_transform("NY_RTH")),
        ("session_forced_LONDON", base_cfg, session_transform("LONDON")),
    ]
    mnq = copy.deepcopy(base_cfg)
    mnq["data"]["instruments"]["NQ"] = dict(mnq["data"]["instruments"]["MNQ"])
    perturbations.append(("instrument_valued_as_MNQ", mnq, None))
    for name, cfg, transform in perturbations:
        scenarios.append({"scenario": {"name": name}, "metrics": _metrics(cfg, dataset, transform, day_filter)})
    baseline = run_dataset(base_cfg, dataset, day_filter=day_filter)
    return {"scenarios": scenarios, "baseline_metrics": compute_metrics(baseline.ledger), "tag_breakdown": tag_breakdown(baseline.ledger)}


def tag_breakdown(ledger: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for tag in ("session", "setup_type", "market_regime", "volatility_regime", "news_day", "trading_day"):
        groups: dict[str, list[dict[str, Any]]] = {}
        for x in ledger:
            groups.setdefault(str(x.get(tag, "unknown")), []).append(x)
        out[tag] = {k: compute_metrics(v) for k, v in sorted(groups.items())}
    return out
