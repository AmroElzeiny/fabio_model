"""Unified command line: ``fabio <group> <command>``."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fabio_mechanical import __version__

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


class CLIError(RuntimeError):
    def __init__(self, message: str, code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------- helpers


def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader (KEY=VALUE lines); never overrides variables already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def _print(obj: Any) -> None:
    from fabio_mechanical.reporting.export import _clean, _default

    print(json.dumps(_clean(obj), indent=2, default=_default, sort_keys=True))


def _cfg(args: argparse.Namespace) -> dict[str, Any]:
    from fabio_mechanical.configs import load_config

    return load_config(args.config_dir)


def _run_dir(args: argparse.Namespace, cfg: dict[str, Any], name: str) -> Path:
    if getattr(args, "output_dir", None):
        return Path(args.output_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(cfg["data"]["runs_dir"]) / f"{name}-{stamp}"


_DATE_RE = re.compile(r"(20\d{2})-?(\d{2})-?(\d{2})")


def _label_day(name: str) -> date | None:
    m = _DATE_RE.search(name)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _sources(args: argparse.Namespace, cfg: dict[str, Any]) -> list[Any]:
    from fabio_mechanical.ingest.sources import GeneratedSource, open_sources
    from fabio_mechanical.ingest.synthetic import SCENARIOS, scenario_events
    from fabio_mechanical.research.datasets import synthetic_dataset

    synthetic = getattr(args, "synthetic", None)
    days = getattr(args, "synthetic_days", None)
    if days:
        return synthetic_dataset(int(days))
    if synthetic:
        if synthetic not in SCENARIOS:
            raise CLIError(f"unknown synthetic scenario {synthetic}; choose from {sorted(SCENARIOS)}")
        return [GeneratedSource(factory=lambda: scenario_events(synthetic), spec={"scenario": synthetic}, name=f"synthetic:{synthetic}")]
    inputs = getattr(args, "input", None)
    if not inputs:
        raise CLIError("provide --input (JSONL / DBN files, directories or globs) or --synthetic SCENARIO")
    srcs = open_sources(inputs, definitions=getattr(args, "definitions", None), known_roots=tuple(cfg["data"]["known_contract_prefixes"]))
    for s in srcs:
        if s.trading_day is None:
            s.trading_day = _label_day(s.name)
    return srcs


def _dataset_manifest(sources: Sequence[Any]) -> dict[str, Any]:
    from fabio_mechanical.ingest.sources import combined_manifest

    return combined_manifest(list(sources))


def _checksum_guard(sources: Sequence[Any]) -> list[str]:
    bad = []
    for s in sources:
        check = getattr(s, "checksum_ok", None)
        if check is not None and check() is False:
            bad.append(s.name)
    return bad


def _engine_with_guards(cfg: dict[str, Any], sources: Sequence[Any], **kwargs: Any) -> Any:
    from fabio_mechanical.backtest import BacktestEngine

    engine = BacktestEngine(cfg, **kwargs)
    for name in _checksum_guard(sources):
        engine.replay.quality.mark_checksum_mismatch(f"manifest_checksum_mismatch:{name}")
    for s in sources:
        if hasattr(s, "on_invalid"):
            s.on_invalid = engine.replay.quality.mark_invalid_record
    return engine


def _news(args: argparse.Namespace, engine: Any) -> None:
    cal = getattr(args, "news_calendar", None)
    if cal:
        from fabio_mechanical.research.data_tools import load_news_days

        engine.replay.news_days = load_news_days(cal, engine.replay.calendar)


# ---------------------------------------------------------------------------- schema/config


def cmd_schema_hash(args: argparse.Namespace) -> int:
    from fabio_mechanical.contracts import CONTRACT_VERSION, canonical_schema_hash, pinned_schema_hash

    current = canonical_schema_hash()
    print(f"CONTRACT_VERSION={CONTRACT_VERSION}")
    print(f"CONTRACT_SCHEMA_SHA256={current}")
    if current != pinned_schema_hash():
        print(f"ERROR: pinned hash is {pinned_schema_hash()}", file=sys.stderr)
        return EXIT_FAIL
    return EXIT_OK


def cmd_schema_export(args: argparse.Namespace) -> int:
    from fabio_mechanical.contracts import export_schema_bundle

    print(export_schema_bundle(args.output) if args.output else export_schema_bundle())
    return EXIT_OK


def cmd_config_validate(args: argparse.Namespace) -> int:
    from fabio_mechanical.configs import config_hash, invariant_hash, search_space_hash

    cfg = _cfg(args)
    _print(
        {
            "valid": True,
            "config_hash": config_hash(cfg),
            "invariant_hash": invariant_hash(),
            "search_space_sha256": search_space_hash(),
            "ai_enabled": cfg["AI_ENABLED"],
            "experimental": cfg["experimental"],
        }
    )
    return EXIT_OK


def cmd_config_show(args: argparse.Namespace) -> int:
    _print(_cfg(args))
    return EXIT_OK


# ---------------------------------------------------------------------------- data


def _downloader(cfg: dict[str, Any]) -> Any:
    from fabio_mechanical.ingest.databento import DatabentoDownloader

    return DatabentoDownloader(dataset=cfg["data"]["dataset"])


def cmd_data_cost(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    dl = _downloader(cfg)
    total = 0.0
    results = []
    for schema in args.schema:
        est = dl.cost_estimate(symbols=args.symbol, schema=schema, start=args.start, end=args.end, stype_in=args.stype_in)
        total += est["estimated_cost_usd"]
        results.append(est)
    _print({"estimates": results, "total_estimated_cost_usd": round(total, 4), "note": "Nothing was downloaded."})
    return EXIT_OK


def cmd_data_download(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if args.max_cost is None and not args.yes:
        raise CLIError("refusing to download without a spending cap: pass --max-cost USD (recommended) or --yes")
    dl = _downloader(cfg)
    est = dl.cost_estimate(symbols=args.symbol, schema=args.schema, start=args.start, end=args.end, stype_in=args.stype_in)
    print("PRE-DOWNLOAD COST ESTIMATE")
    _print(est)
    if args.batch:
        paths = dl.download_batch(
            symbols=args.symbol,
            schema=args.schema,
            start=args.start,
            end=args.end,
            output_dir=Path(args.output),
            state_file=Path(args.state_file or (Path(args.output) / f".batch_resume_{args.schema}.json")),
            stype_in=args.stype_in,
            split_duration=args.split_duration,
            max_cost_usd=args.max_cost,
        )
        for p in paths:
            print(p)
    else:
        p = dl.download_stream(
            symbols=args.symbol,
            schema=args.schema,
            start=args.start,
            end=args.end,
            output=Path(args.output),
            stype_in=args.stype_in,
            retries=args.retries,
            max_cost_usd=args.max_cost,
            estimate=est,
        )
        print(p)
    return EXIT_OK


def cmd_data_resolve(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if args.definitions:
        from dataclasses import asdict

        from fabio_mechanical.ingest.databento import ContractResolver

        res = ContractResolver.from_definition_files(args.definitions, tuple(cfg["data"]["known_contract_prefixes"]))
        _print({"contracts": [asdict(c) for c in res.contracts.values()]})
        return EXIT_OK
    if not args.start:
        raise CLIError("pass --start (API resolution) or --definitions FILE (offline)")
    _print(_downloader(cfg).resolve_continuous(args.symbol, args.start, args.end))
    return EXIT_OK


def cmd_data_validate(args: argparse.Namespace) -> int:
    from fabio_mechanical.ingest.databento import ContractResolver, deep_validate, manifest_path, validate_manifest
    from fabio_mechanical.ingest.sources import expand_inputs, is_dbn, is_definition_file

    cfg = _cfg(args)
    paths = expand_inputs(args.path)
    defs = [Path(d) for d in (args.definitions or [])] or [p for p in paths if is_dbn(p) and is_definition_file(p)]
    resolver = ContractResolver.from_definition_files(defs, tuple(cfg["data"]["known_contract_prefixes"])) if defs and args.deep else None
    ok = True
    out = []
    for p in paths:
        row: dict[str, Any] = {"path": str(p), "exists": p.exists()}
        if not p.exists():
            ok = False
            out.append(row)
            continue
        row["checksum_manifest_present"] = manifest_path(p).exists()
        row["checksum_manifest_valid"] = validate_manifest(p) if row["checksum_manifest_present"] else None
        if row["checksum_manifest_valid"] is False:
            ok = False
        if args.deep and is_dbn(p) and not is_definition_file(p):
            rep = deep_validate(p, resolver, args.max_records)
            row["deep"] = rep
            ok = ok and rep["ok"]
        out.append(row)
    _print({"ok": ok, "files": out})
    return EXIT_OK if ok else EXIT_FAIL


def cmd_data_synthetic(args: argparse.Namespace) -> int:
    from fabio_mechanical.ingest.local import write_jsonl_events
    from fabio_mechanical.ingest.synthetic import SCENARIOS, scenario_events, write_dbn, write_definition_dbn

    if args.scenario not in SCENARIOS:
        raise CLIError(f"unknown scenario; choose from {sorted(SCENARIOS)}")
    out = Path(args.output)
    if args.format == "dbn":
        m = SCENARIOS[args.scenario]()
        write_dbn(m.records, out)
        defs = out.with_name(out.name.split(".")[0] + ".definition.dbn")
        write_definition_dbn(list(m.resolver().contracts.values()), defs, m.records[0]["ts_recv"])
        print(out)
        print(defs)
    else:
        print(write_jsonl_events(out, scenario_events(args.scenario)))
    return EXIT_OK


# ---------------------------------------------------------------------------- replay / features


def cmd_replay_run(args: argparse.Namespace) -> int:
    from fabio_mechanical.ingest.sources import chain_events
    from fabio_mechanical.replay import ReplayEngine

    cfg = _cfg(args)
    sources = _sources(args, cfg)
    eng = ReplayEngine(cfg)
    for name in _checksum_guard(sources):
        eng.quality.mark_checksum_mismatch(f"manifest_checksum_mismatch:{name}")
    t0 = time.perf_counter()
    snapshots = 0
    last = None
    for e in chain_events(sources):
        s = eng.process(e)
        if s is not None:
            snapshots += 1
            last = s
    dt = time.perf_counter() - t0
    q = eng.quality.flags
    out = {
        "events_processed": eng.events_processed,
        "trades_processed": eng.trades_processed,
        "snapshots": snapshots,
        "session_boundaries": eng.session_boundaries,
        "contract_rolls": eng.contract_rolls,
        "final_trade_safe": q.trade_safe,
        "data_quality_reasons": q.reasons,
        "ts_event_regressions": eng.quality.ts_event_regressions,
        "provider_warnings": eng.quality.provider_warnings,
        "book_initialized": eng.book.initialized,
        "last_snapshot": None
        if last is None
        else {
            "timestamp_utc": datetime.fromtimestamp(last.timestamp_ns / 1e9, tz=timezone.utc).isoformat(),
            "active_contract": last.active_contract,
            "session": last.session,
            "last_trade_ticks": last.last_trade_ticks,
            "balance": [last.auction.balance_low_ticks, last.auction.balance_poc_ticks, last.auction.balance_high_ticks],
        },
        "seconds": round(dt, 3),
        "events_per_second": round(eng.events_processed / dt) if dt > 0 else None,
        "dataset": _dataset_manifest(sources),
    }
    _print(out)
    return EXIT_OK if q.trade_safe else EXIT_FAIL


def cmd_features_build(args: argparse.Namespace) -> int:
    from fabio_mechanical.ingest.sources import chain_events
    from fabio_mechanical.replay import ReplayEngine
    from fabio_mechanical.reporting import export_snapshots_jsonl, export_snapshots_parquet

    cfg = _cfg(args)
    sources = _sources(args, cfg)
    eng = ReplayEngine(cfg)
    sample_ns = int(args.sample_ms) * 1_000_000 if args.sample_ms else 0
    state = {"next": 0, "n": 0}

    def snaps() -> Iterable[Any]:
        for e in chain_events(sources):
            s = eng.process(e)
            if s is None:
                continue
            if sample_ns and s.timestamp_ns < state["next"]:
                continue
            state["next"] = (s.timestamp_ns // sample_ns + 1) * sample_ns if sample_ns else 0
            state["n"] += 1
            yield s

    out = Path(args.output)
    if args.format == "parquet":
        export_snapshots_parquet(out, snaps())
    else:
        export_snapshots_jsonl(out, snaps(), compact=args.compact)
    _print(
        {
            "output": str(out),
            "snapshots_written": state["n"],
            "events_processed": eng.events_processed,
            "final_trade_safe": eng.quality.trade_safe,
        }
    )
    return EXIT_OK


# ---------------------------------------------------------------------------- backtest / report


def _phase_filter(args: argparse.Namespace, cfg: dict[str, Any], sources: Sequence[Any]) -> Callable[[str], bool] | None:
    phase = getattr(args, "phase", "any")
    if phase == "any":
        return None
    from fabio_mechanical.research.locking import check_sources, phase_filter, verify_oos_lock

    if phase == "oos":
        if not args.oos_lock:
            raise CLIError("OOS evaluation requires --oos-lock (created with `fabio research lock-oos` before opening OOS data)")
        verify_oos_lock(args.oos_lock, cfg)
    check_sources(cfg, sources, [phase])
    return phase_filter(cfg, [phase])


def cmd_backtest_run(args: argparse.Namespace) -> int:
    from fabio_mechanical.ingest.sources import chain_events
    from fabio_mechanical.journal import JsonlJournal
    from fabio_mechanical.reporting import run_manifest
    from fabio_mechanical.reporting.report import write_backtest_outputs

    cfg = _cfg(args)
    sources = _sources(args, cfg)
    out_dir = _run_dir(args, cfg, "backtest")
    out_dir.mkdir(parents=True, exist_ok=True)
    day_filter = _phase_filter(args, cfg, sources)
    journal = JsonlJournal(out_dir / "journal.jsonl")
    engine = _engine_with_guards(cfg, sources, journal=journal, day_filter=day_filter)
    _news(args, engine)
    t0 = time.perf_counter()
    result = engine.run(chain_events(sources))
    manifest = run_manifest(
        "backtest run", cfg, _dataset_manifest(sources), {"phase": args.phase, "seconds": round(time.perf_counter() - t0, 3)}
    )
    paths = write_backtest_outputs(result, out_dir, manifest)
    _print({"run_dir": str(out_dir), "summary": result.summary, "files": {k: str(v) for k, v in paths.items()}})
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    from fabio_mechanical.reporting.report import write_report

    print(write_report(args.run_dir))
    return EXIT_OK


# ---------------------------------------------------------------------------- research


def _space(args: argparse.Namespace) -> dict[str, Any]:
    from fabio_mechanical.configs import load_search_space

    space = load_search_space(args.config_dir)
    dims = getattr(args, "dims", None)
    if dims:
        keys = [d.strip() for d in dims.split(",") if d.strip()]
        unknown = set(keys) - set(space["parameters"])
        if unknown:
            raise CLIError(f"unknown search dimensions {sorted(unknown)}")
        space = {"parameters": {k: space["parameters"][k] for k in keys}}
    return space


def _research_filter(args: argparse.Namespace, cfg: dict[str, Any], sources: Sequence[Any]) -> Callable[[str], bool] | None:
    from fabio_mechanical.research.locking import check_sources, phase_filter

    if getattr(args, "synthetic_days", None) or getattr(args, "synthetic", None):
        return None  # synthetic smoke datasets carry no real calendar
    check_sources(cfg, sources, ["development"])
    return phase_filter(cfg, ["development"])


def cmd_research_freeze_baseline(args: argparse.Namespace) -> int:
    from fabio_mechanical.data.manifest import code_identity
    from fabio_mechanical.research.locking import freeze_baseline
    from fabio_mechanical.research.runner import run_dataset

    cfg = _cfg(args)
    sources = _sources(args, cfg)
    res = run_dataset(cfg, sources, day_filter=_research_filter(args, cfg, sources))
    body = freeze_baseline(
        args.output, cfg, {**res.summary, "metrics": res.metrics}, _dataset_manifest(sources)["dataset_sha256"], code_identity()
    )
    _print(body)
    return EXIT_OK


def cmd_research_optimize(args: argparse.Namespace) -> int:
    from fabio_mechanical.reporting import export_json, run_manifest
    from fabio_mechanical.research.locking import require_frozen_baseline
    from fabio_mechanical.research.optimize import optimize

    cfg = _cfg(args)
    require_frozen_baseline(args.baseline, cfg)
    sources = _sources(args, cfg)
    day_filter = _research_filter(args, cfg, sources)
    r = cfg["research"]

    def progress(i: int, n: int, row: dict[str, Any]) -> None:
        print(f"candidate {i}/{n} trades={row['metrics']['trades']} score={row['score']:.4f}", file=sys.stderr)

    res = optimize(
        cfg,
        sources,
        _space(args),
        args.mode or r["optimizer"]["mode"],
        args.max_candidates or r["optimizer"]["max_candidates"],
        args.min_trades if args.min_trades is not None else r["min_trades"],
        args.seed if args.seed is not None else r["deterministic_seed"],
        r["optimizer"]["plateau_tolerance"],
        day_filter=day_filter,
        progress=progress,
    )
    res["manifest"] = run_manifest("research optimize", cfg, _dataset_manifest(sources))
    out = Path(args.output)
    export_json(out, res)
    sel = res["selected"]
    _print(
        {
            "output": str(out),
            "candidates_evaluated": res["candidates_evaluated"],
            "eligible": res["candidates_eligible"],
            "selected_params": sel["params"] if sel else None,
            "plateau_size": len(res["plateau"]),
        }
    )
    return EXIT_OK


def cmd_research_walk_forward(args: argparse.Namespace) -> int:
    from fabio_mechanical.reporting import export_json, run_manifest
    from fabio_mechanical.research.locking import require_frozen_baseline
    from fabio_mechanical.research.walk_forward import walk_forward

    cfg = _cfg(args)
    require_frozen_baseline(args.baseline, cfg)
    sources = _sources(args, cfg)
    day_filter = _research_filter(args, cfg, sources)
    w = cfg["research"]["walk_forward"]
    res = walk_forward(
        cfg,
        sources,
        _space(args),
        args.train_days or w["train_days"],
        args.validation_days or w["validation_days"],
        args.step_days or w["step_days"],
        args.max_candidates or w["max_candidates"],
        cfg["research"]["deterministic_seed"],
        cfg["research"]["min_trades"] if args.min_trades is None else args.min_trades,
        day_filter=day_filter,
    )
    res["manifest"] = run_manifest("research walk-forward", cfg, _dataset_manifest(sources))
    export_json(args.output, res)
    _print({"output": args.output, "aggregate": res["aggregate"]})
    return EXIT_OK


def cmd_research_robustness(args: argparse.Namespace) -> int:
    from fabio_mechanical.reporting import export_json, run_manifest
    from fabio_mechanical.research.robustness import robustness_matrix

    cfg = _cfg(args)
    sources = _sources(args, cfg)
    kw: dict[str, Any] = {}
    if args.quick:
        kw = {"fee_multipliers": [1, 2], "slippage_multipliers": [1, 2], "latencies": [10, 100]}
    res = robustness_matrix(cfg, sources, seed=cfg["research"]["deterministic_seed"], day_filter=_research_filter(args, cfg, sources), **kw)
    res["manifest"] = run_manifest("research robustness", cfg, _dataset_manifest(sources))
    export_json(args.output, res)
    _print({"output": args.output, "scenarios": len(res["scenarios"]), "baseline_trades": res["baseline_metrics"]["trades"]})
    return EXIT_OK


def cmd_research_ablation(args: argparse.Namespace) -> int:
    from fabio_mechanical.reporting import export_json
    from fabio_mechanical.research.ablation import ablation_report

    cfg = _cfg(args)
    sources = _sources(args, cfg)
    res = ablation_report(cfg, sources, day_filter=_research_filter(args, cfg, sources))
    export_json(args.output, res)
    _print({"output": args.output, "baseline_trades": res["baseline"]["trades"], "ablations": len(res["ablations"])})
    return EXIT_OK


def cmd_research_monte_carlo(args: argparse.Namespace) -> int:
    import csv

    from fabio_mechanical.reporting import export_json
    from fabio_mechanical.research.monte_carlo import monte_carlo

    cfg = _cfg(args)
    with Path(args.trades).open("r", encoding="utf-8") as f:
        pnls = [float(r["net_pnl"]) for r in csv.DictReader(f)]
    mc = cfg["research"]["monte_carlo"]
    res = monte_carlo(
        pnls,
        args.iterations or mc["iterations"],
        args.seed if args.seed is not None else cfg["research"]["deterministic_seed"],
        mc["block_size"],
        float(cfg["risk"]["initial_equity"]),
    )
    if args.output:
        export_json(args.output, res)
    _print(res)
    return EXIT_OK


def cmd_research_lock_oos(args: argparse.Namespace) -> int:
    from fabio_mechanical.data.manifest import code_identity
    from fabio_mechanical.research.locking import freeze_for_oos

    cfg = _cfg(args)
    ds = ""
    if args.input or args.synthetic or args.synthetic_days:
        ds = _dataset_manifest(_sources(args, cfg))["dataset_sha256"]
    code = code_identity()
    m = freeze_for_oos(args.output, cfg, code.get("git_commit") or code["source_sha256"], dataset_sha256=ds)
    _print(m.__dict__)
    return EXIT_OK


def cmd_research_verify_oos(args: argparse.Namespace) -> int:
    from fabio_mechanical.research.locking import verify_oos_lock

    cfg = _cfg(args)
    verify_oos_lock(args.lock, cfg)
    _print({"lock": args.lock, "verified": True})
    return EXIT_OK


# ---------------------------------------------------------------------------- self-check / bench


def cmd_self_check(args: argparse.Namespace) -> int:
    from fabio_mechanical.backtest import BacktestEngine
    from fabio_mechanical.configs import FrozenInvariantError, apply_strategy_params, load_search_space
    from fabio_mechanical.contracts import verify_schema_pin
    from fabio_mechanical.decision import AIDisabledError, AIProviderStub
    from fabio_mechanical.ingest.sources import GeneratedSource
    from fabio_mechanical.ingest.synthetic import scenario_events
    from fabio_mechanical.reporting import export_json, run_manifest
    from fabio_mechanical.reporting.report import write_backtest_outputs
    from fabio_mechanical.research.ablation import ablation_report
    from fabio_mechanical.research.datasets import synthetic_dataset
    from fabio_mechanical.research.monte_carlo import monte_carlo
    from fabio_mechanical.research.optimize import optimize
    from fabio_mechanical.research.robustness import robustness_matrix
    from fabio_mechanical.research.runner import run_dataset
    from fabio_mechanical.research.walk_forward import walk_forward

    cfg = _cfg(args)
    out = _run_dir(args, cfg, "self-check")
    out.mkdir(parents=True, exist_ok=True)
    checks: dict[str, Any] = {"schema_sha256": verify_schema_pin()}
    src = GeneratedSource(factory=lambda: scenario_events("trend_long"), spec={"scenario": "trend_long"}, name="synthetic:trend_long")
    res = BacktestEngine(cfg).run(src.iter_events())
    write_backtest_outputs(res, out / "backtest_trend_long", run_manifest("self-check backtest", cfg, src.manifest()))
    checks["backtest_trades"] = len(res.ledger)
    checks["backtest_net_pnl"] = res.summary["net_pnl"]
    space = load_search_space()
    small = {"parameters": {k: space["parameters"][k] for k in ("aggression_delta_zscore", "stop_buffer_ticks")}}
    mr = [GeneratedSource(factory=lambda: scenario_events("mean_reversion_short"), spec={"scenario": "mean_reversion_short"}, name="mr")]
    opt = optimize(cfg, mr, small, "grid", 4, 1, 42)
    export_json(out / "optimization.json", opt)
    checks["optimization_candidates"] = opt["candidates_evaluated"]
    days = synthetic_dataset(3, mix=("mean_reversion_short", "mean_reversion_long", "quiet_balance"))
    wfo = walk_forward(cfg, days, small, 1, 1, 1, 2, 42)
    export_json(out / "walk_forward.json", wfo)
    checks["walk_forward_windows"] = wfo["aggregate"]["windows"]
    rob = robustness_matrix(cfg, mr, fee_multipliers=[1], slippage_multipliers=[1, 2], latencies=[10])
    export_json(out / "robustness.json", rob)
    checks["robustness_scenarios"] = len(rob["scenarios"])
    mc = monte_carlo([float(x["net_pnl"]) for x in run_dataset(cfg, days).ledger] or [0.0], 200, 42)
    export_json(out / "monte_carlo.json", mc)
    checks["monte_carlo_iterations"] = mc["iterations"]
    abl = ablation_report(cfg, mr)
    export_json(out / "ablation.json", abl)
    checks["ablations"] = len(abl["ablations"])
    try:
        AIProviderStub().decide(None, None)  # type: ignore[arg-type]
        checks["ai_disabled"] = False
    except AIDisabledError:
        checks["ai_disabled"] = cfg["AI_ENABLED"] is False
    try:
        apply_strategy_params(cfg, {"never_widen_stop": False})
        checks["optimizer_cannot_edit_invariants"] = False
    except FrozenInvariantError:
        checks["optimizer_cannot_edit_invariants"] = True
    export_json(out / "self_check.json", checks)
    _print({"output_dir": str(out), **checks})
    ok = checks["ai_disabled"] and checks["optimizer_cannot_edit_invariants"] and checks["backtest_trades"] >= 1
    return EXIT_OK if ok else EXIT_FAIL


def cmd_bench(args: argparse.Namespace) -> int:
    import tracemalloc

    from fabio_mechanical.backtest import BacktestEngine
    from fabio_mechanical.ingest.synthetic import SyntheticMBO, utc_ns
    from fabio_mechanical.journal import NullJournal
    from fabio_mechanical.replay import ReplayEngine

    cfg = _cfg(args)
    m = SyntheticMBO(seed=3)
    m.set_time(utc_ns(2026, 1, 13, 14, 30))
    m.seed_book(80160)
    for _ in range(max(1, args.minutes)):
        m.balance(80160, 12, 60, step_s=0.5)
        for _ in range(args.book_noise):
            m.noise()  # add/cancel activity away from the touch (real MBO is mostly book updates)
    events = list(m.events())
    trades = sum(1 for e in events if e.action.value == "TRADE")
    t0 = time.perf_counter()
    eng = ReplayEngine(cfg)
    snaps = sum(1 for e in events if eng.process(e) is not None)
    t1 = time.perf_counter()
    BacktestEngine(cfg, journal=NullJournal()).run(iter(events))
    t2 = time.perf_counter()
    tracemalloc.start()
    eng2 = ReplayEngine(cfg)
    for e in events:
        eng2.process(e)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    _print(
        {
            "events": len(events),
            "trade_records": trades,
            "trade_record_share": round(trades / len(events), 3),
            "snapshots": snaps,
            "replay_seconds": round(t1 - t0, 3),
            "replay_events_per_second": round(len(events) / (t1 - t0)),
            "replay_snapshots_per_second": round(snaps / (t1 - t0)),
            "backtest_seconds": round(t2 - t1, 3),
            "backtest_events_per_second": round(len(events) / (t2 - t1)),
            "peak_traced_mb_replay_state": round(peak / 1e6, 1),
            "note": "synthetic MBO; throughput depends mostly on the number of trade packets (one snapshot each)",
        }
    )
    return EXIT_OK


# ---------------------------------------------------------------------------- parser


def _add_inputs(q: argparse.ArgumentParser, synthetic_days: bool = False) -> None:
    q.add_argument("--input", nargs="+", help="JSONL MarketEvent files or Databento .dbn/.dbn.zst files, dirs or globs")
    q.add_argument("--definitions", nargs="+", help="Databento 'definition' schema files (auto-detected next to the data)")
    q.add_argument("--synthetic", help="use a synthetic raw-MBO scenario instead of files")
    if synthetic_days:
        q.add_argument("--synthetic-days", type=int, help="use N synthetic scenario days (smoke tests)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fabio", description="Fabio Mechanical: CME NQ MBO replay + mechanical Fabio strategy research (no AI)."
    )
    p.add_argument("--version", action="version", version=f"fabio-mechanical {__version__}")
    p.add_argument("--config-dir", help="alternate configs directory (copy of fabio_mechanical/configs)")
    top = p.add_subparsers(dest="group", required=True)

    g = top.add_parser("schema", help="contract schema").add_subparsers(dest="cmd", required=True)
    q = g.add_parser("hash", help="print CONTRACT_VERSION and canonical schema SHA256")
    q.set_defaults(func=cmd_schema_hash)
    q = g.add_parser("export", help="write the canonical JSON schema bundle")
    q.add_argument("--output")
    q.set_defaults(func=cmd_schema_export)

    g = top.add_parser("config", help="configuration").add_subparsers(dest="cmd", required=True)
    g.add_parser("validate", help="validate all configs and print hashes").set_defaults(func=cmd_config_validate)
    g.add_parser("show", help="print the effective merged configuration").set_defaults(func=cmd_config_show)

    g = top.add_parser("data", help="Databento acquisition and validation").add_subparsers(dest="cmd", required=True)
    for name, func in (("cost", cmd_data_cost), ("download", cmd_data_download)):
        q = g.add_parser(name, help="cost estimate BEFORE download" if name == "cost" else "download (cost printed first, capped)")
        q.add_argument("--symbol", default="NQ.v.0")
        schemas = ["mbo", "definition", "trades", "mbp-10"]
        if name == "cost":
            q.add_argument("--schema", nargs="+", default=["mbo"], choices=schemas)
        else:
            q.add_argument("--schema", default="mbo", choices=schemas)
        q.add_argument("--start", required=True, help="ISO start (UTC), e.g. 2026-01-06T00:00")
        q.add_argument("--end", required=True, help="ISO end (UTC, exclusive)")
        q.add_argument("--stype-in", default="continuous")
        if name == "download":
            q.add_argument("--output", required=True, help="file path (stream) or directory (--batch)")
            q.add_argument("--max-cost", type=float, help="abort if the estimate exceeds this USD amount")
            q.add_argument("--yes", action="store_true", help="download without a cap (not recommended)")
            q.add_argument("--retries", type=int, default=3)
            q.add_argument("--batch", action="store_true")
            q.add_argument("--state-file")
            q.add_argument("--split-duration", default="day")
        q.set_defaults(func=func)
    q = g.add_parser("resolve", help="resolve the continuous symbol to real contracts")
    q.add_argument("--symbol", default="NQ.v.0")
    q.add_argument("--start")
    q.add_argument("--end")
    q.add_argument("--definitions", nargs="+", help="offline: list contracts in definition files")
    q.set_defaults(func=cmd_data_resolve)
    q = g.add_parser("validate", help="checksum manifests and (with --deep) structural scan")
    q.add_argument("path", nargs="+")
    q.add_argument("--deep", action="store_true")
    q.add_argument("--definitions", nargs="+")
    q.add_argument("--max-records", type=int)
    q.set_defaults(func=cmd_data_validate)
    q = g.add_parser("synthetic", help="write a synthetic raw-MBO scenario (JSONL events or DBN + definitions)")
    q.add_argument("--scenario", required=True)
    q.add_argument("--output", required=True)
    q.add_argument("--format", choices=["jsonl", "dbn"], default="jsonl")
    q.set_defaults(func=cmd_data_synthetic)

    g = top.add_parser("replay", help="event-by-event replay").add_subparsers(dest="cmd", required=True)
    q = g.add_parser("run", help="replay and report data quality / throughput")
    _add_inputs(q)
    q.set_defaults(func=cmd_replay_run)

    g = top.add_parser("features", help="MarketFeatureSnapshot export").add_subparsers(dest="cmd", required=True)
    q = g.add_parser("build", help="replay and stream snapshots to JSONL/Parquet")
    _add_inputs(q)
    q.add_argument("--output", required=True)
    q.add_argument("--format", choices=["jsonl", "parquet"], default="jsonl")
    q.add_argument("--sample-ms", type=int, default=0, help="write at most one snapshot per interval (first after each boundary)")
    q.add_argument("--compact", action="store_true", help="omit per-price volume maps in the export")
    q.set_defaults(func=cmd_features_build)

    g = top.add_parser("backtest", help="Pure Fabio mechanical backtest").add_subparsers(dest="cmd", required=True)
    q = g.add_parser("run", help="event-driven backtest with full journal/exports")
    _add_inputs(q)
    q.add_argument("--output-dir")
    q.add_argument("--phase", choices=["any", "development", "validation", "oos"], default="any")
    q.add_argument("--oos-lock", help="lock manifest required for --phase oos")
    q.add_argument("--news-calendar", help="optional CSV (event,timestamp_utc,source,notes) for news_day tags")
    q.set_defaults(func=cmd_backtest_run)

    q = top.add_parser("report", help="(re)build REPORT.md for a run directory")
    q.add_argument("--run-dir", required=True)
    q.set_defaults(func=cmd_report)

    g = top.add_parser("research", help="scientific research workflow").add_subparsers(dest="cmd", required=True)
    q = g.add_parser("freeze-baseline", help="run and freeze the Pure Fabio baseline (required before optimization)")
    _add_inputs(q, True)
    q.add_argument("--output", required=True)
    q.set_defaults(func=cmd_research_freeze_baseline)
    q = g.add_parser("optimize", help="reproducible search over search_space.yaml (development data only)")
    _add_inputs(q, True)
    q.add_argument("--baseline", required=True)
    q.add_argument("--output", required=True)
    q.add_argument("--mode", choices=["grid", "random"])
    q.add_argument("--max-candidates", type=int)
    q.add_argument("--min-trades", type=int)
    q.add_argument("--seed", type=int)
    q.add_argument("--dims", help="comma-separated subset of search dimensions")
    q.set_defaults(func=cmd_research_optimize)
    q = g.add_parser("walk-forward", help="rolling optimize -> freeze -> validate")
    _add_inputs(q, True)
    q.add_argument("--baseline", required=True)
    q.add_argument("--output", required=True)
    q.add_argument("--train-days", type=int)
    q.add_argument("--validation-days", type=int)
    q.add_argument("--step-days", type=int)
    q.add_argument("--max-candidates", type=int)
    q.add_argument("--min-trades", type=int)
    q.add_argument("--dims")
    q.set_defaults(func=cmd_research_walk_forward)
    q = g.add_parser("robustness", help="cost/latency grid and perturbations")
    _add_inputs(q, True)
    q.add_argument("--output", required=True)
    q.add_argument("--quick", action="store_true", help="reduced cost/latency grid")
    q.set_defaults(func=cmd_research_robustness)
    q = g.add_parser("ablation", help="remove one evidence family at a time")
    _add_inputs(q, True)
    q.add_argument("--output", required=True)
    q.set_defaults(func=cmd_research_ablation)
    q = g.add_parser("monte-carlo", help="bootstrap the trade sequence of a run")
    q.add_argument("--trades", required=True, help="trades.csv from a backtest run")
    q.add_argument("--iterations", type=int)
    q.add_argument("--seed", type=int)
    q.add_argument("--output")
    q.set_defaults(func=cmd_research_monte_carlo)
    q = g.add_parser("lock-oos", help="write-once OOS lock manifest (before opening OOS data)")
    _add_inputs(q, True)
    q.add_argument("--output", required=True)
    q.set_defaults(func=cmd_research_lock_oos)
    q = g.add_parser("verify-oos", help="verify the current config against an OOS lock")
    q.add_argument("--lock", required=True)
    q.set_defaults(func=cmd_research_verify_oos)

    q = top.add_parser("self-check", help="synthetic end-to-end smoke of every subsystem")
    q.add_argument("--output-dir")
    q.set_defaults(func=cmd_self_check)
    q = top.add_parser("bench", help="small replay/backtest throughput + memory benchmark")
    q.add_argument("--minutes", type=int, default=20)
    q.add_argument("--book-noise", type=int, default=600, help="extra add/cancel pairs per simulated minute")
    q.set_defaults(func=cmd_bench)
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except CLIError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except Exception as exc:  # user-facing boundary: known operational errors without tracebacks
        from fabio_mechanical.configs import ConfigError, FrozenInvariantError
        from fabio_mechanical.ingest.databento import CostLimitExceeded, DatabentoUnavailable
        from fabio_mechanical.research.locking import OOSLockError, ProtocolError

        known = (
            DatabentoUnavailable,
            CostLimitExceeded,
            ConfigError,
            FrozenInvariantError,
            OOSLockError,
            ProtocolError,
            FileNotFoundError,
            FileExistsError,
            KeyError,
        )
        if isinstance(exc, known):
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return EXIT_USAGE
        raise


if __name__ == "__main__":
    raise SystemExit(main())
