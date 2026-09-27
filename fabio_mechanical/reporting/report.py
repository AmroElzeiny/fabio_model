"""Backtest output bundle and Markdown run report."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from fabio_mechanical.backtest import BacktestResult

from .export import export_json, export_rows_csv, utc_iso


def write_backtest_outputs(result: BacktestResult, out_dir: str | Path, manifest: dict[str, Any]) -> dict[str, Path]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    paths = {
        "manifest": export_json(d / "manifest.json", manifest),
        "summary": export_json(d / "summary.json", result.summary),
        "metrics": export_json(d / "metrics.json", result.metrics),
        "counters": export_json(d / "counters.json", result.counters),
        "trades": export_rows_csv(d / "trades.csv", result.ledger, ("entry_time_ns", "exit_time_ns")),
        "fills": export_rows_csv(d / "fills.csv", result.fills, ("timestamp_ns",)),
        "state_transitions": export_rows_csv(d / "state_transitions.csv", result.state_transitions, ("timestamp_ns",)),
        "candidates": export_rows_csv(d / "candidates.csv", result.candidates, ("timestamp_ns",)),
        "equity_curve": export_rows_csv(d / "equity_curve.csv", result.equity_curve, ("timestamp_ns",)),
    }
    if result.journal is not None:
        jp = d / "journal.jsonl"
        with jp.open("w", encoding="utf-8", newline="\n") as f:
            for r in result.journal:
                f.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
        paths["journal"] = jp
    paths["report"] = write_report(d)
    return paths


def _read_csv(p: Path) -> list[dict[str, str]]:
    if not p.exists() or p.stat().st_size == 0:
        return []
    with p.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_report(run_dir: str | Path) -> Path:
    d = Path(run_dir)
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    metrics = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
    trades = _read_csv(d / "trades.csv")
    transitions = _read_csv(d / "state_transitions.csv")
    candidates = _read_csv(d / "candidates.csv")
    lines = [
        "# Fabio Mechanical - run report",
        "",
        f"- run id: `{manifest.get('run_id')}`  created: {manifest.get('created_utc')}",
        f"- command: `{manifest.get('command')}`",
        f"- contract: {manifest.get('contract_version')}  schema sha256: `{manifest.get('schema_sha256')}`",
        f"- config hash: `{manifest.get('config_hash')}`  invariants: `{manifest.get('invariant_hash')}`",
        f"- code: {manifest.get('code', {}).get('package_version')} source sha256 `{manifest.get('code', {}).get('source_sha256')}`"
        f" git `{manifest.get('code', {}).get('git_commit')}`",
        f"- dataset sha256: `{manifest.get('dataset', {}).get('dataset_sha256')}`",
        f"- AI enabled: {manifest.get('ai_enabled')}  decision provider: {summary.get('decision_provider')}",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    for k in (
        "trades",
        "gross_pnl",
        "fees",
        "net_pnl",
        "ending_equity",
        "max_drawdown",
        "entry_slippage_ticks_total",
        "exit_slippage_ticks_total",
        "fills",
        "maker_fills",
        "taker_fills",
        "candidates",
        "state_transitions",
        "events",
        "snapshots",
        "trade_safe_at_end",
    ):
        lines.append(f"| {k} | {summary.get(k)} |")
    lines += [
        "",
        f"Data-quality reasons at end: {summary.get('data_quality_reasons') or 'none'}",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    for k, v in metrics.items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## Trades", ""]
    if trades:
        lines += [
            "| # | setup | dir | qty | entry | exit | stop | target | net | R | exit reason | entry (UTC) |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for i, t in enumerate(trades, 1):
            lines.append(
                f"| {i} | {t['setup_type']} | {t['direction']} | {t['quantity']} | {t['entry_price']} | {t['exit_price']} | "
                f"{t['stop_price']} | {t['target_price']} | {t['net_pnl']} | {t['r_multiple']} | {t['exit_reasons']} | {t.get('entry_time_utc', '')} |"
            )
    else:
        lines.append("No trades.")
    lines += ["", "## Candidates", ""]
    outcomes = Counter(c.get("outcome", "") for c in candidates)
    lines += [f"- {k}: {v}" for k, v in sorted(outcomes.items())] or ["No candidates."]
    lines += ["", "## State transitions (counts)", ""]
    counts = Counter((t["model"], t["from_state"], t["to_state"], t["reason"]) for t in transitions)
    if counts:
        lines += ["| model | from | to | reason | count |", "|---|---|---|---|---|"]
        for (m, a, b, r), n in sorted(counts.items()):
            lines.append(f"| {m} | {a} | {b} | {r} | {n} |")
    else:
        lines.append("No transitions.")
    lines += ["", "Full detail: trades.csv, fills.csv, state_transitions.csv, candidates.csv, equity_curve.csv, journal.jsonl.", ""]
    p = d / "REPORT.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


__all__ = ["utc_iso", "write_backtest_outputs", "write_report"]
