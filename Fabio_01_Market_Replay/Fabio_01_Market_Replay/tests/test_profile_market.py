from fabio_market_replay.contracts import Side, TradePrint, MarketState
from fabio_market_replay.profile.volume_profile import VolumeProfileEngine
from fabio_market_replay.market.features import AuctionFeatureEngine


def tr(i,p,s=1,side=Side.BID):
    return TradePrint(ts_event_ns=i, sequence=i, instrument="NQ", active_contract="NQH6", price_ticks=p, size=s, aggressor_side=side)


def test_poc_vah_val_known_distribution():
    vp=VolumeProfileEngine(value_area_fraction=.70)
    for x in [tr(1,100,10),tr(2,101,50),tr(3,102,30),tr(4,103,10)]: vp.consume(x)
    s=vp.snapshot(4,"NQ","NQH6")
    assert s.poc_ticks == 101
    assert s.val_ticks <= s.poc_ticks <= s.vah_ticks
    assert sum(s.volume_by_price.values()) == 100


def test_lvn_hvn_are_zones_not_single_contract_claims():
    vp=VolumeProfileEngine(hvn_quantile=.75, lvn_quantile=.25)
    for i,(p,v) in enumerate([(100,40),(101,5),(102,4),(103,45),(104,6)],1): vp.consume(tr(i,p,v))
    s=vp.snapshot(9,"NQ","NQH6")
    assert s.hvn_zones and s.lvn_zones
    assert all(z.low_ticks <= z.high_ticks and z.width_ticks >= 1 for z in s.lvn_zones)
    assert all(hasattr(z,"retest_count") for z in s.lvn_zones)


def test_arbitrary_profile_builder():
    trades=[tr(1,100,3),tr(2,101,5)]
    s=VolumeProfileEngine.from_trades(trades, ts_event_ns=3, instrument="NQ", active_contract="NQH6", profile_kind="impulse_leg")
    assert s.profile_kind == "impulse_leg" and s.total_volume == 8


def test_auction_features_balance_then_directional():
    a=AuctionFeatureEngine({"balance_efficiency_max":.35,"imbalance_efficiency_min":.6,"range_expansion_min":1.0,"delta_persistence_min":.5})
    for i,p in enumerate([100,101,100,101,100,101,100,101],1): a.update_price(i,p,1, i)
    s=a.snapshot(9,"NQ","NQH6")
    assert s.directional_efficiency is not None
    assert s.state_candidate in set(MarketState)


def test_composite_profile_combines_volume():
    p1=VolumeProfileEngine.from_trades([tr(1,100,3)],ts_event_ns=1,instrument="NQ",active_contract="NQH6",profile_kind="session")
    p2=VolumeProfileEngine.from_trades([tr(2,101,5)],ts_event_ns=2,instrument="NQ",active_contract="NQH6",profile_kind="session")
    c=VolumeProfileEngine.composite_from_snapshots([p1,p2],ts_event_ns=3,instrument="NQ",active_contract="NQH6")
    assert c.profile_kind == "composite" and c.total_volume == 8 and c.poc_ticks == 101
