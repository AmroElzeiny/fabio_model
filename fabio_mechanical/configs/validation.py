"""Strict validation models for every configuration file."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KNOWN_SESSION_LABELS = {"NY_RTH", "LONDON", "POST_RTH", "MAINTENANCE", "GLOBEX_OVERNIGHT"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionWindows(_Strict):
    trend: list[str]
    mean_reversion: list[str]

    @field_validator("trend", "mean_reversion")
    @classmethod
    def known_labels(cls, v: list[str]) -> list[str]:
        unknown = set(v) - KNOWN_SESSION_LABELS
        if unknown:
            raise ValueError(f"unknown session labels {sorted(unknown)}; known: {sorted(KNOWN_SESSION_LABELS)}")
        return v


class StrategyParams(_Strict):
    balance_value_overlap_min: float = Field(ge=0, le=1)
    poc_migration_max: float = Field(ge=0)
    directional_efficiency_min: float = Field(ge=0, le=1)
    directional_efficiency_max_for_balance: float = Field(ge=0, le=1)
    acceptance_event_count: int = Field(ge=1)
    breakout_distance_ticks: float = Field(gt=0)
    displacement_zscore_min: float
    impulse_min_ticks: float = Field(gt=0)
    pullback_depth_min: float = Field(gt=0, lt=1)
    pullback_depth_max: float = Field(gt=0, le=1)
    lvn_relative_volume_max: float = Field(gt=0)
    lvn_neighbor_ratio: float = Field(gt=0)
    lvn_min_width_ticks: int = Field(ge=1)
    lvn_max_age_events: int = Field(ge=0)
    lvn_max_retests: int = Field(ge=0)
    aggression_delta_zscore: float
    aggression_persistence: int = Field(ge=1)
    stacked_imbalance_ratio: float = Field(gt=1)
    stacked_levels_min: int = Field(ge=1)
    big_print_percentile: float = Field(ge=0, le=1)
    cvd_slope_min: float = Field(ge=0, le=1)
    severe_opposing_absorption: float = Field(gt=0, le=1)
    rejection_speed_min: float = Field(ge=0, le=1)
    stop_buffer_ticks: int = Field(ge=0)
    setup_expiry_events: int = Field(ge=1)
    data_quality_min: float = Field(ge=0, le=1)
    stale_data_ms_max: int = Field(ge=0)
    session_windows: SessionWindows

    @model_validator(mode="after")
    def ordered(self) -> StrategyParams:
        if self.pullback_depth_min >= self.pullback_depth_max:
            raise ValueError("pullback_depth_min must be < pullback_depth_max")
        if self.directional_efficiency_max_for_balance >= self.directional_efficiency_min:
            raise ValueError("balance efficiency ceiling must be below the out-of-balance efficiency floor")
        return self


class StructureParams(_Strict):
    period_seconds: int = Field(ge=1)
    sample_seconds: int = Field(ge=1)
    balance_window_minutes: int = Field(ge=2)
    displacement_lookback_periods: int = Field(ge=2)
    displacement_min_periods: int = Field(ge=2)
    max_archived_balances: int = Field(ge=1)
    max_prior_sessions: int = Field(ge=1)

    @model_validator(mode="after")
    def windows(self) -> StructureParams:
        if (self.balance_window_minutes * 60) % (2 * self.period_seconds) != 0:
            raise ValueError("balance window must split into two whole numbers of structure periods")
        if self.sample_seconds > self.period_seconds:
            raise ValueError("sample_seconds must be <= period_seconds")
        return self


class MarketStateThresholds(_Strict):
    balance_efficiency_max: float
    imbalance_efficiency_min: float
    range_expansion_min: float
    delta_persistence_min: float


class MarketParams(_Strict):
    top_n_depth: int = Field(ge=1)
    value_area_fraction: float = Field(gt=0, le=1)
    hvn_quantile: float = Field(ge=0, le=1)
    lvn_quantile: float = Field(ge=0, le=1)
    imbalance_ratio: float = Field(gt=1)
    stacked_levels: int = Field(ge=1)
    big_print_percentile: float = Field(ge=0, le=1)
    rolling_trade_window: int = Field(ge=2)
    auction_window: int = Field(ge=2)
    absorption_price_progress_floor_ticks: float = Field(gt=0)
    absorption_ratio_scale: float = Field(gt=0)
    iceberg_replenishment_scale: float = Field(gt=0)
    session_profile_refresh_ms: int = Field(ge=0)
    market_state: MarketStateThresholds


class ExperimentalParams(_Strict):
    experimental_early_exit_enabled: bool = False

    @model_validator(mode="after")
    def names(self) -> ExperimentalParams:
        for key in type(self).model_fields:
            if not key.startswith("experimental_"):
                raise ValueError(f"experimental flag {key} must be named experimental_*")
        return self


class Interpretation(_Strict):
    version: str
    AI_ENABLED: Literal[False]
    strategy: StrategyParams
    structure: StructureParams
    market: MarketParams
    experimental: ExperimentalParams


class RiskParams(_Strict):
    initial_equity: Decimal = Field(gt=0)
    risk_fraction: float = Field(gt=0, le=0.05)
    max_risk_fraction: float = Field(gt=0, le=0.05)
    max_daily_loss_fraction: float = Field(gt=0, le=1)
    max_weekly_loss_fraction: float = Field(gt=0, le=1)
    weekly_loss_lock_enabled: bool
    max_consecutive_losses: int = Field(ge=1)
    loss_streak_reset: Literal["daily", "never"]
    max_concurrent_exposure_fraction: float = Field(gt=0, le=1)
    max_position_contracts: int = Field(ge=1)
    session_trade_cap: int = Field(ge=1)
    cooldown_events: int = Field(ge=0)
    flatten_time: time
    flatten_timezone: str

    @model_validator(mode="after")
    def caps(self) -> RiskParams:
        if self.risk_fraction > self.max_risk_fraction:
            raise ValueError("risk_fraction must not exceed max_risk_fraction")
        return self


class BracketSpec(_Strict):
    stop: Literal["STOP_MARKET"]
    target: Literal["LIMIT"]
    oco: Literal["server_side_atomic"]


class ExecutionParams(_Strict):
    latency_ms: int = Field(ge=0)
    default_slippage_ticks: int = Field(ge=0)
    max_slippage_ticks: int = Field(ge=0)
    commission_per_contract: Decimal = Field(ge=0)
    fee_per_contract: Decimal = Field(ge=0)
    queue_fill_fraction: float = Field(gt=0, le=1)
    max_gap_ticks: int = Field(ge=0)
    allow_partial_fills: bool
    entry_order_type: Literal["MARKET"]
    bracket: BracketSpec


class InstrumentSpecModel(_Strict):
    tick_size: Decimal = Field(gt=0)
    tick_value: Decimal = Field(gt=0)
    point_value: Decimal = Field(gt=0)


class QualityParams(_Strict):
    max_sequence_gap: int = Field(ge=1)
    dedupe_window: int = Field(ge=1)


class SessionWindowSpec(_Strict):
    label: str
    timezone: str
    start: time
    end: time

    @field_validator("label")
    @classmethod
    def known(cls, v: str) -> str:
        if v not in KNOWN_SESSION_LABELS:
            raise ValueError(f"unknown session label {v}")
        return v


class SessionSpec(_Strict):
    exchange_timezone: str
    trading_day_start: time
    windows: list[SessionWindowSpec]
    default_label: str


class DataParams(_Strict):
    dataset: str
    default_symbol: str
    stype_in: str
    preferred_schema: Literal["mbo", "mbp-10", "trades"]
    raw_dir: str
    cache_dir: str
    runs_dir: str
    known_contract_prefixes: list[str]
    instruments: dict[str, InstrumentSpecModel]
    quality: QualityParams
    sessions: SessionSpec


class DateRange(_Strict):
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self) -> DateRange:
        if self.end < self.start:
            raise ValueError("date range end < start")
        return self


class Phases(_Strict):
    development: DateRange | None = None
    validation: DateRange | None = None
    oos: DateRange | None = None

    @model_validator(mode="after")
    def disjoint(self) -> Phases:
        ranges = [r for r in (self.development, self.validation, self.oos) if r is not None]
        ranges.sort(key=lambda r: r.start)
        for a, b in zip(ranges, ranges[1:], strict=False):
            if b.start <= a.end:
                raise ValueError("development/validation/oos date ranges must not overlap")
        if self.development and self.oos and self.oos.start <= self.development.end:
            raise ValueError("OOS must be strictly after development")
        return self


class OptimizerParams(_Strict):
    mode: Literal["grid", "random"]
    max_candidates: int = Field(ge=1)
    plateau_tolerance: float = Field(ge=0, le=1)


class WalkForwardParams(_Strict):
    train_days: int = Field(ge=1)
    validation_days: int = Field(ge=1)
    step_days: int = Field(ge=1)
    max_candidates: int = Field(ge=1)


class MonteCarloParams(_Strict):
    iterations: int = Field(ge=1)
    block_size: int = Field(ge=1)


class RobustnessParams(_Strict):
    fee_multipliers: list[float]
    slippage_multipliers: list[int]
    latency_ms: list[int]


class ResearchParams(_Strict):
    min_trades: int = Field(ge=0)
    deterministic_seed: int
    phases: Phases
    optimizer: OptimizerParams
    walk_forward: WalkForwardParams
    monte_carlo: MonteCarloParams
    robustness: RobustnessParams


class SearchDimension(_Strict):
    definition: str
    units: str
    min: float
    max: float
    step: float = Field(gt=0)
    rationale: str

    @model_validator(mode="after")
    def bounds(self) -> SearchDimension:
        if self.max < self.min:
            raise ValueError("search dimension max < min")
        return self


class SearchSpace(_Strict):
    parameters: dict[str, SearchDimension]


class InvariantRules(BaseModel):
    model_config = ConfigDict(extra="allow")
    never_widen_stop: Literal[True]
    risk_locks_non_overridable: Literal[True]
    unsafe_data_blocks_trading: Literal[True]
    ai_enabled: Literal[False]
    ai_can_override_risk: Literal[False]
    experimental_features_default_off: Literal[True]
    decision_order: list[str]

    @field_validator("decision_order")
    @classmethod
    def order(cls, v: list[str]) -> list[str]:
        if v != ["market_state", "location", "aggression"]:
            raise ValueError("decision order must be market_state -> location -> aggression")
        return v


class Invariants(_Strict):
    contract_version: Literal["FABIO_MECHANICAL_V1"]
    frozen: Literal[True]
    rules: InvariantRules
