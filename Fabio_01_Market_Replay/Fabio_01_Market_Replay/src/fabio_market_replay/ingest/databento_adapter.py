from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
import hashlib
import json
import os
import time

from fabio_market_replay.contracts import EventAction, MarketEvent, Side, TradePrint

DATASET = "GLBX.MDP3"
ACTION_MAP = {"A": EventAction.ADD, "C": EventAction.CANCEL, "M": EventAction.MODIFY,
              "F": EventAction.FILL, "T": EventAction.TRADE, "R": EventAction.CLEAR, "N": EventAction.NONE}
SIDE_MAP = {"B": Side.BID, "A": Side.ASK, "N": Side.NONE}


def price_nanos_to_ticks(price_nanos: int, tick_size: Decimal = Decimal("0.25")) -> int:
    px = Decimal(price_nanos) / Decimal(1_000_000_000)
    return int((px / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def provider_side_to_aggressor(side: str) -> Side:
    return SIDE_MAP.get(side, Side.NONE)


def record_to_event(record: Any, *, instrument: str, active_contract: str,
                    tick_size: Decimal = Decimal("0.25")) -> MarketEvent:
    def get(name: str, default: Any = None) -> Any:
        return record.get(name, default) if isinstance(record, dict) else getattr(record, name, default)
    action_code = str(get("action", "N")); side_code = str(get("side", "N")); raw_price = get("price")
    return MarketEvent(
        ts_event_ns=int(get("ts_event", 0)), sequence=int(get("sequence", 0)), instrument=instrument,
        active_contract=active_contract, action=ACTION_MAP.get(action_code, EventAction.NONE),
        side=SIDE_MAP.get(side_code, Side.NONE),
        price_ticks=None if raw_price is None else price_nanos_to_ticks(int(raw_price), tick_size),
        size=int(get("size", 0)), order_id=int(get("order_id")) if get("order_id") is not None else None,
        channel_id=int(get("channel_id")) if get("channel_id") is not None else None,
        provider_flags=int(get("flags")) if get("flags") is not None else None, source="databento",
    )


def event_to_trade(event: MarketEvent) -> TradePrint | None:
    if event.action != EventAction.TRADE or event.price_ticks is None or event.size <= 0:
        return None
    return TradePrint(ts_event_ns=event.ts_event_ns, sequence=event.sequence, instrument=event.instrument,
                      active_contract=event.active_contract, price_ticks=event.price_ticks, size=event.size,
                      aggressor_side=event.side,
                      initiator_source="provider" if event.side != Side.NONE else "unknown")


def trades_record_to_trade(record: Any, *, instrument: str, active_contract: str,
                           tick_size: Decimal = Decimal("0.25"),
                           best_bid_ticks: int | None = None, best_ask_ticks: int | None = None) -> TradePrint:
    def get(name: str, default: Any = None) -> Any:
        return record.get(name, default) if isinstance(record, dict) else getattr(record, name, default)
    price_ticks = price_nanos_to_ticks(int(get("price")), tick_size)
    side = provider_side_to_aggressor(str(get("side", "N")))
    source = "provider"
    if side == Side.NONE:
        source = "unknown"
        if best_ask_ticks is not None and price_ticks >= best_ask_ticks:
            side = Side.BID; source = "quote_fallback"
        elif best_bid_ticks is not None and price_ticks <= best_bid_ticks:
            side = Side.ASK; source = "quote_fallback"
    return TradePrint(ts_event_ns=int(get("ts_event", 0)), sequence=int(get("sequence", 0)),
                      instrument=instrument, active_contract=active_contract, price_ticks=price_ticks,
                      size=int(get("size", 0)), aggressor_side=side, initiator_source=source)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class DatabentoDownloader:
    """Testable Databento Historical wrapper with pre-costing, immutable raw data and safe batch resume."""

    def __init__(self, api_key: str | None = None, client: Any | None = None) -> None:
        self.api_key = api_key or os.getenv("DATABENTO_API_KEY")
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise RuntimeError("DATABENTO_API_KEY is not set")
        try:
            import databento as db
        except ImportError as exc:
            raise RuntimeError("Install production extra: pip install '.[databento]'") from exc
        self._client = db.Historical(self.api_key)
        return self._client

    def cost_estimate(self, *, symbols: str | list[str], schema: str, start: str, end: str,
                      stype_in: str = "continuous") -> dict[str, Any]:
        cost = self.client.metadata.get_cost(dataset=DATASET, symbols=symbols, schema=schema,
                                             start=start, end=end, stype_in=stype_in)
        size = self.client.metadata.get_billable_size(dataset=DATASET, symbols=symbols, schema=schema,
                                                       start=start, end=end, stype_in=stype_in)
        count = self.client.metadata.get_record_count(dataset=DATASET, symbols=symbols, schema=schema,
                                                       start=start, end=end, stype_in=stype_in)
        return {"dataset": DATASET, "symbols": symbols, "schema": schema, "start": start, "end": end,
                "stype_in": stype_in, "estimated_cost_usd": float(cost), "billable_bytes": int(size),
                "record_count": int(count)}

    def resolve_continuous(self, symbol: str, start_date: str, end_date: str | None = None) -> dict[str, Any]:
        return self.client.symbology.resolve(dataset=DATASET, symbols=symbol, stype_in="continuous",
                                              stype_out="instrument_id", start_date=start_date, end_date=end_date)

    def download_stream(self, *, symbols: str | list[str], schema: str, start: str, end: str,
                        output: Path, stype_in: str = "continuous", retries: int = 3) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            raise FileExistsError(f"raw immutable target already exists: {output}")
        estimate = self.cost_estimate(symbols=symbols, schema=schema, start=start, end=end, stype_in=stype_in)
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                data = self.client.timeseries.get_range(dataset=DATASET, symbols=symbols, schema=schema,
                                                        start=start, end=end, stype_in=stype_in, path=str(output))
                if not output.exists() and hasattr(data, "to_file"):
                    data.to_file(output)
                if not output.exists() or output.stat().st_size == 0:
                    raise IOError("download produced empty/missing file")
                manifest = {**estimate, "sha256": sha256_file(output), "bytes": output.stat().st_size,
                            "file": output.name, "created_unix": int(time.time())}
                output.with_suffix(output.suffix + ".manifest.json").write_text(
                    json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
                return output
            except Exception as exc:
                last_exc = exc
                if output.exists(): output.unlink()
                time.sleep(min(2 ** attempt, 4))
        raise RuntimeError(f"download failed after {retries} attempts") from last_exc

    def download_batch(self, *, symbols: str | list[str], schema: str, start: str, end: str,
                       output_dir: Path, state_file: Path, stype_in: str = "continuous",
                       split_duration: str = "day", poll_seconds: float = 1.0) -> list[Path]:
        request = {"dataset": DATASET, "symbols": symbols, "schema": schema, "start": start, "end": end,
                   "stype_in": stype_in, "split_duration": split_duration}
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        output_dir.mkdir(parents=True, exist_ok=True); state_file.parent.mkdir(parents=True, exist_ok=True)
        if state_file.exists():
            state = json.loads(state_file.read_text(encoding="utf-8"))
            if state.get("request_sha256") != fingerprint:
                raise RuntimeError("resume state belongs to a different batch request")
            job_id = str(state["job_id"])
        else:
            estimate = self.cost_estimate(symbols=symbols, schema=schema, start=start, end=end, stype_in=stype_in)
            job = self.client.batch.submit_job(dataset=DATASET, symbols=symbols, schema=schema, start=start, end=end,
                                               stype_in=stype_in, split_duration=split_duration, encoding="dbn")
            job_id = str(job["id"])
            state = {"request_sha256": fingerprint, "job_id": job_id, "request": request, "estimate": estimate}
            state_file.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        while True:
            if job_id in {str(j["id"]) for j in self.client.batch.list_jobs("done")}:
                break
            time.sleep(poll_seconds)
        paths = [Path(p) for p in self.client.batch.download(job_id=job_id, output_dir=output_dir)]
        listed = {x["filename"]: x for x in self.client.batch.list_files(job_id=job_id)}
        checksums: dict[str, str] = {}
        for p in paths:
            if not p.is_file(): continue
            digest = sha256_file(p); checksums[p.name] = digest
            remote_hash = str(listed.get(p.name, {}).get("hash", ""))
            if remote_hash.startswith("sha256:") and remote_hash.split(":", 1)[1] != digest:
                raise IOError(f"checksum mismatch for {p.name}")
        state["downloaded_files"] = [str(p) for p in paths]; state["sha256"] = checksums; state["complete"] = True
        state_file.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        return paths

    @staticmethod
    def validate_manifest(data_path: Path) -> bool:
        manifest_path = data_path.with_suffix(data_path.suffix + ".manifest.json")
        if not data_path.exists() or not manifest_path.exists():
            return False
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return int(manifest.get("bytes", -1)) == data_path.stat().st_size and manifest.get("sha256") == sha256_file(data_path)
