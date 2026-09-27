from __future__ import annotations
from decimal import Decimal
from contracts.models import Direction, MarketFeatureSnapshot, SetupType, VolumeNode

class StopWideningError(RuntimeError): pass


def target_for(s: MarketFeatureSnapshot, setup: SetupType, direction: Direction) -> Decimal:
    if setup == SetupType.MEAN_REVERSION:
        return s.auction.balance_poc
    # Trend baseline target: relevant balance POC supplied by the snapshot context.
    # For synthetic fixtures this represents the downstream/upstream balance POC.
    return s.volume_profile.poc


def stop_for(s: MarketFeatureSnapshot, setup: SetupType, direction: Direction, node: VolumeNode, cfg: dict) -> Decimal:
    b = Decimal(str(cfg["strategy"]["stop_buffer_ticks"])) * s.tick_size
    if setup == SetupType.MEAN_REVERSION:
        if direction == Direction.LONG:
            extreme = s.auction.impulse_low if s.auction.impulse_low is not None else node.low
            return extreme - b
        extreme = s.auction.impulse_high if s.auction.impulse_high is not None else node.high
        return extreme + b
    if direction == Direction.LONG:
        return node.low - b
    return node.high + b


def validate_stop_update(direction: Direction, current: Decimal, proposed: Decimal) -> Decimal:
    if direction == Direction.LONG and proposed < current:
        raise StopWideningError("Long stop widening is forbidden")
    if direction == Direction.SHORT and proposed > current:
        raise StopWideningError("Short stop widening is forbidden")
    return proposed


def checkpoints(s: MarketFeatureSnapshot) -> list[tuple[str, Decimal, Decimal]]:
    """Informational LVN/HVN checkpoints only; never automatic reversal targets."""
    out=[]
    for n in s.volume_profile.lvn_zones: out.append(("LVN", n.low, n.high))
    for n in s.volume_profile.hvn_zones: out.append(("HVN", n.low, n.high))
    return out

def should_early_exit(s: MarketFeatureSnapshot, direction: Direction, cfg: dict) -> bool:
    """Optional participation-based exit. Baseline is OFF by configuration."""
    if not cfg.get("research",{}).get("early_exit_enabled",False): return False
    opposing = s.directional_aggression not in {direction, Direction.FLAT}
    severe_absorption = s.absorption_score >= cfg["strategy"]["severe_opposing_absorption"]
    lost_auction = not s.auction.out_of_balance if s.market_regime=="TREND" else False
    return opposing or severe_absorption or lost_auction
