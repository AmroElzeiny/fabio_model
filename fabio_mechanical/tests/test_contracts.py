from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from fabio_mechanical.contracts import (
    CONTRACT_MODELS,
    CONTRACT_VERSION,
    DecisionSnapshot,
    Direction,
    EventAction,
    MarketEvent,
    MarketFeatureSnapshot,
    SetupType,
    TradeCandidate,
    canonical_schema_bundle,
    canonical_schema_hash,
    contract_descriptor,
    payload_sha256,
    pinned_schema_hash,
    verify_schema_pin,
)
from fabio_mechanical.contracts.legacy import migrate_p1_market_feature_snapshot
from fabio_mechanical.contracts.schema import BUNDLE_FILE

from .fixtures.snapshots import valid_trend_long

PKG = Path(__file__).resolve().parents[1]


def test_contract_version_and_pinned_schema_hash() -> None:
    assert CONTRACT_VERSION == "FABIO_MECHANICAL_V1"
    h = canonical_schema_hash()
    assert len(h) == 64 and h == canonical_schema_hash()
    assert verify_schema_pin() == h == pinned_schema_hash()


def test_exported_bundle_matches_canonical_models() -> None:
    bundle = json.loads(BUNDLE_FILE.read_text(encoding="utf-8"))
    assert bundle["descriptor"] == json.loads(json.dumps(contract_descriptor()))
    assert bundle["schema_sha256"] == canonical_schema_hash()
    names = set(contract_descriptor()["top_level_models"])
    for required in (
        "MarketEvent",
        "TradePrint",
        "BookSnapshot",
        "FootprintSnapshot",
        "VolumeProfileSnapshot",
        "AuctionFeatureSnapshot",
        "MarketFeatureSnapshot",
        "DecisionSnapshot",
        "TradeCandidate",
        "ExecutionIntent",
        "ExecutionReport",
    ):
        assert required in names
    assert set(canonical_schema_bundle()["json_schema"]) == names


def test_schema_hash_is_library_independent() -> None:
    """The hash is built from our own descriptor, so pydantic's JSON-Schema rendering cannot move it."""
    d = contract_descriptor()
    tc = {f["name"]: f for f in d["models"]["TradeCandidate"]["fields"]}
    assert tc["stop_ticks"]["type"] == "int" and tc["direction"]["type"] == "enum:Direction"
    me = {f["name"]: f for f in d["models"]["MarketEvent"]["fields"]}
    assert me["size"]["constraints"] == ["ge=0"] and me["price_ticks"]["type"] == "union<int,null>"
    assert d["enums"]["Direction"] == ["LONG", "SHORT", "FLAT"]


def test_single_canonical_schema_definition() -> None:
    """No duplicate schema definitions anywhere in the package."""
    for model in CONTRACT_MODELS:
        hits = [p for p in PKG.rglob("*.py") if "tests" not in p.parts and f"class {model.__name__}(" in p.read_text(encoding="utf-8")]
        assert [h.relative_to(PKG).as_posix() for h in hits] == ["contracts/models.py"], model.__name__


def test_snapshot_roundtrip_and_hash_determinism() -> None:
    s = valid_trend_long()
    restored = MarketFeatureSnapshot.model_validate_json(s.model_dump_json())
    assert restored == s
    assert payload_sha256(restored) == payload_sha256(s)


def test_market_event_validation() -> None:
    with pytest.raises(ValidationError):
        MarketEvent(ts_event_ns=1, sequence=1, instrument="NQ", active_contract="NQH6", action=EventAction.ADD, price_ticks=1, size=1)
    with pytest.raises(ValidationError):
        MarketEvent(ts_event_ns=1, sequence=1, instrument="NQ", active_contract="NQH6", action=EventAction.TRADE, size=1)
    with pytest.raises(ValidationError):
        MarketEvent(ts_event_ns=1, sequence=1, instrument="NQ", active_contract="NQH6", action=EventAction.CANCEL, order_id=1, size=-1)
    e = MarketEvent(ts_event_ns=5, ts_recv_ns=7, sequence=1, instrument="NQ", active_contract="NQH6", action=EventAction.NONE)
    assert e.clock_ns == 7


def test_candidate_geometry_and_ai_flag_are_contractual() -> None:
    d = DecisionSnapshot(timestamp_ns=1, instrument="NQ", active_contract="NQH6", trade_safe=True)
    with pytest.raises(ValidationError):
        DecisionSnapshot(timestamp_ns=1, instrument="NQ", active_contract="NQH6", trade_safe=True, ai_enabled=True)  # type: ignore[arg-type]
    base = dict(
        candidate_id="c",
        setup_type=SetupType.TREND,
        timestamp_ns=1,
        event_index=1,
        instrument="NQ",
        active_contract="NQH6",
        evidence={},
        decision_snapshot=d,
    )
    TradeCandidate(direction=Direction.LONG, entry_reference_ticks=10, stop_ticks=8, target_ticks=20, **base)
    with pytest.raises(ValidationError):
        TradeCandidate(direction=Direction.LONG, entry_reference_ticks=10, stop_ticks=12, target_ticks=20, **base)
    with pytest.raises(ValidationError):
        TradeCandidate(direction=Direction.SHORT, entry_reference_ticks=10, stop_ticks=12, target_ticks=11, **base)


def test_crossed_book_cannot_be_trade_safe() -> None:
    s = valid_trend_long()
    data = s.model_dump()
    data.update(best_bid_ticks=500, best_ask_ticks=499)
    with pytest.raises(ValidationError):
        MarketFeatureSnapshot.model_validate(data)


def test_legacy_package1_snapshot_migrates_with_neutral_structure() -> None:
    line = (PKG / "tests" / "fixtures" / "legacy_p1_market_feature_snapshots.jsonl").read_text(encoding="utf-8").strip()
    s = migrate_p1_market_feature_snapshot(json.loads(line))
    assert s.schema_version == CONTRACT_VERSION
    assert s.auction.out_of_balance is False and s.auction.failed_auction is False
    assert s.directional_aggression == Direction.FLAT
    assert s.trading_day == "2026-01-12"
