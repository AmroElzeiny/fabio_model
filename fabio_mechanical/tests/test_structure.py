from __future__ import annotations

from typing import Any

from fabio_mechanical.contracts import AuctionFeatureSnapshot, Direction
from fabio_mechanical.market.structure import AuctionStructureEngine, StructureSettings

NS = 1_000_000_000
T0 = 1_768_300_000 * NS  # arbitrary, period aligned below
T0 -= T0 % (60 * NS)
PATTERN = [0, 1, -1, 2, -2, 0, 3, -3, 1]  # 9s cycle divides each 15-minute half; minute closes vary


def engine(cfg: dict[str, Any]) -> AuctionStructureEngine:
    return AuctionStructureEngine(StructureSettings.from_config(cfg), "NQ", "NQH6")


def balance(eng: AuctionStructureEngine, start_s: int, seconds: int, center: int = 1000) -> int:
    for s in range(start_s, start_s + seconds):
        off = PATTERN[s % len(PATTERN)]
        eng.on_trade(center + off, 3 if off == 0 else 2, T0 + s * NS)
    return start_s + seconds


def walk(eng: AuctionStructureEngine, start_s: int, prices: list[int], step_s: float = 1.0, size: int = 2) -> int:
    t = float(start_s)
    for p in prices:
        eng.on_trade(p, size, T0 + int(t * NS))
        t += step_s
    return int(t) + 1


def f(eng: AuctionStructureEngine, t_s: int) -> dict[str, Any]:
    defaults = {k: v.default for k, v in AuctionFeatureSnapshot.model_fields.items()}
    return {**defaults, **eng.fields(eng._last_price, T0 + t_s * NS)}


def test_balance_detected_after_window(cfg: dict[str, Any]) -> None:
    eng = engine(cfg)
    t = balance(eng, 0, 20 * 60)
    assert eng.ref is None  # window not yet full
    t = balance(eng, t, 15 * 60)
    out = f(eng, t)
    assert eng.ref is not None and out["balanced"] is True
    assert out["balance_low_ticks"] <= 1000 <= out["balance_high_ticks"] and out["balance_poc_ticks"] == 1000
    assert out["balance_value_overlap"] >= 0.55 and abs(out["balance_poc_migration_ticks"]) <= 4
    assert eng.poc_candidates() == [1000]


def test_failed_auction_and_reclaim(cfg: dict[str, Any]) -> None:
    eng = engine(cfg)
    t = balance(eng, 0, 35 * 60)
    vah = eng.ref.area.high_ticks  # type: ignore[union-attr]
    t = walk(eng, t, list(range(vah + 1, vah + 7)), step_s=2)  # quick probe 6 ticks beyond value
    out = f(eng, t)
    assert out["excursion_active"] and out["breakout_side"] == Direction.LONG and out["breakout_distance_ticks"] == 6
    val = eng.ref.area.low_ticks  # type: ignore[union-attr]
    t = walk(eng, t, list(range(vah + 5, val - 1, -1)), step_s=0.5)  # fast rejection back to the value low
    out = f(eng, t)
    assert out["failed_auction"] and out["reclaim_inside_balance"] and not out["excursion_active"]
    assert out["acceptance_events"] < cfg["strategy"]["acceptance_event_count"]
    assert out["failed_auction_extreme_ticks"] == vah + 6
    assert out["rejection_speed"] > 0.3
    t = walk(eng, t, [val + 1, val + 2], step_s=1)  # pullback (inside value) from the reclaim low locks the leg
    out = f(eng, t)
    assert out["reclaim_leg_id"] is not None and out["reclaim_profile"] is not None
    assert out["reclaim_leg_high_ticks"] == vah + 6


def test_accepted_excursion_is_not_a_failed_auction(cfg: dict[str, Any]) -> None:
    eng = engine(cfg)
    t = balance(eng, 0, 35 * 60)
    vah = eng.ref.area.high_ticks  # type: ignore[union-attr]
    t = walk(eng, t, [vah + 5] * 300, step_s=1)  # five minutes outside value
    out = f(eng, t)
    assert out["acceptance_events"] >= 3
    t = walk(eng, t, list(range(vah + 4, 999, -1)), step_s=0.5)
    out = f(eng, t)
    assert out["failed_auction"] is False and out["reclaim_inside_balance"] is False


def test_impulse_leg_locks_with_profile_and_out_of_balance(cfg: dict[str, Any]) -> None:
    eng = engine(cfg)
    t = balance(eng, 0, 35 * 60)
    ref = eng.ref.area  # type: ignore[union-attr]
    up = list(range(1000, 1031))  # efficient impulse through VAH and the balance range high
    t = walk(eng, t, up, step_s=1.5, size=3)
    out = f(eng, t)
    assert out["excursion_active"] and out["out_of_balance"] and out["structure_break"]
    assert out["impulse_id"] is None  # not locked until the pullback starts
    assert out["displacement_zscore"] > 1.0
    t = walk(eng, t, [1028, 1026, 1025], step_s=1)  # >= 15% retracement locks the leg
    out = f(eng, t)
    assert out["impulse_id"] is not None and out["impulse_profile"] is not None
    assert out["impulse_low_ticks"] <= ref.poc_ticks and out["impulse_high_ticks"] == 1030
    assert out["impulse_ticks"] >= 30 and out["impulse_efficiency"] >= cfg["strategy"]["directional_efficiency_min"]


def test_new_value_archives_old_balance_and_roll_resets(cfg: dict[str, Any]) -> None:
    eng = engine(cfg)
    t = balance(eng, 0, 35 * 60)
    t = walk(eng, t, list(range(1000, 1061)), step_s=1)
    t = balance(eng, t, 40 * 60, center=1060)
    assert eng.ref is not None and eng.ref.area.poc_ticks == 1060
    assert [a.poc_ticks for a in eng.archived] == [1000]
    assert eng.poc_candidates() == [1000, 1060]
    eng.on_contract_roll("NQM6")
    assert eng.ref is None and eng.poc_candidates() == [] and eng.contract == "NQM6"
