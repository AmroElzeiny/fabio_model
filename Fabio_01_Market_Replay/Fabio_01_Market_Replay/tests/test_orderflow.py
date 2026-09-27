from fabio_market_replay.contracts import Side, TradePrint
from fabio_market_replay.orderflow.engine import OrderFlowEngine
from fabio_market_replay.orderflow.footprint import FootprintEngine


def t(i, p, size, side):
    return TradePrint(ts_event_ns=i*1_000_000_000, sequence=i, instrument="NQ", active_contract="NQH6",
                      price_ticks=p, size=size, aggressor_side=side)


def test_delta_cvd_and_rolling_delta():
    f=OrderFlowEngine(rolling_window=3)
    for x in [t(1,100,10,Side.BID), t(2,101,4,Side.ASK), t(3,101,5,Side.BID)]: f.consume(x)
    assert f.state.cvd == 11
    assert f.rolling_delta() == 11
    assert f.state.buy_volume == 15 and f.state.sell_volume == 4


def test_big_print_percentile_and_aggression():
    f=OrderFlowEngine(rolling_window=10, big_print_percentile=.8)
    for i,s in enumerate([1,2,3,4,20],1): f.consume(t(i,100+i,s,Side.BID if i%2 else Side.ASK))
    m=f.big_print_metrics()
    assert m["threshold_contracts"] >= 4
    assert m["big_print_count"] >= 1
    assert 0 <= f.aggression_metrics()["persistence"] <= 1


def test_absorption_is_metric_not_boolean():
    f=OrderFlowEngine(rolling_window=10)
    for i in range(1,6): f.consume(t(i,100,5,Side.BID))
    m=f.absorption_metrics()
    assert m["aggressive_volume_per_tick_progress"] == 25
    assert m["adverse_aggression_no_continuation"] == 1.0
    assert m["repeated_aggression_same_level"] == 1.0


def test_footprint_delta_and_diagonal_imbalance():
    fp=FootprintEngine(imbalance_ratio=2.0)
    trades=[t(1,100,2,Side.ASK), t(2,101,10,Side.BID), t(3,102,12,Side.BID)]
    for x in trades: fp.consume(x)
    s=fp.snapshot(trades[-1].ts_event_ns,"NQ","NQH6")
    assert s.total_volume == 24
    assert s.delta == 20
    assert any(x.buy_imbalance for x in s.levels)


def test_footprint_event_window_and_time_bars():
    fp=FootprintEngine()
    for x in [t(1,100,1,Side.BID), t(2,101,2,Side.ASK), t(3,102,3,Side.BID)]: fp.consume(x)
    last=fp.snapshot_last_events(2, 3_000_000_000, "NQ", "NQH6")
    bars=fp.time_bar_snapshots(1_000_000_000, "NQ", "NQH6")
    assert last.total_volume == 5
    assert len(bars) >= 3
