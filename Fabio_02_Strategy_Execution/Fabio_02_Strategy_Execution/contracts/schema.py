from __future__ import annotations
import hashlib, json
from pathlib import Path
from .models import *

MODELS = [MarketEvent, TradePrint, BookSnapshot, FootprintSnapshot, VolumeProfileSnapshot,
          AuctionFeatureSnapshot, MarketFeatureSnapshot, DecisionSnapshot, TradeCandidate,
          ExecutionIntent, ExecutionReport]

def canonical_schema() -> dict:
    return {"contract_version": CONTRACT_VERSION,
            "models": {m.__name__: m.model_json_schema() for m in MODELS}}

def canonical_schema_json() -> str:
    return json.dumps(canonical_schema(), sort_keys=True, separators=(",", ":"), default=str)

def schema_sha256() -> str:
    return hashlib.sha256(canonical_schema_json().encode()).hexdigest()

def export_schema(path: str | Path) -> None:
    Path(path).write_text(json.dumps(canonical_schema(), indent=2, default=str), encoding="utf-8")
