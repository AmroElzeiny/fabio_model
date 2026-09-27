"""Location and aggression checks shared by both Fabio models (Package 2, tick units).

Integration notes (see INTEGRATION_REPORT.md):
* ``lvn_neighbor_ratio`` was declared in Package 2's interpretation/search space but never read;
  it is now part of LVN qualification (zone volume / mean neighbouring volume).
* big-print evidence uses the percentile of the largest print on the SAME side as the trade.
* stacked imbalances are computed from the aggression-window footprint at the configured ratio.
"""

from __future__ import annotations

from typing import Any

from fabio_mechanical.contracts import Direction, MarketFeatureSnapshot, ProfileZone
from fabio_mechanical.orderflow.footprint import stacked_imbalance_levels


def aggression_evidence(s: MarketFeatureSnapshot, direction: Direction, cfg: dict[str, Any]) -> dict[str, Any]:
    p = cfg["strategy"]
    long = direction == Direction.LONG
    signed_delta = s.delta_zscore if long else -s.delta_zscore
    signed_cvd = s.cvd_slope_normalized if long else -s.cvd_slope_normalized
    big_print = s.big_buy_print_percentile if long else s.big_sell_print_percentile
    stacked = stacked_imbalance_levels(s.footprint, buy=long, ratio=float(p["stacked_imbalance_ratio"]))
    checks = {
        "directional_aggression": s.directional_aggression == direction,
        "delta_zscore": signed_delta >= p["aggression_delta_zscore"],
        "cvd_slope": signed_cvd >= p["cvd_slope_min"],
        "big_print_or_stacked": big_print >= p["big_print_percentile"] or stacked >= p["stacked_levels_min"],
        "no_severe_absorption": s.absorption_score < p["severe_opposing_absorption"],
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "signed_delta_zscore": signed_delta,
        "signed_cvd_slope": signed_cvd,
        "big_print_percentile": big_print,
        "stacked_levels": stacked,
        "absorption_score": s.absorption_score,
    }


def directional_aggression_ok(s: MarketFeatureSnapshot, direction: Direction, cfg: dict[str, Any]) -> bool:
    return bool(aggression_evidence(s, direction, cfg)["ok"])


def is_trade_safe(s: MarketFeatureSnapshot, cfg: dict[str, Any]) -> bool:
    p = cfg["strategy"]
    return s.trade_safe and s.data_quality_score >= p["data_quality_min"] and s.stale_ms <= p["stale_data_ms_max"]


def lvn_qualifies(zone: ProfileZone, cfg: dict[str, Any]) -> bool:
    p = cfg["strategy"]
    zone_to_neighbors = 1.0 / zone.neighboring_volume_ratio if zone.neighboring_volume_ratio > 0 else float("inf")
    return (
        zone.volume_to_median_ratio <= p["lvn_relative_volume_max"]
        and zone_to_neighbors <= p["lvn_neighbor_ratio"]
        and zone.width_ticks >= p["lvn_min_width_ticks"]
        and zone.age_events <= p["lvn_max_age_events"]
        and zone.retest_count <= p["lvn_max_retests"]
    )


def choose_lvn(s: MarketFeatureSnapshot, profile_name: str, direction: Direction, cfg: dict[str, Any]) -> ProfileZone | None:
    """Qualifying LVN of the leg profile nearest to the current price (deterministic tie-break).

    Only LVNs on the retracement side are eligible (long: zone at/below price, short: at/above):
    a zone the pullback has already traded through cannot be retested from the right side.
    Package 2 accepted ``direction`` but ignored it.
    """
    profile = getattr(s, profile_name)
    if profile is None or s.last_trade_ticks is None:
        return None
    px = s.last_trade_ticks
    valid = [
        z for z in profile.lvn_zones if lvn_qualifies(z, cfg) and (z.low_ticks <= px if direction == Direction.LONG else z.high_ticks >= px)
    ]
    if not valid:
        return None
    valid.sort(key=lambda z: (abs((z.low_ticks + z.high_ticks) / 2 - px), (z.low_ticks + z.high_ticks) / 2))
    return valid[0]


def lvn_touched(s: MarketFeatureSnapshot, node: ProfileZone | None) -> bool:
    return bool(node is not None and s.last_trade_ticks is not None and node.low_ticks <= s.last_trade_ticks <= node.high_ticks)


def at_location(s: MarketFeatureSnapshot, node: ProfileZone | None) -> bool:
    """Aggression is 'at location' when the aggression window itself traded inside the LVN zone."""
    if node is None or s.aggression_window_low_ticks is None or s.aggression_window_high_ticks is None:
        return False
    return s.aggression_window_low_ticks <= node.high_ticks and s.aggression_window_high_ticks >= node.low_ticks


def out_of_balance_direction(s: MarketFeatureSnapshot) -> Direction:
    a = s.auction
    if not a.out_of_balance:
        return Direction.FLAT
    return a.breakout_side


def session_allowed(s: MarketFeatureSnapshot, model: str, cfg: dict[str, Any]) -> bool:
    windows = cfg["strategy"].get("session_windows", {}).get(model, [])
    return not windows or s.session in windows
