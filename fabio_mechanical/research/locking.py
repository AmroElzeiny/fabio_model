"""Scientific protocol guards (Package 2 locking, extended).

* Phases (research.yaml): DEVELOPMENT, VALIDATION, LOCKED OOS - disjoint CME trading-day ranges.
* The optimizer and walk-forward only ever use DEVELOPMENT days. The guard is enforced up front
  (source labels) and at runtime (every snapshot's trading day is checked before the strategy
  sees it), so OOS data can never reach an optimization decision or score.
* The Pure Fabio baseline must be frozen (config hash recorded) before any optimization, and the
  optimizer must start from exactly that frozen configuration.
* OOS evaluation requires a lock manifest (config, schema, code, dataset hashes) created before
  OOS data is opened; any config change afterwards is rejected.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fabio_mechanical.configs import config_hash, invariant_hash
from fabio_mechanical.contracts import canonical_schema_hash
from fabio_mechanical.ingest.sources import EventSource


class OOSLockError(RuntimeError):
    pass


class ProtocolError(RuntimeError):
    pass


def _range(cfg: dict[str, Any], phase: str) -> tuple[date, date] | None:
    r = cfg["research"]["phases"].get(phase)
    if not r:
        return None
    return date.fromisoformat(str(r["start"])), date.fromisoformat(str(r["end"]))


def phase_of(cfg: dict[str, Any], day: date) -> str | None:
    for phase in ("development", "validation", "oos"):
        r = _range(cfg, phase)
        if r and r[0] <= day <= r[1]:
            return phase
    return None


def assert_optimization_allowed(phase: str) -> None:
    if phase.upper() in {"LOCKED_OOS", "OOS"}:
        raise OOSLockError("Optimization is forbidden on locked OOS")


def _allowed_ranges(cfg: dict[str, Any], allowed: Sequence[str]) -> list[tuple[date, date]]:
    ranges = [r for r in (_range(cfg, p) for p in allowed) if r is not None]
    if not ranges:
        raise ProtocolError(f"research.yaml phases {list(allowed)} are not configured; set development/validation/oos date ranges first")
    return ranges


def phase_filter(cfg: dict[str, Any], allowed: Sequence[str]) -> Callable[[str], bool]:
    """Runtime predicate: may the strategy act on (and be scored on) this CME trading day?

    Snapshots of other trading days still update market state (a UTC-day Databento file contains
    the first overnight hour of the next trading day) but never reach the strategy or metrics.
    """
    ranges = _allowed_ranges(cfg, allowed)

    def allowed_day(trading_day: str) -> bool:
        d = date.fromisoformat(trading_day)
        return any(a <= d <= b for a, b in ranges)

    return allowed_day


def check_sources(cfg: dict[str, Any], sources: Sequence[EventSource], allowed: Sequence[str]) -> None:
    """Refuse up front any labelled source whose trading day lies outside the allowed phases."""
    ranges = _allowed_ranges(cfg, allowed)
    for s in sources:
        d = s.trading_day
        if d is not None and not any(a <= d <= b for a, b in ranges):
            raise OOSLockError(f"source {s.name} ({d}) is outside the allowed phases {list(allowed)}")


@dataclass(frozen=True)
class LockManifest:
    phase: str
    config_hash: str
    invariant_hash: str
    code_commit: str
    schema_hash: str
    dataset_sha256: str
    created_utc: str


def freeze_for_oos(
    path: str | Path, config: dict[str, Any], code_commit: str, schema_hash: str | None = None, dataset_sha256: str = ""
) -> LockManifest:
    m = LockManifest(
        phase="LOCKED_OOS",
        config_hash=config_hash(config),
        invariant_hash=invariant_hash(),
        code_commit=code_commit,
        schema_hash=schema_hash or canonical_schema_hash(),
        dataset_sha256=dataset_sha256,
        created_utc=datetime.now(timezone.utc).isoformat(),
    )
    p = Path(path)
    if p.exists():
        raise OOSLockError(f"lock manifest {p} already exists; OOS locks are write-once")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(m), indent=2), encoding="utf-8")
    return m


def verify_oos_lock(path: str | Path, config: dict[str, Any]) -> bool:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if d.get("phase") != "LOCKED_OOS" or d.get("config_hash") != config_hash(config):
        raise OOSLockError("OOS config differs from frozen manifest")
    if d.get("schema_hash") != canonical_schema_hash():
        raise OOSLockError("contract schema changed since the OOS lock")
    if d.get("invariant_hash") != invariant_hash():
        raise OOSLockError("Fabio invariants changed since the OOS lock")
    return True


def freeze_baseline(
    path: str | Path, config: dict[str, Any], summary: dict[str, Any], dataset_sha256: str, code: dict[str, Any]
) -> dict[str, Any]:
    exp = config.get("experimental", {})
    if any(exp.values()):
        raise ProtocolError("the Pure Fabio baseline cannot include experimental extensions")
    body = {
        "phase": "BASELINE_FROZEN",
        "config_hash": config_hash(config),
        "invariant_hash": invariant_hash(),
        "schema_hash": canonical_schema_hash(),
        "dataset_sha256": dataset_sha256,
        "code": code,
        "summary": summary,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    return body


def require_frozen_baseline(path: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise ProtocolError(f"baseline must be frozen before optimization: run `fabio research freeze-baseline` ({p} missing)")
    d: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    if d.get("config_hash") != config_hash(config):
        raise ProtocolError("optimization must start from the frozen baseline configuration (config hash mismatch)")
    return d
