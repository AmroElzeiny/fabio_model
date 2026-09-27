"""Reproducible parameter search over the whitelisted search space (Package 2).

* Only ``search_space.yaml`` dimensions can change (``apply_strategy_params``); invariants,
  risk, execution and data settings are untouchable.
* Candidate generation is fully determined by (search-space hash, mode, max_candidates, seed);
  the baseline parameter set is always candidate #0.
* EVERY candidate is retained in the output with its raw metrics; selection is an explicit,
  documented multi-metric score (never net profit alone) and the plateau (all candidates within
  the tolerance of the best score) plus per-parameter sensitivity are reported so a sharp,
  isolated optimum is visible as such. Nothing is selected by hand.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Callable
from typing import Any

from fabio_mechanical.configs import apply_strategy_params, config_hash, load_search_space, search_space_hash

from .datasets import DatasetLike
from .metrics import compute_metrics
from .runner import run_dataset

DEFAULT_WEIGHTS = {"expectancy": 0.30, "profit_factor": 0.20, "sharpe": 0.15, "calmar": 0.15, "max_drawdown": -0.20}


def explicit_score(m: dict[str, Any], weights: dict[str, float] = DEFAULT_WEIGHTS) -> float:
    """Transparent, bounded transformation of raw metrics (all raw metrics are also retained)."""

    def cap(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, float(v)))

    vals = {
        "expectancy": math.tanh(float(m["expectancy"]) / 100.0),
        "profit_factor": math.tanh(cap(m["profit_factor"], 0, 10) / 3),
        "sharpe": math.tanh(float(m["sharpe"]) / 3),
        "calmar": math.tanh(cap(m["calmar"], -10, 10) / 3),
        "max_drawdown": cap(m["max_drawdown"], 0, 1),
    }
    return sum(weights[k] * vals[k] for k in weights)


def _values(spec: dict[str, Any]) -> list[float]:
    vals = []
    x = float(spec["min"])
    step = float(spec["step"])
    mx = float(spec["max"])
    while x <= mx + 1e-12:
        vals.append(round(x, 10))
        x += step
    return vals


def generate_candidates(
    space: dict[str, Any], mode: str = "grid", max_candidates: int = 20, seed: int = 42, baseline: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    lists = [(k, _values(v)) for k, v in sorted(space["parameters"].items())]
    out: list[dict[str, Any]] = []
    if baseline is not None:
        out.append({k: baseline[k] for k, _ in lists})
    if mode == "grid" and len(lists) <= 4:
        for combo in itertools.product(*[v for _, v in lists]):
            cand = dict(zip([k for k, _ in lists], combo, strict=True))
            if cand not in out:
                out.append(cand)
            if len(out) >= max_candidates:
                break
        return out
    rng = random.Random(seed)
    attempts = 0
    while len(out) < max_candidates and attempts < max_candidates * 50:
        attempts += 1
        cand = {k: rng.choice(vals) for k, vals in lists}
        if cand not in out:
            out.append(cand)
    return out


def parameter_sensitivity(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Mean score per value of each parameter (marginal view of the search surface)."""
    out: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        if not r["eligible"]:
            continue
        for k, v in r["params"].items():
            out.setdefault(k, {}).setdefault(str(v), []).append(r["score"])
    return {k: {v: sum(s) / len(s) for v, s in sorted(d.items())} for k, d in out.items()}


def optimize(
    base_cfg: dict[str, Any],
    dataset: DatasetLike,
    search_space: dict[str, Any] | None = None,
    mode: str = "random",
    max_candidates: int = 12,
    min_trades: int = 1,
    seed: int = 42,
    plateau_tolerance: float = 0.05,
    day_filter: Callable[[str], bool] | None = None,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    space = search_space or load_search_space()
    baseline = {k: base_cfg["strategy"][k] for k in space["parameters"]}
    candidates = generate_candidates(space, mode, max_candidates, seed, baseline=baseline)
    rows = []
    for i, params in enumerate(candidates):
        cfg = apply_strategy_params(base_cfg, params, space)
        res = run_dataset(cfg, dataset, day_filter=day_filter)
        m = compute_metrics(res.ledger, float(cfg["risk"]["initial_equity"]))
        eligible = m["trades"] >= min_trades
        row = {
            "index": i,
            "is_baseline": i == 0,
            "params": params,
            "config_hash": config_hash(cfg),
            "metrics": m,
            "eligible": eligible,
            "score": explicit_score(m) if eligible else float("-inf"),
        }
        rows.append(row)
        if progress:
            progress(i + 1, len(candidates), row)
    eligible = [r for r in rows if r["eligible"]]
    eligible.sort(key=lambda r: (r["score"], -r["metrics"]["max_drawdown"], -r["index"]), reverse=True)
    selected = eligible[0] if eligible else None
    plateau: list[dict[str, Any]] = []
    if selected:
        floor = selected["score"] - max(abs(selected["score"]) * plateau_tolerance, 0.01)
        plateau = [r for r in eligible if r["score"] >= floor]
    return {
        "selection_method": "explicit multi-metric weighted score; all candidates and raw metrics retained",
        "weights": DEFAULT_WEIGHTS,
        "mode": mode,
        "seed": seed,
        "max_candidates": max_candidates,
        "min_trades": min_trades,
        "search_space_sha256": search_space_hash(space),
        "base_config_hash": config_hash(base_cfg),
        "candidates_evaluated": len(rows),
        "candidates_eligible": len(eligible),
        "selected": selected,
        "plateau": plateau,
        "plateau_tolerance": plateau_tolerance,
        "parameter_sensitivity": parameter_sensitivity(rows),
        "candidates": rows,
    }
