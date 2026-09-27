from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

CONTRACT_VERSION = "FABIO_MECHANICAL_V1"

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
    KILL_SWITCH = "KILL_SWITCH"

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

class MarketEvent(StrictModel):
    timestamp: datetime
    instrument: str
    event_type: str
    sequence: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)

class TradePrint(StrictModel):
    timestamp: datetime
    price: Decimal
    size: Decimal
    aggressor: Literal["BUY", "SELL", "UNKNOWN"] = "UNKNOWN"

class BookLevel(StrictModel):
    price: Decimal
    size: Decimal
    queue_ahead: Decimal = Decimal("0")

class BookSnapshot(StrictModel):
    timestamp: datetime
    bids: list[BookLevel] = Field(default_factory=list)
    asks: list[BookLevel] = Field(default_factory=list)
    mbo_available: bool = False

class FootprintLevel(StrictModel):
    price: Decimal
    bid_volume: Decimal = Decimal("0")
    ask_volume: Decimal = Decimal("0")
    delta: Decimal = Decimal("0")
    imbalance_ratio: Decimal = Decimal("1")

class FootprintSnapshot(StrictModel):
    timestamp: datetime
    levels: list[FootprintLevel] = Field(default_factory=list)
    stacked_buy_levels: int = 0
    stacked_sell_levels: int = 0

class VolumeNode(StrictModel):
    low: Decimal
    high: Decimal
    relative_volume: float
    age_events: int = 0
    retests: int = 0

class VolumeProfileSnapshot(StrictModel):
    timestamp: datetime
    poc: Decimal
    vah: Decimal
    val: Decimal
    hvn_zones: list[VolumeNode] = Field(default_factory=list)
    lvn_zones: list[VolumeNode] = Field(default_factory=list)

class AuctionFeatureSnapshot(StrictModel):
    balanced: bool
    out_of_balance: bool
    balance_low: Decimal
    balance_high: Decimal
    balance_poc: Decimal
    value_overlap: float = 1.0
    poc_migration_ticks: float = 0.0
    directional_efficiency: float = 0.0
    breakout_side: Direction = Direction.FLAT
    breakout_distance_ticks: float = 0.0
    acceptance_events: int = 0
    displacement_zscore: float = 0.0
    structure_break: bool = False
    impulse_id: str | None = None
    impulse_low: Decimal | None = None
    impulse_high: Decimal | None = None
    impulse_ticks: float = 0.0
    reclaim_inside_balance: bool = False
    failed_auction: bool = False

class MarketFeatureSnapshot(StrictModel):
    contract_version: str = CONTRACT_VERSION
    timestamp: datetime
    instrument: str
    session: str
    event_index: int
    last_price: Decimal
    bid: Decimal
    ask: Decimal
    tick_size: Decimal
    structure_direction: Direction = Direction.FLAT
    auction: AuctionFeatureSnapshot
    volume_profile: VolumeProfileSnapshot
    impulse_profile: VolumeProfileSnapshot | None = None
    reclaim_profile: VolumeProfileSnapshot | None = None
    delta_zscore: float = 0.0
    cvd_slope: float = 0.0
    big_print_percentile: float = 0.0
    stacked_imbalance_ratio: float = 1.0
    stacked_levels: int = 0
    absorption_score: float = 0.0
    rejection_speed: float = 0.0
    pulling_stacking_score: float = 0.0
    directional_aggression: Direction = Direction.FLAT
    footprint: FootprintSnapshot | None = None
    book: BookSnapshot | None = None
    data_quality_score: float = 1.0
    trade_safe: bool = True
    stale_ms: int = 0
    news_day: bool | None = None
    volatility_regime: Literal["LOW", "NORMAL", "HIGH"] = "NORMAL"
    market_regime: Literal["BALANCE", "TREND", "TRANSITION"] = "TRANSITION"

    @model_validator(mode="after")
    def validate_market(self):
        if self.ask < self.bid:
            raise ValueError("ask must be >= bid")
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive")
        return self

class DecisionSnapshot(StrictModel):
    timestamp: datetime
    instrument: str
    setup_type: SetupType
    direction: Direction
    state: str
    evidence: dict[str, Any]
    parameter_version: str
    config_hash: str
    trade_safe: bool
    provider: str = "MECHANICAL"
    ai_enabled: bool = False

class TradeCandidate(StrictModel):
    candidate_id: str
    setup_type: SetupType
    direction: Direction
    timestamp: datetime
    instrument: str
    entry_reference: Decimal
    stop_price: Decimal
    target_price: Decimal
    evidence: dict[str, Any]
    decision_snapshot: DecisionSnapshot

class ExecutionIntent(StrictModel):
    intent_id: str
    candidate_id: str
    timestamp: datetime
    instrument: str
    direction: Direction
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    reference_price: Decimal | None = None
    stop_price: Decimal
    target_price: Decimal
    max_slippage_ticks: int = 10

class Fill(StrictModel):
    timestamp: datetime
    price: Decimal
    quantity: Decimal
    fee: Decimal = Decimal("0")
    slippage_ticks: Decimal = Decimal("0")

class ExecutionReport(StrictModel):
    intent_id: str
    status: OrderStatus
    fills: list[Fill] = Field(default_factory=list)
    rejected_reason: str | None = None
    latency_ms: int = 0
    total_quantity: Decimal = Decimal("0")
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Decimal | None = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
