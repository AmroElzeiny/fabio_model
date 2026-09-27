"""Canonical MarketFeatureSnapshot fixtures for FSM/risk unit tests (Package 2 fixtures, tick units).

Geometry (ticks, 0.25 index points each):
* trend long : balance 380-400 POC 390; impulse 388->416; LVN 403-405; price 404; next POC above 420
* trend short: balance 420-440 POC 430; impulse 432->404; LVN 413-415; price 414; next POC below 400
* MR long    : balance 400-440 POC 420; failed downside auction extreme 396; reclaim leg 396->416;
               LVN 407-409; price 408 -> target POC 420, stop 394
* MR short   : balance 400-440 POC 420; failed upside auction extreme 444; reclaim leg 444->424;
               LVN 431-433; price 432 -> target POC 420, stop 446
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fabio_mechanical.contracts import (
    AuctionFeatureSnapshot,
    BookLevel,
    BookSnapshot,
    DataQualityFlags,
    Direction,
    FootprintSnapshot,
    MarketFeatureSnapshot,
    MarketState,
    ProfileZone,
    VolumeProfileSnapshot,
)

BASE_NS = 1_768_314_600_000_000_000  # 2026-01-13 14:30:00 UTC (09:30 ET)


def zone(low: int, high: int, ratio: float = 0.3, retests: int = 0, age: int = 2, neighbor_ratio: float = 3.0) -> ProfileZone:
    return ProfileZone(
        low_ticks=low,
        high_ticks=high,
        relative_volume_metric=1 - ratio,
        neighboring_volume_ratio=neighbor_ratio,
        width_ticks=high - low + 1,
        volume_to_median_ratio=ratio,
        age_events=age,
        retest_count=retests,
    )


def profile(ts: int, poc: int, vah: int, val: int, lvns: list[ProfileZone], kind: str = "impulse_leg") -> VolumeProfileSnapshot:
    return VolumeProfileSnapshot(
        ts_event_ns=ts,
        instrument="NQ",
        active_contract="NQH6",
        profile_kind=kind,
        start_ns=ts,
        end_ns=ts,
        poc_ticks=poc,
        vah_ticks=vah,
        val_ticks=val,
        hvn_zones=[],
        lvn_zones=lvns,
        volume_by_price={poc: 10},
        profile_shape="D_SHAPE",
        total_volume=10,
        value_area_fraction=0.7,
    )


def snapshot(
    i: int = 1,
    direction: Direction = Direction.LONG,
    setup: str = "trend",
    *,
    price: int | None = None,
    safe: bool = True,
    stale: int = 0,
    absorption: float = 0.1,
    aggression: bool = True,
    session: str | None = None,
    auction: dict[str, Any] | None = None,
    **overrides: Any,
) -> MarketFeatureSnapshot:
    ts = BASE_NS + i * 1_000_000_000
    long = direction == Direction.LONG
    common_auction: dict[str, Any] = {
        "ts_event_ns": ts,
        "instrument": "NQ",
        "active_contract": "NQH6",
        "state_candidate": MarketState.TRANSITION,
    }
    if setup == "trend":
        if long:
            a = dict(
                balance_low_ticks=380,
                balance_high_ticks=400,
                balance_poc_ticks=390,
                balance_range_low_ticks=376,
                balance_range_high_ticks=402,
                impulse_low_ticks=388,
                impulse_high_ticks=416,
                breakout_side=Direction.LONG,
            )
            lvn, px, pocs, win = zone(403, 405), 404, [390, 420], (402, 406)
            ip = profile(ts, 410, 414, 396, [lvn])
        else:
            a = dict(
                balance_low_ticks=420,
                balance_high_ticks=440,
                balance_poc_ticks=430,
                balance_range_low_ticks=418,
                balance_range_high_ticks=444,
                impulse_low_ticks=404,
                impulse_high_ticks=432,
                breakout_side=Direction.SHORT,
            )
            lvn, px, pocs, win = zone(413, 415), 414, [400, 430], (412, 416)
            ip = profile(ts, 410, 424, 406, [lvn])
        a.update(
            balanced=False,
            out_of_balance=True,
            balance_id="B1",
            excursion_active=True,
            breakout_distance_ticks=24.0,
            acceptance_events=4,
            displacement_zscore=1.8,
            structure_break=True,
            impulse_id="B1:I:1",
            impulse_ticks=28.0,
            impulse_efficiency=0.8,
        )
        rp = None
        sess = session or "NY_RTH"
    else:
        a = dict(
            balance_low_ticks=400,
            balance_high_ticks=440,
            balance_poc_ticks=420,
            balance_range_low_ticks=398,
            balance_range_high_ticks=442,
            balanced=False,
            out_of_balance=False,
            balance_id="B2",
            excursion_active=False,
            acceptance_events=0,
            displacement_zscore=1.1,
            failed_auction=True,
            reclaim_inside_balance=True,
            reclaim_leg_id="B2:R:1",
        )
        if long:
            a.update(
                breakout_side=Direction.SHORT,
                breakout_distance_ticks=20.0,
                failed_auction_extreme_ticks=396,
                reclaim_leg_low_ticks=396,
                reclaim_leg_high_ticks=416,
                reclaim_leg_ticks=20.0,
            )
            lvn, px, pocs, win = zone(407, 409), 408, [420], (406, 410)
        else:
            a.update(
                breakout_side=Direction.LONG,
                breakout_distance_ticks=20.0,
                failed_auction_extreme_ticks=444,
                reclaim_leg_low_ticks=424,
                reclaim_leg_high_ticks=444,
                reclaim_leg_ticks=20.0,
            )
            lvn, px, pocs, win = zone(431, 433), 432, [420], (430, 434)
        rp = profile(ts, 420, 426, 414, [lvn], kind="reclaim_leg")
        ip = None
        sess = session or "LONDON"
    a.update(auction or {})
    px = price if price is not None else px
    agg = direction if aggression else Direction.FLAT
    sign = 1 if long else -1
    fp = FootprintSnapshot(
        ts_event_ns=ts, instrument="NQ", active_contract="NQH6", window_start_ns=ts, window_end_ns=ts, levels=[], total_volume=0, delta=0
    )
    book = BookSnapshot(
        ts_event_ns=ts,
        sequence=i,
        instrument="NQ",
        active_contract="NQH6",
        best_bid=BookLevel(price_ticks=px - 1, size=10, order_count=2),
        best_ask=BookLevel(price_ticks=px + 1, size=10, order_count=2),
        bids=[BookLevel(price_ticks=px - 1, size=10, order_count=2)],
        asks=[BookLevel(price_ticks=px + 1, size=10, order_count=2)],
        spread_ticks=2,
        weighted_bid_depth=10.0,
        weighted_ask_depth=10.0,
        dom_imbalance=0.0,
        mbo_available=True,
    )
    flags = DataQualityFlags() if safe else DataQualityFlags(missing_snapshot=True, trade_safe=False, reasons=["test_unsafe"])
    data: dict[str, Any] = dict(
        timestamp_ns=ts,
        event_index=i,
        structure_period_index=ts // 60_000_000_000,
        instrument="NQ",
        active_contract="NQH6",
        tick_size=Decimal("0.25"),
        session=sess,
        trading_day="2026-01-13",
        best_bid_ticks=px - 1,
        best_ask_ticks=px + 1,
        last_trade_ticks=px,
        structure_direction=direction if setup == "trend" else Direction.FLAT,
        auction=AuctionFeatureSnapshot(**common_auction, **a),
        profiles=[profile(ts, pocs[0], pocs[0] + 5, pocs[0] - 5, [], kind="developing_session")],
        impulse_profile=ip,
        reclaim_profile=rp,
        relevant_lvn_zones=[],
        relevant_hvn_zones=[],
        relevant_poc_candidates=pocs,
        delta=0,
        cvd=0,
        rolling_delta=0,
        cvd_slope=0.0,
        cvd_acceleration=0.0,
        cvd_slope_normalized=0.5 * sign,
        delta_zscore=1.6 * sign,
        big_buy_print_percentile=0.95 if long else 0.2,
        big_sell_print_percentile=0.95 if not long else 0.2,
        aggression_window_low_ticks=win[0],
        aggression_window_high_ticks=win[1],
        absorption_score=absorption,
        rejection_speed=0.8,
        pulling_stacking_score=0.7,
        directional_aggression=agg,
        footprint=fp,
        book=book,
        aggression_metrics={},
        absorption_metrics={},
        big_print_metrics={},
        dom_depth_metrics={},
        liquidity_pulling_stacking={},
        data_quality=flags,
        data_quality_score=1.0 if safe else 0.2,
        stale_ms=stale,
        market_regime="TREND" if setup == "trend" else "BALANCE",
    )
    data.update(overrides)
    return MarketFeatureSnapshot(**data)


def valid_trend_long() -> MarketFeatureSnapshot:
    return snapshot(1, Direction.LONG, "trend")


def valid_trend_short() -> MarketFeatureSnapshot:
    return snapshot(1, Direction.SHORT, "trend")


def valid_mean_reversion_long() -> MarketFeatureSnapshot:
    return snapshot(1, Direction.LONG, "mean")


def valid_mean_reversion_short() -> MarketFeatureSnapshot:
    return snapshot(1, Direction.SHORT, "mean")
