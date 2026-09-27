from __future__ import annotations

from fabio_mechanical.contracts import MarketState, Side, TradePrint
from fabio_mechanical.market.features import AuctionFeatureEngine
from fabio_mechanical.orderflow.engine import OrderFlowEngine
from fabio_mechanical.orderflow.footprint import FootprintEngine, RollingFootprint, stacked_imbalance_levels
from fabio_mechanical.profile.volume_profile import VolumeProfileEngine, ZoneTracker, profile_from_vap, zones


def t(i: int, p: int, size: int, side: Side) -> TradePrint:
    return TradePrint(
        ts_event_ns=i * 1_000_000_000, sequence=i, instrument="NQ", active_contract="NQH6", price_ticks=p, size=size, aggressor_side=side
    )


# ---------------------------------------------------------------- order flow (Package 1 + aggression measures)


def test_delta_cvd_and_rolling_delta() -> None:
    f = OrderFlowEngine(rolling_window=3)
    for x in [t(1, 100, 10, Side.BID), t(2, 101, 4, Side.ASK), t(3, 101, 5, Side.BID)]:
        f.consume(x)
    assert f.state.cvd == 11 and f.rolling_delta() == 11
    assert f.state.buy_volume == 15 and f.state.sell_volume == 4
    f.consume(t(4, 101, 2, Side.ASK))
    assert f.rolling_delta() == -4 + 5 - 2


def test_big_print_percentile_and_aggression() -> None:
    f = OrderFlowEngine(rolling_window=10, big_print_percentile=0.8)
    for i, s in enumerate([1, 2, 3, 4, 20], 1):
        f.consume(t(i, 100 + i, s, Side.BID if i % 2 else Side.ASK))
    m = f.big_print_metrics()
    assert m["threshold_contracts"] >= 4 and m["big_print_count"] >= 1
    assert 0 <= f.aggression_metrics()["persistence"] <= 1
    buy_pct, sell_pct = f.directional_big_print_percentiles()
    assert buy_pct == f.size_percentile_rank(20) == 0.9  # mid-rank of the largest buy print (20)
    assert sell_pct == f.size_percentile_rank(4)


def test_normalized_cvd_slope_and_delta_zscore() -> None:
    f = OrderFlowEngine(rolling_window=5)
    for i in range(1, 6):
        f.consume(t(i, 100, 3, Side.BID))
    assert abs(f.cvd_slope_normalized() - 1.0) < 1e-9
    g = OrderFlowEngine(rolling_window=5)
    for i in range(1, 40):
        g.consume(t(i, 100, 1, Side.BID if i % 2 else Side.ASK))
    for i in range(40, 46):
        g.consume(t(i, 100, 10, Side.BID))
    assert g.delta_zscore() > 1.0 and g.directional_aggression() == 1


def test_absorption_is_metric_not_boolean() -> None:
    f = OrderFlowEngine(rolling_window=10)
    for i in range(1, 6):
        f.consume(t(i, 100, 5, Side.BID))
    m = f.absorption_metrics()
    assert m["aggressive_volume_per_tick_progress"] == 25
    assert m["adverse_aggression_no_continuation"] == 1.0 and m["repeated_aggression_same_level"] == 1.0


def test_footprint_delta_diagonal_and_stacked_imbalance() -> None:
    fp = FootprintEngine(imbalance_ratio=2.0)
    trades = [t(1, 100, 2, Side.ASK), t(2, 101, 10, Side.BID), t(3, 102, 12, Side.BID)]
    for x in trades:
        fp.consume(x)
    s = fp.snapshot(trades[-1].ts_event_ns, "NQ", "NQH6")
    assert s.total_volume == 24 and s.delta == 20 and any(x.buy_imbalance for x in s.levels)
    assert stacked_imbalance_levels(s, buy=True, ratio=2.0) == 2
    last = fp.snapshot_last_events(2, 3_000_000_000, "NQ", "NQH6")
    assert last.total_volume == 22 and len(fp.time_bar_snapshots(1_000_000_000, "NQ", "NQH6")) >= 3


def test_rolling_footprint_window_and_gap_breaks_stack() -> None:
    rf = RollingFootprint(window=2, imbalance_ratio=2.0)
    for x in [t(1, 100, 5, Side.BID), t(2, 101, 5, Side.BID), t(3, 105, 5, Side.BID)]:
        rf.consume(x)
    s = rf.snapshot(3, "NQ", "NQH6")
    assert [lvl.price_ticks for lvl in s.levels] == [101, 105]
    assert stacked_imbalance_levels(s, buy=True, ratio=2.0) == 1  # 101 and 105 are not adjacent


# ---------------------------------------------------------------- profile (Package 1 + integration corrections)


def tr(i: int, p: int, s: int = 1) -> TradePrint:
    return TradePrint(ts_event_ns=i, sequence=i, instrument="NQ", active_contract="NQH6", price_ticks=p, size=s, aggressor_side=Side.BID)


def test_poc_vah_val_known_distribution() -> None:
    vp = VolumeProfileEngine(value_area_fraction=0.70)
    for x in [tr(1, 100, 10), tr(2, 101, 50), tr(3, 102, 30), tr(4, 103, 10)]:
        vp.consume(x)
    s = vp.snapshot(4, "NQ", "NQH6")
    assert s.poc_ticks == 101 and s.val_ticks is not None and s.vah_ticks is not None
    assert s.val_ticks <= s.poc_ticks <= s.vah_ticks and sum(s.volume_by_price.values()) == 100


def test_lvn_hvn_zones_and_valley_rule() -> None:
    vp = VolumeProfileEngine(hvn_quantile=0.75, lvn_quantile=0.25)
    for i, (p, v) in enumerate([(100, 40), (101, 5), (102, 4), (103, 45), (104, 6)], 1):
        vp.consume(tr(i, p, v))
    s = vp.snapshot(9, "NQ", "NQH6")
    assert s.hvn_zones and s.lvn_zones
    assert [(z.low_ticks, z.high_ticks) for z in s.lvn_zones] == [(101, 102)]  # 104 touches the edge: excess, not a valley
    z = s.lvn_zones[0]
    assert z.width_ticks == 2 and 0 < z.volume_to_median_ratio < 1 and z.neighboring_volume_ratio > 1


def test_untraded_ticks_count_as_zero_volume() -> None:
    p = profile_from_vap(
        {100: 30, 101: 30, 105: 30, 106: 30},
        ts_event_ns=1,
        instrument="NQ",
        active_contract="NQH6",
        profile_kind="impulse_leg",
        start_ns=1,
        end_ns=1,
    )
    assert [(z.low_ticks, z.high_ticks) for z in p.lvn_zones] == [(102, 104)]
    assert p.volume_by_price == {100: 30, 101: 30, 105: 30, 106: 30}


def test_zone_tracker_counts_revisits_not_prints() -> None:
    zt = ZoneTracker()
    zt.refresh([(10, 12)], period=5)
    for px in (11, 11, 12, 15, 10, 9, 11):
        zt.observe_price(px)
    zt.current_period = 8
    assert zt.stats((10, 12)) == (1, 3, 3)


def test_arbitrary_and_composite_profiles() -> None:
    s = VolumeProfileEngine.from_trades(
        [tr(1, 100, 3), tr(2, 101, 5)], ts_event_ns=3, instrument="NQ", active_contract="NQH6", profile_kind="impulse_leg"
    )
    assert s.profile_kind == "impulse_leg" and s.total_volume == 8
    p1 = VolumeProfileEngine.from_trades([tr(1, 100, 3)], ts_event_ns=1, instrument="NQ", active_contract="NQH6", profile_kind="session")
    p2 = VolumeProfileEngine.from_trades([tr(2, 101, 5)], ts_event_ns=2, instrument="NQ", active_contract="NQH6", profile_kind="session")
    c = VolumeProfileEngine.composite_from_snapshots([p1, p2], ts_event_ns=3, instrument="NQ", active_contract="NQH6")
    assert c.profile_kind == "composite" and c.total_volume == 8 and c.poc_ticks == 101
    assert zones({1: 1}, low=True, lvn_quantile=0.25, hvn_quantile=0.75) == []


def test_auction_features_balance_then_directional() -> None:
    a = AuctionFeatureEngine(
        {"balance_efficiency_max": 0.35, "imbalance_efficiency_min": 0.6, "range_expansion_min": 1.0, "delta_persistence_min": 0.5}
    )
    for i, p in enumerate([100, 101, 100, 101, 100, 101, 100, 101], 1):
        a.update_price(i, p, 1, i)
    s = a.snapshot(9, "NQ", "NQH6")
    assert s.directional_efficiency is not None and s.state_candidate == MarketState.BALANCE
    b = AuctionFeatureEngine(
        {"balance_efficiency_max": 0.35, "imbalance_efficiency_min": 0.6, "range_expansion_min": 1.0, "delta_persistence_min": 0.5}
    )
    for i in range(1, 12):
        b.update_price(i, 100 + i * i, 5, i * 5, vwap_ticks=100.0 + i)
    s2 = b.snapshot(12, "NQ", "NQH6")
    assert (
        s2.state_candidate == MarketState.IMBALANCE_UP
        and s2.vwap_slope_ticks_per_event is not None
        and abs(s2.vwap_slope_ticks_per_event - 1.0) < 1e-9
    )


def test_incremental_window_matches_direct_computation() -> None:
    a = AuctionFeatureEngine({}, window=10)
    prices = [100, 103, 101, 104, 108, 107, 109, 105, 110, 111, 115, 112, 118, 117]
    for i, p in enumerate(prices):
        a.update_price(i, p, (-1) ** i, i * 3 - (i % 4), vwap_ticks=p + 0.5)
    window = prices[-10:]
    path = sum(abs(b - x) for x, b in zip(window, window[1:], strict=False))
    f = a.fields()
    assert abs(f["directional_efficiency"] - abs(window[-1] - window[0]) / path) < 1e-12
    n = 10
    vw = [p + 0.5 for p in window]
    xbar = (n - 1) / 2
    slope = sum((i - xbar) * (v - sum(vw) / n) for i, v in enumerate(vw)) / sum((i - xbar) ** 2 for i in range(n))
    assert abs(f["vwap_slope_ticks_per_event"] - slope) < 1e-9
