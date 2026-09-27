"""Databento GLBX.MDP3 acquisition and MBO -> MarketEvent adaptation (Package 1, extended).

* Cost is ALWAYS estimated before a download and a download is refused above ``max_cost_usd``.
* Raw files are immutable: existing targets are never overwritten, files are made read-only,
  and a SHA256 manifest is written next to each file.
* The adapter streams DBN records (no full-file loads), converts prices with exact integer
  arithmetic, maps ``instrument_id`` to the real contract via definition records, emits a
  ``CONTRACT_ROLL`` when the continuous symbol maps to a new instrument, and emits a
  ``SNAPSHOT(snapshot_complete=True)`` marker after Databento's ``F_SNAPSHOT`` record block.
* ``databento`` is an optional dependency; nothing here imports it at module import time.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from fabio_mechanical.contracts import CONTRACT_VERSION, EventAction, MarketEvent, Side, TradePrint
from fabio_mechanical.data.instruments import instrument_root
from fabio_mechanical.data.manifest import sha256_file

from .prices import FIXED_PRICE_SCALE, UNDEF_PRICE, nanos_to_ticks, price_nanos_to_ticks, tick_nanos

DATASET = "GLBX.MDP3"
F_LAST = 1 << 7
F_SNAPSHOT = 1 << 5
ACTION_MAP = {
    "A": EventAction.ADD,
    "C": EventAction.CANCEL,
    "M": EventAction.MODIFY,
    "F": EventAction.FILL,
    "T": EventAction.TRADE,
    "R": EventAction.CLEAR,
    "N": EventAction.NONE,
}
SIDE_MAP = {"B": Side.BID, "A": Side.ASK, "N": Side.NONE}
_ORDER_ACTIONS = {EventAction.ADD, EventAction.CANCEL, EventAction.MODIFY, EventAction.FILL}
_PRICED_ACTIONS = {EventAction.ADD, EventAction.MODIFY, EventAction.TRADE, EventAction.FILL}


class DatabentoUnavailable(RuntimeError):
    pass


class CostLimitExceeded(RuntimeError):
    pass


def _get(record: Any, name: str, default: Any = None) -> Any:
    return record.get(name, default) if isinstance(record, dict) else getattr(record, name, default)


def _code(value: Any, default: str = "N") -> str:
    """Normalize Databento enum/char/int codes (``Action.ADD`` / ``'A'`` / ``65``) to one char."""
    if value is None:
        return default
    if hasattr(value, "value"):
        value = value.value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, int):
        value = chr(value)
    s = str(value)
    return s[:1] if s else default


def provider_side_to_aggressor(side: str) -> Side:
    return SIDE_MAP.get(side, Side.NONE)


def record_to_event(record: Any, *, instrument: str, active_contract: str, tick_size: Decimal = Decimal("0.25")) -> MarketEvent:
    """Validated conversion of one MBO record (dict or DBN record) -> ``MarketEvent``."""
    raw_price = _get(record, "price")
    price = None if raw_price is None or int(raw_price) == UNDEF_PRICE else price_nanos_to_ticks(int(raw_price), tick_size)
    order_id = _get(record, "order_id")
    return MarketEvent(
        ts_event_ns=int(_get(record, "ts_event", 0)),
        ts_recv_ns=int(_get(record, "ts_recv")) if _get(record, "ts_recv") is not None else None,
        sequence=int(_get(record, "sequence", 0)),
        instrument=instrument,
        active_contract=active_contract,
        instrument_id=int(_get(record, "instrument_id")) if _get(record, "instrument_id") is not None else None,
        action=ACTION_MAP.get(_code(_get(record, "action")), EventAction.NONE),
        side=SIDE_MAP.get(_code(_get(record, "side")), Side.NONE),
        price_ticks=price,
        size=int(_get(record, "size", 0)),
        order_id=int(order_id) if order_id is not None else None,
        channel_id=int(_get(record, "channel_id")) if _get(record, "channel_id") is not None else None,
        provider_flags=int(_get(record, "flags")) if _get(record, "flags") is not None else None,
        source="databento",
    )


def event_to_trade(event: MarketEvent) -> TradePrint | None:
    if event.action != EventAction.TRADE or event.price_ticks is None or event.size <= 0:
        return None
    return TradePrint(
        ts_event_ns=event.ts_event_ns,
        sequence=event.sequence,
        instrument=event.instrument,
        active_contract=event.active_contract,
        price_ticks=event.price_ticks,
        size=event.size,
        aggressor_side=event.side,
        initiator_source="provider" if event.side != Side.NONE else "unknown",
    )


def trades_record_to_trade(
    record: Any,
    *,
    instrument: str,
    active_contract: str,
    tick_size: Decimal = Decimal("0.25"),
    best_bid_ticks: int | None = None,
    best_ask_ticks: int | None = None,
) -> TradePrint:
    """Databento ``trades`` schema record -> TradePrint (provider side first, labelled quote fallback)."""
    price_ticks = price_nanos_to_ticks(int(_get(record, "price")), tick_size)
    side = provider_side_to_aggressor(_code(_get(record, "side")))
    source = "provider"
    if side == Side.NONE:
        source = "unknown"
        if best_ask_ticks is not None and price_ticks >= best_ask_ticks:
            side, source = Side.BID, "quote_fallback"
        elif best_bid_ticks is not None and price_ticks <= best_bid_ticks:
            side, source = Side.ASK, "quote_fallback"
    return TradePrint(
        ts_event_ns=int(_get(record, "ts_event", 0)),
        sequence=int(_get(record, "sequence", 0)),
        instrument=instrument,
        active_contract=active_contract,
        price_ticks=price_ticks,
        size=int(_get(record, "size", 0)),
        aggressor_side=side,
        initiator_source=source,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------- contracts


@dataclass(frozen=True, slots=True)
class ContractInfo:
    instrument_id: int
    raw_symbol: str
    root: str
    tick_size: Decimal
    expiration_ns: int | None = None


class ContractResolver:
    """instrument_id -> real contract, built from Databento ``definition`` records."""

    def __init__(self, contracts: dict[int, ContractInfo] | None = None) -> None:
        self.contracts: dict[int, ContractInfo] = dict(contracts or {})

    @classmethod
    def from_records(cls, records: Iterable[Any], known_roots: tuple[str, ...] = ("MNQ", "NQ")) -> ContractResolver:
        out: dict[int, ContractInfo] = {}
        for rec in records:
            raw_symbol = _get(rec, "raw_symbol")
            iid = _get(rec, "instrument_id")
            inc = _get(rec, "min_price_increment")
            if raw_symbol is None or iid is None or inc is None:
                continue
            exp = _get(rec, "expiration")
            asset = _get(rec, "asset")
            root = str(asset) if asset else instrument_root(str(raw_symbol), known_roots)
            out[int(iid)] = ContractInfo(
                instrument_id=int(iid),
                raw_symbol=str(raw_symbol),
                root=instrument_root(root, known_roots),
                tick_size=Decimal(int(inc)) / Decimal(FIXED_PRICE_SCALE),
                expiration_ns=int(exp) if exp is not None and int(exp) < UNDEF_PRICE else None,
            )
        return cls(out)

    @classmethod
    def from_definition_files(cls, paths: Iterable[str | Path], known_roots: tuple[str, ...] = ("MNQ", "NQ")) -> ContractResolver:
        merged: dict[int, ContractInfo] = {}
        for p in paths:
            merged.update(cls.from_records(iter_dbn_records(p), known_roots).contracts)
        return cls(merged)

    def resolve(self, instrument_id: int) -> ContractInfo:
        try:
            return self.contracts[instrument_id]
        except KeyError as exc:
            raise KeyError(
                f"instrument_id {instrument_id} has no definition record; download the 'definition' schema for the same dates"
            ) from exc


class DatabentoMBOAdapter:
    """Stream Databento MBO records as canonical ``MarketEvent`` objects."""

    def __init__(self, resolver: ContractResolver, on_invalid: Callable[[str], None] | None = None) -> None:
        self.resolver = resolver
        self.on_invalid = on_invalid
        self.invalid_records = 0
        self.records = 0
        self.rolls = 0
        self._current_iid: int | None = None
        self._in_snapshot = False
        self._last_snapshot_ts: tuple[int, int | None, int] = (0, None, 0)
        self._tick_ns: dict[int, int] = {}

    def _invalid(self, reason: str) -> None:
        self.invalid_records += 1
        if self.on_invalid is not None:
            self.on_invalid(reason)

    def events(self, records: Iterable[Any]) -> Iterator[MarketEvent]:
        for rec in records:
            if _get(rec, "order_id") is None or _get(rec, "action") is None:
                continue  # not an MBO record (e.g. system/symbol-mapping records)
            self.records += 1
            iid = int(_get(rec, "instrument_id"))
            info = self.resolver.resolve(iid)
            ts_event = int(_get(rec, "ts_event"))
            ts_recv_raw = _get(rec, "ts_recv")
            ts_recv = int(ts_recv_raw) if ts_recv_raw is not None else None
            seq = int(_get(rec, "sequence", 0) or 0)
            flags = int(_get(rec, "flags", 0) or 0)
            if self._current_iid is not None and iid != self._current_iid:
                self.rolls += 1
                self._in_snapshot = False
                yield MarketEvent.model_construct(
                    schema_version=CONTRACT_VERSION,
                    ts_event_ns=ts_event,
                    ts_recv_ns=ts_recv,
                    sequence=0,
                    instrument=info.root,
                    active_contract=info.raw_symbol,
                    instrument_id=iid,
                    action=EventAction.CONTRACT_ROLL,
                    side=Side.NONE,
                    price_ticks=None,
                    size=0,
                    order_id=None,
                    channel_id=None,
                    provider_flags=None,
                    source="databento",
                    snapshot_complete=False,
                    metadata={},
                )
            self._current_iid = iid
            is_snapshot = bool(flags & F_SNAPSHOT)
            if self._in_snapshot and not is_snapshot:
                t_ev, t_recv, t_seq = self._last_snapshot_ts
                self._in_snapshot = False
                yield MarketEvent.model_construct(
                    schema_version=CONTRACT_VERSION,
                    ts_event_ns=t_ev,
                    ts_recv_ns=t_recv,
                    sequence=t_seq,
                    instrument=info.root,
                    active_contract=info.raw_symbol,
                    instrument_id=iid,
                    action=EventAction.SNAPSHOT,
                    side=Side.NONE,
                    price_ticks=None,
                    size=0,
                    order_id=None,
                    channel_id=None,
                    provider_flags=F_LAST,
                    source="databento",
                    snapshot_complete=True,
                    metadata={},
                )
            action = ACTION_MAP.get(_code(_get(rec, "action")), EventAction.NONE)
            raw_price = _get(rec, "price")
            price: int | None = None
            if raw_price is not None and int(raw_price) != UNDEF_PRICE:
                tns = self._tick_ns.get(iid)
                if tns is None:
                    tns = self._tick_ns[iid] = tick_nanos(info.tick_size)
                price = nanos_to_ticks(int(raw_price), tns)
            size = int(_get(rec, "size", 0) or 0)
            if (action in _PRICED_ACTIONS and price is None) or size < 0:
                self._invalid(f"invalid_{action.value.lower()}_record")
                continue
            if is_snapshot:
                self._in_snapshot = True
                self._last_snapshot_ts = (ts_event, ts_recv, seq)
            channel = _get(rec, "channel_id")
            yield MarketEvent.model_construct(
                schema_version=CONTRACT_VERSION,
                ts_event_ns=ts_event,
                ts_recv_ns=ts_recv,
                sequence=seq,
                instrument=info.root,
                active_contract=info.raw_symbol,
                instrument_id=iid,
                action=action,
                side=SIDE_MAP.get(_code(_get(rec, "side")), Side.NONE),
                price_ticks=price,
                size=size,
                order_id=int(_get(rec, "order_id")) if action in _ORDER_ACTIONS or action == EventAction.TRADE else None,
                channel_id=int(channel) if channel is not None else None,
                provider_flags=flags,
                source="databento",
                snapshot_complete=False,
                metadata={},
            )


# ---------------------------------------------------------------------------- files


def _require_databento() -> Any:
    try:
        import databento as db
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise DatabentoUnavailable("Install the Databento extra: pip install -e '.[databento]'") from exc
    return db


def iter_dbn_records(path: str | Path) -> Iterator[Any]:
    """Stream records from a DBN / DBN.zst file (the DBNStore iterator decodes incrementally)."""
    db = _require_databento()
    store = db.DBNStore.from_file(str(path))
    yield from store


def dbn_metadata(path: str | Path) -> dict[str, Any]:
    db = _require_databento()
    store = db.DBNStore.from_file(str(path))
    return {
        "dataset": store.dataset,
        "schema": str(store.schema),
        "symbols": list(store.symbols or []),
        "stype_in": str(store.stype_in) if store.stype_in is not None else None,
        "stype_out": str(store.stype_out),
        "start": str(store.start),
        "end": str(store.end) if store.end is not None else None,
    }


def manifest_path(data_path: Path) -> Path:
    return data_path.with_suffix(data_path.suffix + ".manifest.json")


def validate_manifest(data_path: Path) -> bool:
    mp = manifest_path(data_path)
    if not data_path.exists() or not mp.exists():
        return False
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    return int(manifest.get("bytes", -1)) == data_path.stat().st_size and manifest.get("sha256") == sha256_file(data_path)


def deep_validate(path: str | Path, resolver: ContractResolver | None = None, max_records: int | None = None) -> dict[str, Any]:
    """Stream the file and report structural facts needed before a replay."""
    counts: dict[str, int] = {}
    first_recv = last_recv = None
    recv_regressions = 0
    snapshot_records = 0
    starts_with_snapshot = None
    instrument_ids: set[int] = set()
    n = 0
    for rec in iter_dbn_records(path):
        if _get(rec, "action") is None:
            continue
        n += 1
        act = _code(_get(rec, "action"))
        counts[act] = counts.get(act, 0) + 1
        flags = int(_get(rec, "flags", 0) or 0)
        if starts_with_snapshot is None:
            starts_with_snapshot = act == "R" or bool(flags & F_SNAPSHOT)
        if flags & F_SNAPSHOT:
            snapshot_records += 1
        recv = int(_get(rec, "ts_recv", 0) or 0)
        if last_recv is not None and recv < last_recv:
            recv_regressions += 1
        first_recv = recv if first_recv is None else first_recv
        last_recv = recv
        instrument_ids.add(int(_get(rec, "instrument_id")))
        if max_records is not None and n >= max_records:
            break
    unresolved = sorted(i for i in instrument_ids if resolver is not None and i not in resolver.contracts)
    return {
        "path": str(path),
        "records": n,
        "action_counts": counts,
        "first_ts_recv_ns": first_recv,
        "last_ts_recv_ns": last_recv,
        "ts_recv_regressions": recv_regressions,
        "starts_with_snapshot": bool(starts_with_snapshot),
        "snapshot_records": snapshot_records,
        "instrument_ids": sorted(instrument_ids),
        "unresolved_instrument_ids": unresolved,
        "ok": recv_regressions == 0 and bool(starts_with_snapshot) and not unresolved and n > 0,
    }


class DatabentoDownloader:
    """Historical client wrapper: pre-costing, cost cap, immutable raw data, safe batch resume."""

    def __init__(self, api_key: str | None = None, client: Any | None = None, dataset: str = DATASET) -> None:
        self.api_key = api_key or os.getenv("DATABENTO_API_KEY")
        self._client = client
        self.dataset = dataset

    @property
    def client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise DatabentoUnavailable("DATABENTO_API_KEY is not set (put it in .env or the environment)")
        db = _require_databento()
        self._client = db.Historical(self.api_key)
        return self._client

    def cost_estimate(self, *, symbols: str | list[str], schema: str, start: str, end: str, stype_in: str = "continuous") -> dict[str, Any]:
        kw = {"dataset": self.dataset, "symbols": symbols, "schema": schema, "start": start, "end": end, "stype_in": stype_in}
        cost = self.client.metadata.get_cost(**kw)
        size = self.client.metadata.get_billable_size(**kw)
        count = self.client.metadata.get_record_count(**kw)
        return {**kw, "estimated_cost_usd": float(cost), "billable_bytes": int(size), "record_count": int(count)}

    def resolve_continuous(self, symbol: str, start_date: str, end_date: str | None = None) -> dict[str, Any]:
        result = self.client.symbology.resolve(
            dataset=self.dataset,
            symbols=symbol,
            stype_in="continuous",
            stype_out="instrument_id",
            start_date=start_date,
            end_date=end_date,
        )
        return dict(result) if not isinstance(result, dict) else result

    @staticmethod
    def _check_cost(estimate: dict[str, Any], max_cost_usd: float | None) -> None:
        if max_cost_usd is not None and estimate["estimated_cost_usd"] > max_cost_usd:
            raise CostLimitExceeded(
                f"estimated cost ${estimate['estimated_cost_usd']:.2f} exceeds --max-cost ${max_cost_usd:.2f}; nothing downloaded"
            )

    def download_stream(
        self,
        *,
        symbols: str | list[str],
        schema: str,
        start: str,
        end: str,
        output: Path,
        stype_in: str = "continuous",
        retries: int = 3,
        max_cost_usd: float | None = None,
        estimate: dict[str, Any] | None = None,
    ) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            raise FileExistsError(f"raw immutable target already exists: {output}")
        estimate = estimate or self.cost_estimate(symbols=symbols, schema=schema, start=start, end=end, stype_in=stype_in)
        self._check_cost(estimate, max_cost_usd)
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                data = self.client.timeseries.get_range(
                    dataset=self.dataset, symbols=symbols, schema=schema, start=start, end=end, stype_in=stype_in, path=str(output)
                )
                if not output.exists() and hasattr(data, "to_file"):
                    data.to_file(output)
                if not output.exists() or output.stat().st_size == 0:
                    raise OSError("download produced empty/missing file")
                manifest = {
                    **estimate,
                    "sha256": sha256_file(output),
                    "bytes": output.stat().st_size,
                    "file": output.name,
                    "created_unix": int(time.time()),
                }
                manifest_path(output).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
                os.chmod(output, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
                return output
            except (CostLimitExceeded, FileExistsError):
                raise
            except Exception as exc:  # network/provider errors: retry with backoff
                last_exc = exc
                if output.exists():
                    output.unlink()
                time.sleep(min(2**attempt, 4))
        raise RuntimeError(f"download failed after {retries} attempts") from last_exc

    def download_batch(
        self,
        *,
        symbols: str | list[str],
        schema: str,
        start: str,
        end: str,
        output_dir: Path,
        state_file: Path,
        stype_in: str = "continuous",
        split_duration: str = "day",
        poll_seconds: float = 5.0,
        max_cost_usd: float | None = None,
    ) -> list[Path]:
        request = {
            "dataset": self.dataset,
            "symbols": symbols,
            "schema": schema,
            "start": start,
            "end": end,
            "stype_in": stype_in,
            "split_duration": split_duration,
        }
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        output_dir.mkdir(parents=True, exist_ok=True)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        if state_file.exists():
            state = json.loads(state_file.read_text(encoding="utf-8"))
            if state.get("request_sha256") != fingerprint:
                raise RuntimeError("resume state belongs to a different batch request")
            job_id = str(state["job_id"])
        else:
            estimate = self.cost_estimate(symbols=symbols, schema=schema, start=start, end=end, stype_in=stype_in)
            self._check_cost(estimate, max_cost_usd)
            job = self.client.batch.submit_job(
                dataset=self.dataset,
                symbols=symbols,
                schema=schema,
                start=start,
                end=end,
                stype_in=stype_in,
                split_duration=split_duration,
                encoding="dbn",
            )
            job_id = str(job["id"])
            state = {"request_sha256": fingerprint, "job_id": job_id, "request": request, "estimate": estimate}
            state_file.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        while job_id not in {str(j["id"]) for j in self.client.batch.list_jobs("done")}:
            time.sleep(poll_seconds)
        paths = [Path(p) for p in self.client.batch.download(job_id=job_id, output_dir=output_dir)]
        listed = {x["filename"]: x for x in self.client.batch.list_files(job_id=job_id)}
        checksums: dict[str, str] = {}
        for p in paths:
            if not p.is_file():
                continue
            digest = sha256_file(p)
            checksums[p.name] = digest
            remote_hash = str(listed.get(p.name, {}).get("hash", ""))
            if remote_hash.startswith("sha256:") and remote_hash.split(":", 1)[1] != digest:
                raise OSError(f"checksum mismatch for {p.name}")
        state.update(downloaded_files=[str(p) for p in paths], sha256=checksums, complete=True)
        state_file.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        return paths
