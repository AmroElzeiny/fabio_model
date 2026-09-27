"""JSON/CSV/Parquet writers (Package 2 export + Package 1 snapshot export)."""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterable
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from fabio_mechanical.contracts import MarketFeatureSnapshot


def _default(x: Any) -> Any:
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, Enum):
        return x.value
    if is_dataclass(x) and not isinstance(x, type):
        return asdict(x)
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json")
    if isinstance(x, float) and (math.isinf(x) or math.isnan(x)):
        return str(x)
    return str(x)


def _clean(obj: Any) -> Any:
    if isinstance(obj, float) and (math.isinf(obj) or math.isnan(obj)):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def export_json(path: str | Path, obj: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_clean(obj), indent=2, default=_default, sort_keys=True) + "\n", encoding="utf-8")
    return p


def utc_iso(ts_ns: int | None) -> str:
    if not ts_ns:
        return ""
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).isoformat(timespec="microseconds")


def export_rows_csv(path: str | Path, rows: list[dict[str, Any]], time_fields: tuple[str, ...] = ()) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for r in rows:
        row = {k: _default(v) if isinstance(v, (Decimal, Enum)) else v for k, v in r.items()}
        for tf in time_fields:
            if tf in row:
                row[f"{tf.removesuffix('_ns')}_utc"] = utc_iso(row[tf])
        out.append(row)
    fields: list[str] = []
    for r in out:
        for k in r:
            if k not in fields:
                fields.append(k)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out)
    return p


def export_ledger_csv(path: str | Path, ledger: list[Any]) -> Path:
    rows = [asdict(x) if is_dataclass(x) and not isinstance(x, type) else dict(x) for x in ledger]
    return export_rows_csv(path, rows, ("entry_time_ns", "exit_time_ns"))


def export_snapshots_jsonl(path: str | Path, snapshots: Iterable[MarketFeatureSnapshot], compact: bool = False) -> Path:
    """Stream snapshots to JSONL. ``compact`` drops per-price volume maps (export view only)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="\n") as f:
        for s in snapshots:
            data = s.model_dump(mode="json")
            if compact:
                for prof in data.get("profiles", []):
                    prof["volume_by_price"] = {}
                for key in ("impulse_profile", "reclaim_profile"):
                    if data.get(key):
                        data[key]["volume_by_price"] = {}
                data["provenance"]["export_view"] = "compact"
            f.write(json.dumps(data, separators=(",", ":"), sort_keys=True) + "\n")
    return p


def export_snapshots_parquet(path: str | Path, snapshots: Iterable[MarketFeatureSnapshot]) -> Path:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("Install the parquet extra: pip install -e '.[parquet]'") from exc
    rows = [
        {
            "timestamp_ns": s.timestamp_ns,
            "event_index": s.event_index,
            "instrument": s.instrument,
            "active_contract": s.active_contract,
            "trade_safe": s.trade_safe,
            "snapshot_json": s.model_dump_json(),
        }
        for s in snapshots
    ]
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), p, compression="zstd")
    return p
