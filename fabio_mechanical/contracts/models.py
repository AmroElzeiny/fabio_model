"""Canonical FABIO_MECHANICAL_V1 contract.

This is the ONLY schema definition in the integrated system. It is the superset of the
Package 1 (market replay) and Package 2 (strategy/execution) contracts:

* prices intended for exact arithmetic are integer ticks (Package 1 convention);
* timestamps are UTC nanoseconds (Package 1 convention);
* money is ``Decimal`` (Package 2 convention);
* strategy-facing auction/structure fields expected by the Package 2 FSMs are carried
  explicitly (``balance_*``, ``breakout_*``, ``impulse_*``, ``reclaim_leg_*`` ...).

Field-name reconciliation with the two pre-integration contracts is documented in
``contracts/legacy.py`` and ``INTEGRATION_REPORT.md``.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "FABIO_MECHANICAL_V1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- enums


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
    """Book side. For TRADE events the side is the AGGRESSOR side (BID = buyer lifted the ask)."""

    BID = "BID"
    ASK = "ASK"
    NONE = "NONE"


class MarketState(str, Enum):
    BALANCE = "BALANCE"
    IMBALANCE_UP = "IMBALANCE_UP"
    IMBALANCE_DOWN = "IMBALANCE_DOWN"
    TRANSITION = "TRANSITION"
    UNKNOWN = "UNKNOWN"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class SetupType(str, Enum):
    TREND = "TREND"
    MEAN_REVERSION = "MEAN_REVERSION"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OrderPurpose(str, Enum):
    ENTRY = "ENTRY"
    STOP = "STOP"
    TARGET = "TARGET"
    EXIT = "EXIT"


class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ExitReason(str, Enum):
    TARGET = "TARGET"
    STOP = "STOP"
    INVALIDATION = "INVALIDATION"
    EARLY_EXIT = "EARLY_EXIT"
    SESSION_END = "SESSION_END"
    END_OF_DATA = "END_OF_DATA"
    CONTRACT_ROLL = "CONTRACT_ROLL"
    KILL_SWITCH = "KILL_SWITCH"


# --------------------------------------------------------------------------- raw market data


class MarketEvent(StrictModel):
    schema_version: str = CONTRACT_VERSION
    ts_event_ns: int = Field(ge=0)
    ts_recv_ns: int | None = Field(default=None, ge=0)
    sequence: int = Field(ge=0)
    instrument: str
    active_contract: str
    instrument_id: int | None = None
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
    def validate_order_fields(self) -> MarketEvent:
        if self.action in {EventAction.ADD, EventAction.MODIFY, EventAction.CANCEL, EventAction.FILL} and self.order_id is None:
            raise ValueError("order_id required for order-level book actions")
        if self.action in {EventAction.ADD, EventAction.MODIFY, EventAction.TRADE, EventAction.FILL} and self.price_ticks is None:
            raise ValueError("price_ticks required for priced events")
        return self

    @property
    def clock_ns(self) -> int:
        """Replay clock: receive time when the provider supplies it (Databento files are ts_recv ordered)."""
        return self.ts_recv_ns if self.ts_recv_ns is not None else self.ts_event_ns


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
    mbo_available: bool = False
    queue_metrics: dict[str, float] = Field(default_factory=dict)


# --------------------------------------------------------------------------- derived features


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
    volume_to_median_ratio: float = 1.0
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


class BalanceArea(StrictModel):
    """A completed or developing balance (value) area. Its POC is a target candidate."""

    balance_id: str
    source: Literal["intraday_balance", "prior_session"]
    active_contract: str
    low_ticks: int
    high_ticks: int
    poc_ticks: int
    range_low_ticks: int
    range_high_ticks: int
    start_ns: int
    end_ns: int


class AuctionFeatureSnapshot(StrictModel):
    schema_version: str = CONTRACT_VERSION
    ts_event_ns: int
    instrument: str
    active_contract: str
    # --- Package 1 auction measurements (session-level) ---
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
    # --- structure context consumed by the Fabio FSMs (Package 2 fields, tick units) ---
    balanced: bool = False
    out_of_balance: bool = False
    balance_id: str | None = None
    balance_low_ticks: int | None = None
    balance_high_ticks: int | None = None
    balance_poc_ticks: int | None = None
    balance_range_low_ticks: int | None = None
    balance_range_high_ticks: int | None = None
    balance_value_overlap: float | None = None
    balance_poc_migration_ticks: int | None = None
    balance_directional_efficiency: float | None = None
    excursion_active: bool = False
    breakout_side: Direction = Direction.FLAT
    breakout_distance_ticks: float = 0.0
    acceptance_events: int = 0
    displacement_zscore: float = 0.0
    structure_break: bool = False
    impulse_id: str | None = None
    impulse_low_ticks: int | None = None
    impulse_high_ticks: int | None = None
    impulse_ticks: float = 0.0
    impulse_efficiency: float | None = None
    reclaim_inside_balance: bool = False
    failed_auction: bool = False
    failed_auction_extreme_ticks: int | None = None
    reclaim_leg_id: str | None = None
    reclaim_leg_low_ticks: int | None = None
    reclaim_leg_high_ticks: int | None = None
    reclaim_leg_ticks: float = 0.0


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
    checksum_mismatch: bool = False
    trade_safe: bool = True
    reasons: list[str] = Field(default_factory=list)


class MarketFeatureSnapshot(StrictModel):
    """Sole strategy input. Produced only from information available at ``timestamp_ns``."""

    schema_version: str = CONTRACT_VERSION
    timestamp_ns: int
    event_index: int = Field(ge=0)
    structure_period_index: int
    instrument: str
    active_contract: str
    tick_size: Decimal
    session: str
    trading_day: str
    best_bid_ticks: int | None
    best_ask_ticks: int | None
    last_trade_ticks: int | None
    structure_direction: Direction = Direction.FLAT
    structure_features: dict[str, float] = Field(default_factory=dict)
    auction: AuctionFeatureSnapshot
    profiles: list[VolumeProfileSnapshot]
    impulse_profile: VolumeProfileSnapshot | None = None
    reclaim_profile: VolumeProfileSnapshot | None = None
    balance_areas: list[BalanceArea] = Field(default_factory=list)
    relevant_lvn_zones: list[ProfileZone]
    relevant_hvn_zones: list[ProfileZone]
    relevant_poc_candidates: list[int]
    delta: int
    cvd: int
    rolling_delta: int
    cvd_slope: float
    cvd_acceleration: float
    cvd_slope_normalized: float = 0.0
    delta_zscore: float = 0.0
    big_buy_print_percentile: float = 0.0
    big_sell_print_percentile: float = 0.0
    aggression_window_low_ticks: int | None = None
    aggression_window_high_ticks: int | None = None
    absorption_score: float = 0.0
    rejection_speed: float = 0.0
    pulling_stacking_score: float = 0.0
    directional_aggression: Direction = Direction.FLAT
    footprint: FootprintSnapshot
    book: BookSnapshot | None = None
    aggression_metrics: dict[str, float]
    absorption_metrics: dict[str, float]
    big_print_metrics: dict[str, float]
    dom_depth_metrics: dict[str, float]
    liquidity_pulling_stacking: dict[str, float]
    data_quality: DataQualityFlags
    data_quality_score: float = Field(default=1.0, ge=0.0, le=1.0)
    stale_ms: int = Field(default=0, ge=0)
    stale_data_flags: dict[str, bool] = Field(default_factory=dict)
    news_day: bool | None = None
    volatility_regime: Literal["LOW", "NORMAL", "HIGH"] = "NORMAL"
    market_regime: Literal["BALANCE", "TREND", "TRANSITION"] = "TRANSITION"
    provenance: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_market(self) -> MarketFeatureSnapshot:
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive")
        if (
            self.best_bid_ticks is not None
            and self.best_ask_ticks is not None
            and self.best_ask_ticks < self.best_bid_ticks
            and self.data_quality.trade_safe
        ):
            raise ValueError("crossed book cannot be marked trade_safe")
        return self

    @property
    def trade_safe(self) -> bool:
        return self.data_quality.trade_safe


# --------------------------------------------------------------------------- decisions & execution


class DecisionSnapshot(StrictModel):
    schema_version: str = CONTRACT_VERSION
    timestamp_ns: int
    instrument: str
    active_contract: str
    setup_type: SetupType | None = None
    direction: Direction = Direction.FLAT
    state: str = ""
    decision: Literal["UNSET", "ENTER", "NO_TRADE"] = "UNSET"
    evidence: dict[str, Any] = Field(default_factory=dict)
    parameter_version: str = ""
    config_hash: str = ""
    market_feature_sha256: str | None = None
    trade_safe: bool
    provider: str = "MECHANICAL"
    ai_enabled: Literal[False] = False


class TradeCandidate(StrictModel):
    schema_version: str = CONTRACT_VERSION
    candidate_id: str
    setup_type: SetupType
    direction: Direction
    timestamp_ns: int
    event_index: int
    instrument: str
    active_contract: str
    entry_reference_ticks: int
    stop_ticks: int
    target_ticks: int
    evidence: dict[str, Any]
    decision_snapshot: DecisionSnapshot

    @model_validator(mode="after")
    def validate_geometry(self) -> TradeCandidate:
        if self.direction == Direction.LONG:
            ok = self.stop_ticks < self.entry_reference_ticks < self.target_ticks
        elif self.direction == Direction.SHORT:
            ok = self.target_ticks < self.entry_reference_ticks < self.stop_ticks
        else:
            ok = False
        if not ok:
            raise ValueError("candidate requires stop < entry < target (long) or target < entry < stop (short)")
        return self


class ExecutionIntent(StrictModel):
    schema_version: str = CONTRACT_VERSION
    intent_id: str
    candidate_id: str
    timestamp_ns: int
    instrument: str
    active_contract: str
    direction: Direction
    purpose: OrderPurpose = OrderPurpose.ENTRY
    order_type: OrderType
    quantity: int = Field(gt=0)
    limit_price_ticks: int | None = None
    trigger_price_ticks: int | None = None
    reference_price_ticks: int | None = None
    stop_price_ticks: int | None = None
    target_price_ticks: int | None = None
    max_slippage_ticks: int = Field(default=10, ge=0)

    @model_validator(mode="after")
    def validate_prices(self) -> ExecutionIntent:
        if self.direction == Direction.FLAT:
            raise ValueError("order direction must be LONG (buy) or SHORT (sell)")
        if self.order_type == OrderType.LIMIT and self.limit_price_ticks is None:
            raise ValueError("LIMIT requires limit_price_ticks")
        if self.order_type == OrderType.STOP and self.trigger_price_ticks is None:
            raise ValueError("STOP requires trigger_price_ticks")
        return self


class Fill(StrictModel):
    timestamp_ns: int
    price_ticks: int
    quantity: int = Field(gt=0)
    fee: Decimal = Decimal("0")
    slippage_ticks: int = 0
    liquidity: Literal["TAKER", "MAKER"] = "TAKER"


class ExecutionReport(StrictModel):
    schema_version: str = CONTRACT_VERSION
    intent_id: str
    status: OrderStatus
    fills: list[Fill] = Field(default_factory=list)
    rejected_reason: str | None = None
    latency_ms: int = 0
    total_quantity: int = 0
    filled_quantity: int = 0
    average_fill_price_ticks: Decimal | None = None


CONTRACT_MODELS: list[type[BaseModel]] = [
    MarketEvent,
    TradePrint,
    BookSnapshot,
    FootprintSnapshot,
    VolumeProfileSnapshot,
    BalanceArea,
    AuctionFeatureSnapshot,
    DataQualityFlags,
    MarketFeatureSnapshot,
    DecisionSnapshot,
    TradeCandidate,
    ExecutionIntent,
    ExecutionReport,
]
