from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
import sys

from fabio_market_replay.config import EngineConfig
from fabio_market_replay.contracts import CONTRACT_VERSION, canonical_schema_bundle, canonical_schema_hash
from fabio_market_replay.export.snapshots import export_jsonl, export_parquet
from fabio_market_replay.ingest.databento_adapter import DatabentoDownloader
from fabio_market_replay.ingest.local import iter_jsonl_events, write_jsonl_events
from fabio_market_replay.replay.engine import ReplayEngine
from fabio_market_replay.synthetic import known_fixture


def _config(path: str | None) -> EngineConfig:
    return EngineConfig.from_file(path) if path else EngineConfig()


def cmd_schema_hash(_: argparse.Namespace) -> int:
    print(f"CONTRACT_VERSION={CONTRACT_VERSION}")
    print(f"CONTRACT_SCHEMA_SHA256={canonical_schema_hash()}")
    return 0


def cmd_export_schema(args: argparse.Namespace) -> int:
    p = Path(args.output); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(canonical_schema_bundle(), indent=2, sort_keys=True), encoding="utf-8")
    print(p)
    return 0


def cmd_fixture(args: argparse.Namespace) -> int:
    write_jsonl_events(args.output, known_fixture())
    print(args.output)
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    dl = DatabentoDownloader()
    result = dl.cost_estimate(symbols=args.symbol, schema=args.schema, start=args.start, end=args.end, stype_in=args.stype_in)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    dl = DatabentoDownloader()
    estimate = dl.cost_estimate(symbols=args.symbol, schema=args.schema, start=args.start, end=args.end, stype_in=args.stype_in)
    print("PRE-DOWNLOAD COST ESTIMATE")
    print(json.dumps(estimate, indent=2, sort_keys=True))
    if args.batch:
        paths = dl.download_batch(symbols=args.symbol, schema=args.schema, start=args.start, end=args.end,
                                  output_dir=Path(args.output), state_file=Path(args.state_file),
                                  stype_in=args.stype_in, split_duration=args.split_duration)
        for p in paths:
            print(p)
    else:
        p = dl.download_stream(symbols=args.symbol, schema=args.schema, start=args.start, end=args.end,
                               output=Path(args.output), stype_in=args.stype_in, retries=args.retries)
        print(p)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    ok = DatabentoDownloader.validate_manifest(Path(args.path))
    print(json.dumps({"path": args.path, "checksum_manifest_valid": ok}))
    return 0 if ok else 2


def _run_replay(args: argparse.Namespace):
    engine = ReplayEngine(_config(args.config))
    result = engine.replay(iter_jsonl_events(args.input))
    return engine, result


def cmd_replay(args: argparse.Namespace) -> int:
    _, result = _run_replay(args)
    print(json.dumps({"events_processed": result.events_processed, "trades_processed": result.trades_processed,
                      "snapshots": len(result.snapshots),
                      "final_trade_safe": result.snapshots[-1].data_quality.trade_safe if result.snapshots else False}, indent=2))
    return 0


def cmd_derive(args: argparse.Namespace) -> int:
    _, result = _run_replay(args)
    out = Path(args.output)
    if args.format == "parquet":
        export_parquet(out, result.snapshots)
    else:
        export_jsonl(out, result.snapshots)
    print(out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fabio-replay", description="Fabio mechanical market replay and feature engine")
    sp = p.add_subparsers(dest="command", required=True)
    q = sp.add_parser("schema-hash"); q.set_defaults(func=cmd_schema_hash)
    q = sp.add_parser("export-schema"); q.add_argument("--output", default="contracts/schema_bundle.json"); q.set_defaults(func=cmd_export_schema)
    q = sp.add_parser("generate-fixture"); q.add_argument("--output", default="tests/fixtures/known_session.jsonl"); q.set_defaults(func=cmd_fixture)
    for name, func in [("cost-estimate", cmd_cost), ("download", cmd_download)]:
        q = sp.add_parser(name); q.add_argument("--symbol", default="NQ.v.0"); q.add_argument("--schema", default="mbo", choices=["mbo","trades","mbp-10","definition"])
        q.add_argument("--start", required=True); q.add_argument("--end", required=True); q.add_argument("--stype-in", default="continuous")
        if name == "download":
            q.add_argument("--output", required=True); q.add_argument("--retries", type=int, default=3)
            q.add_argument("--batch", action="store_true"); q.add_argument("--state-file", default="data/raw/.batch_resume.json")
            q.add_argument("--split-duration", default="day")
        q.set_defaults(func=func)
    q = sp.add_parser("validate"); q.add_argument("path"); q.set_defaults(func=cmd_validate)
    for name, func in [("replay", cmd_replay), ("derive-features", cmd_derive), ("export-snapshots", cmd_derive)]:
        q = sp.add_parser(name); q.add_argument("--input", required=True); q.add_argument("--config")
        if name != "replay":
            q.add_argument("--output", required=True); q.add_argument("--format", choices=["jsonl","parquet"], default="jsonl")
        q.set_defaults(func=func)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))

if __name__ == "__main__":
    raise SystemExit(main())
