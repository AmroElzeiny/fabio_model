from __future__ import annotations

from pathlib import Path
import hashlib
import json
from typing import Iterable

from fabio_market_replay.contracts import MarketFeatureSnapshot


def config_data_hash(data_paths: Iterable[Path], config_payload: dict) -> str:
    h = hashlib.sha256(json.dumps(config_payload, sort_keys=True, separators=(",", ":")).encode())
    for p in sorted(data_paths, key=lambda x: str(x)):
        h.update(str(p).encode())
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    return h.hexdigest()


def export_jsonl(path: str | Path, snapshots: Iterable[MarketFeatureSnapshot]) -> Path:
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for s in snapshots:
            f.write(s.model_dump_json() + "\n")
    return p


def export_parquet(path: str | Path, snapshots: Iterable[MarketFeatureSnapshot]) -> Path:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("Install production extra: pip install '.[parquet]'") from exc
    rows = []
    for s in snapshots:
        rows.append({"timestamp_ns": s.timestamp_ns, "instrument": s.instrument,
                     "active_contract": s.active_contract, "trade_safe": s.data_quality.trade_safe,
                     "snapshot_json": s.model_dump_json()})
    table = pa.Table.from_pylist(rows)
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, p, compression="zstd")
    return p
