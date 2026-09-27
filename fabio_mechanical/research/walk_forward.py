"""Rolling walk-forward: optimize on each training window, freeze, evaluate the next window.

Windows are built from trading-day-labelled sources; the validation window of each fold is
evaluated with parameters frozen from its own training window only (no peeking).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from fabio_mechanical.configs import apply_strategy_params, config_hash
from fabio_mechanical.ingest.sources import EventSource

from .metrics import compute_metrics
from .optimize import optimize
from .runner import run_dataset


def build_windows(
    sources: Sequence[EventSource], train_days: int, validation_days: int, step_days: int
) -> list[tuple[list[EventSource], list[EventSource]]]:
    ordered = sorted(sources, key=lambda s: (s.trading_day is None, s.trading_day))
    windows = []
    start = 0
    while start + train_days + validation_days <= len(ordered):
        train = list(ordered[start : start + train_days])
        val = list(ordered[start + train_days : start + train_days + validation_days])
        windows.append((train, val))
        start += step_days
    return windows


def walk_forward(
    base_cfg: dict[str, Any],
    sources: Sequence[EventSource],
    search_space: dict[str, Any],
    train_days: int,
    validation_days: int,
    step_days: int,
    max_candidates: int = 6,
    seed: int = 42,
    min_trades: int = 1,
    day_filter: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    reports = []
    for idx, (train, val) in enumerate(build_windows(sources, train_days, validation_days, step_days)):
        opt = optimize(base_cfg, train, search_space, "random", max_candidates, min_trades, seed + idx, day_filter=day_filter)
        selected = opt["selected"]
        cfg = base_cfg if not selected else apply_strategy_params(base_cfg, selected["params"], search_space)
        val_res = run_dataset(cfg, val, day_filter=day_filter)
        reports.append(
            {
                "window": idx,
                "train_days": [s.trading_day.isoformat() if s.trading_day else s.name for s in train],
                "validation_days": [s.trading_day.isoformat() if s.trading_day else s.name for s in val],
                "selected_params": selected["params"] if selected else {},
                "frozen_config_hash": config_hash(cfg),
                "train_metrics": selected["metrics"] if selected else {},
                "train_candidates": opt["candidates_evaluated"],
                "validation_metrics": compute_metrics(val_res.ledger, float(cfg["risk"]["initial_equity"])),
            }
        )
    agg = {
        "windows": len(reports),
        "validation_trades": sum(r["validation_metrics"]["trades"] for r in reports),
        "validation_net_pnl": sum(r["validation_metrics"]["net_pnl"] for r in reports),
    }
    return {"windows": reports, "aggregate": agg}
