from __future__ import annotations
from abc import ABC, abstractmethod
from contracts.models import DecisionSnapshot, TradeCandidate, MarketFeatureSnapshot

class DecisionProvider(ABC):
    @abstractmethod
    def decide(self, snapshot: MarketFeatureSnapshot, candidate: TradeCandidate) -> DecisionSnapshot: ...

class MechanicalDecisionProvider(DecisionProvider):
    def decide(self, snapshot: MarketFeatureSnapshot, candidate: TradeCandidate) -> DecisionSnapshot:
        return candidate.decision_snapshot

class AIProviderStub(DecisionProvider):
    def decide(self, snapshot: MarketFeatureSnapshot, candidate: TradeCandidate) -> DecisionSnapshot:
        raise RuntimeError("AI is disabled in FABIO_MECHANICAL_V1; no AI implementation is present")
