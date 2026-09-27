from __future__ import annotations
from decimal import Decimal
from contracts.models import Direction, MarketFeatureSnapshot, VolumeNode


def directional_aggression_ok(s: MarketFeatureSnapshot, direction: Direction, cfg: dict) -> bool:
    p = cfg["strategy"]
    if s.directional_aggression != direction:
        return False
    signed_delta = s.delta_zscore if direction == Direction.LONG else -s.delta_zscore
    signed_cvd = s.cvd_slope if direction == Direction.LONG else -s.cvd_slope
    return (
        signed_delta >= p["aggression_delta_zscore"]
        and signed_cvd >= p["cvd_slope_min"]
        and (s.big_print_percentile >= p["big_print_percentile"] or
             (s.stacked_imbalance_ratio >= p["stacked_imbalance_ratio"] and s.stacked_levels >= p["stacked_levels_min"]))
        and s.absorption_score < p["severe_opposing_absorption"]
    )


def is_trade_safe(s: MarketFeatureSnapshot, cfg: dict) -> bool:
    p = cfg["strategy"]
    return s.trade_safe and s.data_quality_score >= p["data_quality_min"] and s.stale_ms <= p["stale_data_ms_max"]


def choose_lvn(s: MarketFeatureSnapshot, profile_name: str, direction: Direction, cfg: dict) -> VolumeNode | None:
    p = cfg["strategy"]
    profile = getattr(s, profile_name)
    if profile is None:
        return None
    valid = []
    for n in profile.lvn_zones:
        width_ticks = float((n.high - n.low) / s.tick_size)
        if n.relative_volume <= p["lvn_relative_volume_max"] and width_ticks >= p["lvn_min_width_ticks"] and n.age_events <= p["lvn_max_age_events"] and n.retests <= p["lvn_max_retests"]:
            valid.append(n)
    if not valid:
        return None
    # deterministic: nearest to current price, then lowest midpoint
    valid.sort(key=lambda n: (abs(float(((n.low+n.high)/2) - s.last_price)), float((n.low+n.high)/2)))
    return valid[0]


def lvn_touched(s: MarketFeatureSnapshot, node: VolumeNode | None) -> bool:
    return bool(node and node.low <= s.last_price <= node.high)


def out_of_balance_direction(s: MarketFeatureSnapshot) -> Direction:
    a = s.auction
    if not a.out_of_balance:
        return Direction.FLAT
    return a.breakout_side


def session_allowed(s: MarketFeatureSnapshot, model: str, cfg: dict) -> bool:
    windows=cfg["strategy"].get("session_windows",{}).get(model,[])
    return not windows or s.session in windows
