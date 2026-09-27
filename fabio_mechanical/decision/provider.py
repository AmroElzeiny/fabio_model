"""Decision-provider seam (Package 2). V1 runtime is purely mechanical.

``AIProviderStub`` exists only to pin the future interface: it raises on use. A future provider
may only VETO a mechanical candidate (return ``decision="NO_TRADE"``); it can never change
direction, size, stop, target, risk locks or submit orders. ``validate_provider_output``
enforces that contract on every decision.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from fabio_mechanical.contracts import DecisionSnapshot, MarketFeatureSnapshot, TradeCandidate


class AIDisabledError(RuntimeError):
    pass


class DecisionContractViolation(RuntimeError):
    pass


class DecisionProvider(ABC):
    name = "abstract"

    @abstractmethod
    def decide(self, snapshot: MarketFeatureSnapshot, candidate: TradeCandidate) -> DecisionSnapshot: ...


class MechanicalDecisionProvider(DecisionProvider):
    name = "MECHANICAL"

    def decide(self, snapshot: MarketFeatureSnapshot, candidate: TradeCandidate) -> DecisionSnapshot:
        return candidate.decision_snapshot


class AIProviderStub(DecisionProvider):
    name = "AI_STUB_DISABLED"

    def decide(self, snapshot: MarketFeatureSnapshot, candidate: TradeCandidate) -> DecisionSnapshot:
        raise AIDisabledError("AI is disabled in FABIO_MECHANICAL_V1; no AI implementation is present")


def validate_provider_output(candidate: TradeCandidate, decision: DecisionSnapshot) -> bool:
    """Return True to proceed, False for a veto. Anything else is a contract violation."""
    base = candidate.decision_snapshot
    if decision.ai_enabled:
        raise DecisionContractViolation("ai_enabled decisions are not allowed in V1")
    if decision.setup_type != base.setup_type or decision.direction != base.direction:
        raise DecisionContractViolation("a decision provider may not change setup or direction")
    if decision.decision == "NO_TRADE":
        return False
    if decision.decision != "ENTER":
        raise DecisionContractViolation(f"unexpected decision {decision.decision}")
    return True


def get_decision_provider(cfg: dict[str, Any]) -> DecisionProvider:
    if cfg.get("AI_ENABLED") is not False:
        raise AIDisabledError("AI_ENABLED must be false; the runtime only supports the mechanical provider")
    return MechanicalDecisionProvider()
