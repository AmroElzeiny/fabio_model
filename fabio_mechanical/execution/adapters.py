"""Execution adapters (Package 2). Only simulated execution exists; live adapters are hard stubs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from fabio_mechanical.contracts import ExecutionIntent

from .simulator import ExecutionSimulator, SimOrder


class LiveTradingDisabledError(NotImplementedError):
    pass


class ExecutionAdapter(ABC):
    @abstractmethod
    def submit(self, intent: ExecutionIntent, now_ns: int, tag: str = "") -> Any: ...


class HistoricalReplayExecutionAdapter(ExecutionAdapter):
    def __init__(self, simulator: ExecutionSimulator) -> None:
        self.simulator = simulator

    def submit(self, intent: ExecutionIntent, now_ns: int, tag: str = "") -> SimOrder:
        return self.simulator.submit(intent, now_ns, tag)


class PaperExecutionAdapter(HistoricalReplayExecutionAdapter):
    """Paper trading uses the same simulator semantics."""


class MT5ExecutionAdapter(ExecutionAdapter):
    def submit(self, intent: ExecutionIntent, now_ns: int, tag: str = "") -> Any:
        raise LiveTradingDisabledError("Stub only: live MT5 ordering is intentionally disabled")


class FuturesExecutionAdapter(ExecutionAdapter):
    def submit(self, intent: ExecutionIntent, now_ns: int, tag: str = "") -> Any:
        raise LiveTradingDisabledError("Stub only: live futures ordering is intentionally disabled")
