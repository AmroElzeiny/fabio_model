"""Small adapters from the two pre-integration contracts to the canonical contract.

Only field *names/units* are adapted here; no trading logic lives in this module.
Package 2 never produced serialized artifacts that must be read back (its snapshots were
in-memory fixtures), so only the Package 1 ``MarketFeatureSnapshot`` JSON needs migration.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .models import MarketFeatureSnapshot

# Package 2 name -> canonical name (units converted from Decimal price to integer ticks).
P2_TO_CANONICAL_FIELDS: dict[str, str] = {
    "MarketFeatureSnapshot.contract_version": "MarketFeatureSnapshot.schema_version",
    "MarketFeatureSnapshot.timestamp": "MarketFeatureSnapshot.timestamp_ns",
    "MarketFeatureSnapshot.last_price": "MarketFeatureSnapshot.last_trade_ticks",
    "MarketFeatureSnapshot.bid": "MarketFeatureSnapshot.best_bid_ticks",
    "MarketFeatureSnapshot.ask": "MarketFeatureSnapshot.best_ask_ticks",
    "MarketFeatureSnapshot.volume_profile": "MarketFeatureSnapshot.profiles[0] (+ relevant_poc_candidates for targets)",
    "MarketFeatureSnapshot.cvd_slope": "MarketFeatureSnapshot.cvd_slope_normalized",
    "MarketFeatureSnapshot.big_print_percentile": "MarketFeatureSnapshot.big_buy_print_percentile / big_sell_print_percentile",
    "MarketFeatureSnapshot.stacked_imbalance_ratio/stacked_levels": "computed from MarketFeatureSnapshot.footprint.levels",
    "MarketFeatureSnapshot.trade_safe": "MarketFeatureSnapshot.data_quality.trade_safe (read-only property trade_safe)",
    "AuctionFeatureSnapshot.balance_low": "AuctionFeatureSnapshot.balance_low_ticks",
    "AuctionFeatureSnapshot.balance_high": "AuctionFeatureSnapshot.balance_high_ticks",
    "AuctionFeatureSnapshot.balance_poc": "AuctionFeatureSnapshot.balance_poc_ticks",
    "AuctionFeatureSnapshot.value_overlap": "AuctionFeatureSnapshot.balance_value_overlap",
    "AuctionFeatureSnapshot.poc_migration_ticks": "AuctionFeatureSnapshot.balance_poc_migration_ticks",
    "AuctionFeatureSnapshot.directional_efficiency": "AuctionFeatureSnapshot.balance_directional_efficiency",
    "AuctionFeatureSnapshot.impulse_low/high (trend)": "AuctionFeatureSnapshot.impulse_low_ticks/impulse_high_ticks",
    "AuctionFeatureSnapshot.impulse_id (mean reversion)": "AuctionFeatureSnapshot.reclaim_leg_id",
    "AuctionFeatureSnapshot.impulse_low/high (mean reversion stop)": "AuctionFeatureSnapshot.failed_auction_extreme_ticks",
    "VolumeNode.low/high": "ProfileZone.low_ticks/high_ticks",
    "VolumeNode.relative_volume": "ProfileZone.volume_to_median_ratio",
    "VolumeNode.retests": "ProfileZone.retest_count",
    "VolumeNode.age_events": "ProfileZone.age_events (structure periods)",
    "TradeCandidate.entry_reference/stop_price/target_price": "TradeCandidate.*_ticks",
    "ExecutionIntent.quantity (Decimal)": "ExecutionIntent.quantity (int contracts)",
    "Session label NEW_YORK": "NY_RTH",
}

_NY = ZoneInfo("America/New_York")


def _trading_day(ts_ns: int) -> str:
    local = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).astimezone(_NY)
    if local.hour >= 18:
        local = local + timedelta(days=1)
    return local.date().isoformat()


def migrate_p1_market_feature_snapshot(
    payload: dict[str, Any], *, event_index: int = 0, tick_size: Decimal = Decimal("0.25"), period_seconds: int = 60
) -> MarketFeatureSnapshot:
    """Upgrade a Package 1 ``MarketFeatureSnapshot`` JSON object to the canonical contract.

    Fields that Package 1 never produced (structure context, directional aggression) take
    their neutral defaults, so a migrated snapshot can never satisfy an entry condition by
    accident.
    """
    data = dict(payload)
    ts = int(data["timestamp_ns"])
    data.setdefault("event_index", event_index)
    data.setdefault("structure_period_index", ts // (period_seconds * 1_000_000_000))
    data.setdefault("tick_size", tick_size)
    data.setdefault("trading_day", _trading_day(ts))
    return MarketFeatureSnapshot.model_validate(data)
