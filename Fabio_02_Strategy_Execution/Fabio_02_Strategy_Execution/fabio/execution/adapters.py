from __future__ import annotations
from abc import ABC, abstractmethod
from contracts.models import ExecutionIntent, ExecutionReport, MarketFeatureSnapshot
from .simulator import ExecutionSimulator

class ExecutionAdapter(ABC):
    @abstractmethod
    def submit(self,intent:ExecutionIntent,snapshot:MarketFeatureSnapshot)->ExecutionReport: ...

class HistoricalReplayExecutionAdapter(ExecutionAdapter):
    def __init__(self,simulator:ExecutionSimulator):self.simulator=simulator
    def submit(self,intent,snapshot):return self.simulator.execute(intent,snapshot)

class PaperExecutionAdapter(HistoricalReplayExecutionAdapter): pass
class MT5ExecutionAdapter(ExecutionAdapter):
    def submit(self,intent,snapshot):raise NotImplementedError('Stub only: live MT5 ordering intentionally disabled')
class FuturesExecutionAdapter(ExecutionAdapter):
    def submit(self,intent,snapshot):raise NotImplementedError('Stub only: live futures ordering intentionally disabled')
