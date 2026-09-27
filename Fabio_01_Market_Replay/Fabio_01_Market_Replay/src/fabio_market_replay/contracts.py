from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "FABIO_MECHANICAL_V1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EventAction(str, Enum):
    ADD = "ADD"
    CANCEL = "CANCEL"
    MODIFY = "MODIFY"
    FILL = "FILL"
    TRADE = "TRADE"
    CLEAR = "CLEAR"
    SNAPSHOT = "SNAPSHOT"
    SESSION_BOUNDARY = "SESSION_BOUNDARY"
    CONTRACT_ROLL = "CONTRACT_ROLL"
    NONE = "NONE"


class Side(str, Enum):
    BID = "BID"
    ASK = "ASK"
    NONE = "NONE"


class MarketState(str, Enum):
    BALANCE = "BALANCE"
    IMBALANCE_UP = "IMBALANCE_UP"
    IMBALANCE_DOWN = "IMBALANCE_DOWN"
    TRANSITION = "TRANSITION"
    UNKNOWN = "UNKNOWN"


class MarketEvent(StrictModel):
    schema_version: str = CONTRACT_VERSION
    ts_event_ns: int = Field(ge=0)
    sequence: int = Field(ge=0)
    instrument: str
    active_contract: str
    action: EventAction
    side: Side = Side.NONE
    price_ticks: int | None = None
    size: int = Field(default=0, ge=0)
    order_id: int | None = None
    channel_id: int | None = None
    provider_flags: int | None = None
    source: str = "synthetic"
    snapshot_complete: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_order_fields(self) -> "MarketEvent":
        if self.action in {EventAction.ADD, EventAction.MODIFY, EventAction.CANCEL, EventAction.FILL}:
            if self.order_id is None:
                raise ValueError("order_id required for order-level book actions")
        if self.action in {EventAction.ADD, EventAction.MODIFY, EventAction.TRADE, EventAction.FILL}:
            if self.price_ticks is None:
                raise ValueError("price_ticks required for priced events")
        return self


class TradePrint(StrictModel):
    schema_version: str = CONTRACT_VERSION
    ts_event_ns: int = Field(ge=0)
    sequence: int = Field(ge=0)
    instrument: str
    active_contract: str
    price_ticks: int
    size: int = Field(gt=0)
    aggressor_side: Side
    initiator_source: Literal["provider", "quote_fallback", "unknown"] = "provider"


class BookLevel(StrictModel):
    price_ticks: int
    size: int = Field(ge=0)
    order_count: int = Field(ge=0)


class BookSnapshot(StrictModel):
    schema_version: str = CONTRACT_VERSION
    ts_event_ns: int
    sequence: int
    instrument: str
    active_contract: str
    best_bid: BookLevel | None
    best_ask: BookLevel | None
    bids: list[BookLevel]
    asks: list[BookLevel]
    spread_ticks: int | None
    weighted_bid_depth: float
    weighted_ask_depth: float
    dom_imbalance: float
    microprice_ticks: float | None = None
    queue_metrics: dict[str, float] = Field(default_factory=dict)


class FootprintLevel(StrictModel):
    price_ticks: int
    bid_executed: int = 0
    ask_executed: int = 0
    delta: int = 0
    diagonal_buy_ratio: float | None = None
    diagonal_sell_ratio: float | None = None
    buy_imbalance: bool = False
    sell_imbalance: bool = False


class FootprintSnapshot(StrictModel):
    schema_version: str = CONTRACT_VERSION
    ts_event_ns: int
    instrument: str
    active_contract: str
    window_start_ns: int
    window_end_ns: int
    levels: list[FootprintLevel]
    total_volume: int
    delta: int
    stacked_buy_imbalance_levels: int = 0
    stacked_sell_imbalance_levels: int = 0
    unfinished_auction_evidence: dict[str, float] = Field(default_factory=dict)


class ProfileZone(StrictModel):
    low_ticks: int
    high_ticks: int
    relative_volume_metric: float
    neighboring_volume_ratio: float
    width_ticks: int
    persistence: int = 1
    age_events: int = 0
    retest_count: int = 0


class VolumeProfileSnapshot(StrictModel):
    schema_version: str = CONTRACT_VERSION
    ts_event_ns: int
    instrument: str
    active_contract: str
    profile_kind: str
    start_ns: int
    end_ns: int
    poc_ticks: int | None
    vah_ticks: int | None
    val_ticks: int | None
    hvn_zones: list[ProfileZone]
    lvn_zones: list[ProfileZone]
    volume_by_price: dict[int, int]
    profile_shape: str
    total_volume: int
    value_area_fraction: float


class AuctionFeatureSnapshot(StrictModel):
    schema_version: str = CONTRACT_VERSION
    ts_event_ns: int
    instrument: str
    active_contract: str
    state_candidate: MarketState
    value_area_overlap: float | None = None
    poc_migration_ticks: int | None = None
    vah_migration_ticks: int | None = None
    val_migration_ticks: int | None = None
    directional_efficiency: float | None = None
    range_expansion_ratio: float | None = None
    time_inside_value_fraction: float | None = None
    time_outside_value_fraction: float | None = None
    acceptance_outside_value: float | None = None
    return_to_value_frequency: float | None = None
    vwap_slope_ticks_per_event: float | None = None
    impulse_velocity_ticks_per_event: float | None = None
    pullback_depth_fraction: float | None = None
    delta_persistence: float | None = None
    cvd_persistence: float | None = None
    raw: dict[str, float] = Field(default_factory=dict)


class DataQualityFlags(StrictModel):
    gaps: bool = False
    out_of_order: bool = False
    impossible_book: bool = False
    negative_size: bool = False
    crossed_book: bool = False
    stale_stream: bool = False
    unknown_contract: bool = False
    missing_snapshot: bool = False
    timestamp_regression: bool = False
    duplicated_event: bool = False
    trade_safe: bool = True
    reasons: list[str] = Field(default_factory=list)


class MarketFeatureSnapshot(StrictModel):
    schema_version: str = CONTRACT_VERSION
    timestamp_ns: int
    instrument: str
    active_contract: str
    session: str
    best_bid_ticks: int | None
    best_ask_ticks: int | None
    last_trade_ticks: int | None
    structure_features: dict[str, float] = Field(default_factory=dict)
    auction: AuctionFeatureSnapshot
    profiles: list[VolumeProfileSnapshot]
    relevant_lvn_zones: list[ProfileZone]
    relevant_hvn_zones: list[ProfileZone]
    relevant_poc_candidates: list[int]
    delta: int
    cvd: int
    rolling_delta: int
    cvd_slope: float
    cvd_acceleration: float
    footprint: FootprintSnapshot
    aggression_metrics: dict[str, float]
    absorption_metrics: dict[str, float]
    big_print_metrics: dict[str, float]
    dom_depth_metrics: dict[str, float]
    liquidity_pulling_stacking: dict[str, float]
    data_quality: DataQualityFlags
    stale_data_flags: dict[str, bool] = Field(default_factory=dict)
    provenance: dict[str, str] = Field(default_factory=dict)


class DecisionSnapshot(StrictModel):
    """Transport placeholder for a later strategy package; contains no trade decision logic."""

    schema_version: str = CONTRACT_VERSION
    timestamp_ns: int
    instrument: str
    active_contract: str
    market_feature_sha256: str
    trade_safe: bool
    decision: Literal["UNSET"] = "UNSET"


CONTRACT_MODELS = [
    MarketEvent,
    TradePrint,
    BookSnapshot,
    FootprintSnapshot,
    VolumeProfileSnapshot,
    AuctionFeatureSnapshot,
    MarketFeatureSnapshot,
    DecisionSnapshot,
]


def canonical_schema_bundle() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "models": {m.__name__: m.model_json_schema() for m in CONTRACT_MODELS},
    }


def canonical_schema_bytes() -> bytes:
    return json.dumps(canonical_schema_bundle(), sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_schema_hash() -> str:
    return hashlib.sha256(canonical_schema_bytes()).hexdigest()


def snapshot_hash(snapshot: MarketFeatureSnapshot) -> str:
    payload = snapshot.model_dump(mode="json")
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
