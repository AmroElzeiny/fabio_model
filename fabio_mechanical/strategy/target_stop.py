"""Targets (balance POC) and structural stops (Package 2, tick units)."""

from __future__ import annotations

from typing import Any

from fabio_mechanical.contracts import Direction, MarketFeatureSnapshot, ProfileZone, SetupType


class StopWideningError(RuntimeError):
    pass


def target_for(
    s: MarketFeatureSnapshot, setup: SetupType, direction: Direction, entry_ticks: int, setup_balance_poc: int | None = None
) -> int | None:
    """Balance-POC target.

    * Mean reversion: the POC of the balance whose breakout attempt failed, captured when the
      setup started (``setup_balance_poc``); a developing reference would otherwise absorb the
      failed auction's own volume and drift toward the entry.
    * Trend: the nearest known balance POC strictly beyond the entry in the trade direction
      (prior-session and completed intraday balances). No POC => no target => no trade
      (a fixed-RR target is explicitly excluded by the invariants).
    """
    if setup == SetupType.MEAN_REVERSION:
        return setup_balance_poc if setup_balance_poc is not None else s.auction.balance_poc_ticks
    if direction == Direction.LONG:
        above = [p for p in s.relevant_poc_candidates if p > entry_ticks]
        return min(above) if above else None
    below = [p for p in s.relevant_poc_candidates if p < entry_ticks]
    return max(below) if below else None


def stop_for(s: MarketFeatureSnapshot, setup: SetupType, direction: Direction, node: ProfileZone, cfg: dict[str, Any]) -> int:
    b = int(cfg["strategy"]["stop_buffer_ticks"])
    if setup == SetupType.MEAN_REVERSION:
        extreme = s.auction.failed_auction_extreme_ticks
        if direction == Direction.LONG:
            return (extreme if extreme is not None else node.low_ticks) - b
        return (extreme if extreme is not None else node.high_ticks) + b
    if direction == Direction.LONG:
        return node.low_ticks - b
    return node.high_ticks + b


def validate_stop_update(direction: Direction, current: int, proposed: int) -> int:
    """The only stop-modification path. Widening is forbidden (invariant ``never_widen_stop``)."""
    if direction == Direction.LONG and proposed < current:
        raise StopWideningError("Long stop widening is forbidden")
    if direction == Direction.SHORT and proposed > current:
        raise StopWideningError("Short stop widening is forbidden")
    return proposed


def checkpoints(s: MarketFeatureSnapshot) -> list[tuple[str, int, int]]:
    """Informational LVN/HVN checkpoints of the developing session profile; never reversal targets."""
    out = [("LVN", z.low_ticks, z.high_ticks) for z in s.relevant_lvn_zones]
    out += [("HVN", z.low_ticks, z.high_ticks) for z in s.relevant_hvn_zones]
    return out


def should_early_exit(s: MarketFeatureSnapshot, direction: Direction, cfg: dict[str, Any]) -> bool:
    """EXPERIMENTAL participation-based exit (``experimental_early_exit_enabled``), OFF in the baseline."""
    if not cfg.get("experimental", {}).get("experimental_early_exit_enabled", False):
        return False
    opposing = s.directional_aggression not in {direction, Direction.FLAT}
    severe_absorption = s.absorption_score >= cfg["strategy"]["severe_opposing_absorption"]
    lost_auction = (not s.auction.out_of_balance) if s.market_regime == "TREND" else False
    return opposing or severe_absorption or lost_auction
