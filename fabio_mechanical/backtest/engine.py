"""Event-driven Pure Fabio backtest.

raw MarketEvent -> ReplayEngine (book, order flow, profiles, structure) -> MarketFeatureSnapshot
-> Trend / Mean-Reversion FSM -> TradeCandidate -> DecisionProvider (mechanical) -> RiskEngine
-> ExecutionSimulator (latency, depth, slippage, fees, queue) -> bracket management -> Portfolio.

Processing order per event (no look-ahead):
1. orders whose latency has elapsed are matched against the book as of the previous packet;
2. the event is applied to the replay state (and may emit a snapshot);
3. trades update resting targets/stops; triggered stops execute at the packet boundary;
4. only then may the strategy act on the new snapshot; its orders arrive after ``latency_ms``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from fabio_mechanical.accounting import Portfolio, Position, TradeLedgerEntry
from fabio_mechanical.contracts import (
    Direction,
    EventAction,
    ExecutionIntent,
    ExitReason,
    MarketEvent,
    MarketFeatureSnapshot,
    OrderPurpose,
    OrderType,
    TradeCandidate,
)
from fabio_mechanical.data.instruments import InstrumentSpec, instrument_spec
from fabio_mechanical.decision import DecisionProvider, get_decision_provider, validate_provider_output
from fabio_mechanical.execution import ExecutionSimulator
from fabio_mechanical.journal import Journal, MemoryJournal
from fabio_mechanical.quality.engine import F_LAST
from fabio_mechanical.replay import ReplayEngine
from fabio_mechanical.research.metrics import compute_metrics
from fabio_mechanical.risk import RiskDecision, RiskEngine
from fabio_mechanical.strategy import MeanReversionMachine, TrendMachine, should_early_exit
from fabio_mechanical.strategy.fsm import MachineBase, Transition

SnapshotTransform = Callable[[MarketFeatureSnapshot], MarketFeatureSnapshot | None]


class LookaheadError(RuntimeError):
    pass


@dataclass
class PendingEntry:
    order_id: str
    candidate: TradeCandidate
    model: str
    risk: RiskDecision
    session: str
    trading_day: str
    market_regime: str = ""
    volatility_regime: str = ""
    news_day: str = ""


@dataclass
class BacktestResult:
    ledger: list[dict[str, Any]]
    fills: list[dict[str, Any]]
    state_transitions: list[dict[str, Any]]
    equity_curve: list[dict[str, Any]]
    summary: dict[str, Any]
    metrics: dict[str, Any]
    counters: dict[str, int]
    journal: list[dict[str, Any]] | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)


def _parse_hhmm(v: Any) -> time:
    if isinstance(v, time):
        return v
    hh, mm = str(v).split(":")[:2]
    return time(int(hh), int(mm))


class BacktestEngine:
    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        journal: Journal | None = None,
        snapshot_transform: SnapshotTransform | None = None,
        decision_provider: DecisionProvider | None = None,
        day_filter: Callable[[str], bool] | None = None,
    ) -> None:
        self.cfg = cfg
        self.day_filter = day_filter
        self.replay = ReplayEngine(cfg)
        self.risk = RiskEngine(cfg)
        self.portfolio = Portfolio(Decimal(str(cfg["risk"]["initial_equity"])))
        self.trend = TrendMachine(cfg)
        self.meanrev = MeanReversionMachine(cfg)
        self.machines: dict[str, MachineBase] = {"TREND": self.trend, "MEAN_REVERSION": self.meanrev}
        self.provider = decision_provider or get_decision_provider(cfg)
        self.sim = ExecutionSimulator(cfg)
        self.journal = journal if journal is not None else MemoryJournal()
        self.transform = snapshot_transform
        self.spec: InstrumentSpec | None = None
        self.position: Position | None = None
        self.pending_entry: PendingEntry | None = None
        self.stop_order_id: str | None = None
        self.target_order_id: str | None = None
        self.exit_order_id: str | None = None
        self.exit_reason: str = ""
        self.transitions: list[dict[str, Any]] = []
        self.fills: list[dict[str, Any]] = []
        self.candidates: list[dict[str, Any]] = []
        self.equity_curve: list[dict[str, Any]] = []
        self.counters: Counter[str] = Counter()
        self.last_snapshot: MarketFeatureSnapshot | None = None
        self._last_event_index = 0
        self._last_snapshot_ts: int | None = None
        self._clock = 0
        self._at_boundary = True
        self._was_trade_safe = True
        self._last_period: int | None = None
        self._position_seq = 0
        self._flatten_time = _parse_hhmm(cfg["risk"]["flatten_time"])
        self._flatten_tz = ZoneInfo(str(cfg["risk"]["flatten_timezone"]))
        self._finished = False

    # ------------------------------------------------------------------ journal helpers
    def _j(self, kind: str, **payload: Any) -> None:
        self.journal.write({"type": kind, "timestamp_ns": self._clock, "event_index": self.replay.event_index, **payload})

    def _drain_transitions(self) -> None:
        for m in self.machines.values():
            for t in m.drain_transitions():
                row = self._transition_row(t)
                self.transitions.append(row)
                self.journal.write({"type": "STATE_TRANSITION", **row})

    def _transition_row(self, t: Transition) -> dict[str, Any]:
        ts = t.timestamp_ns or self._clock
        price = None
        if t.price_ticks is not None and self.spec is not None:
            price = str(self.spec.ticks_to_price(t.price_ticks))
        return {
            "timestamp_ns": ts,
            "event_index": t.event_index or self.replay.event_index,
            "model": t.model,
            "from_state": t.from_state,
            "to_state": t.to_state,
            "reason": t.reason,
            "direction": t.direction,
            "price_ticks": t.price_ticks,
            "price": price,
        }

    # ------------------------------------------------------------------ main loop
    def run(self, events: Iterable[MarketEvent]) -> BacktestResult:
        for e in events:
            self.process_event(e)
        return self.finish()

    def process_event(self, e: MarketEvent) -> None:
        if self._finished:
            raise RuntimeError("backtest already finished")
        clock = e.clock_ns
        self._clock = max(self._clock, clock)
        self.counters["events"] += 1
        if self.spec is None:
            self.spec = instrument_spec(self.cfg, e.instrument)
        book = self.replay.book
        self.sim.on_before_event(clock, book, self.replay.quality.trade_safe, self._at_boundary)
        self._handle_executions()
        if e.action == EventAction.CONTRACT_ROLL:
            self.counters["contract_rolls"] += 1
            self._j("CONTRACT_ROLL", new_contract=e.active_contract)
            if self.position is not None or self.pending_entry is not None:
                self._exit_now(clock, ExitReason.CONTRACT_ROLL.value)
            self._hint()
            for m in self.machines.values():
                m.on_market_reset(None, "contract_roll")
            self._drain_transitions()
        snap = self.replay.process(e)
        boundary = e.provider_flags is None or bool(e.provider_flags & F_LAST)
        self._at_boundary = boundary
        self.sim.on_after_event(e, book, boundary)
        self._handle_executions()
        trade_safe = self.replay.quality.trade_safe
        if trade_safe != self._was_trade_safe:
            self._was_trade_safe = trade_safe
            self._j("DATA_QUALITY", trade_safe=trade_safe, reasons=self.replay.quality.flags.reasons)
        if self.position is not None and self.exit_order_id is None:
            self._check_flatten(clock)
        if snap is not None:
            self._on_snapshot(snap)

    # ------------------------------------------------------------------ snapshots & decisions
    def _on_snapshot(self, raw: MarketFeatureSnapshot) -> None:
        self.counters["snapshots"] += 1
        s = self.transform(raw) if self.transform is not None else raw
        if s is None:
            self.counters["snapshots_dropped_by_transform"] += 1
            return
        if s.event_index <= self._last_event_index:
            raise LookaheadError("snapshot event_index must strictly increase")
        if self._last_snapshot_ts is not None and s.timestamp_ns < self._last_snapshot_ts:
            raise LookaheadError("snapshot timestamps must be monotonic")
        self._last_event_index = s.event_index
        self._last_snapshot_ts = s.timestamp_ns
        if self.day_filter is not None and not self.day_filter(s.trading_day):
            # outside the allowed research phase: market state advances, the strategy never sees it
            self.counters["snapshots_outside_phase"] += 1
            if self.position is not None and self.exit_order_id is None:
                self._submit_exit(s.timestamp_ns, ExitReason.SESSION_END.value)
            return
        self.last_snapshot = s
        for m in self.machines.values():
            m.clock_hint = (s.timestamp_ns, s.event_index)
        if self.risk.roll_calendar(s.trading_day):
            self.counters["trading_days"] += 1
            self._j("TRADING_DAY", trading_day=s.trading_day)
        self.portfolio.mark(s.last_trade_ticks)
        if self.position is not None and s.last_trade_ticks is not None:
            self.position.mark_excursion(s.last_trade_ticks)
        if self._last_period != s.structure_period_index:
            self._last_period = s.structure_period_index
            self._record_equity(s.timestamp_ns)
        if self.position is not None and self.exit_order_id is None and should_early_exit(s, self.position.direction, self.cfg):
            self._submit_exit(s.timestamp_ns, ExitReason.EARLY_EXIT.value)
        if self.position is None and self.pending_entry is None and self.exit_order_id is None:
            for machine in (self.trend, self.meanrev):
                cand = machine.update(s)
                self._drain_transitions()
                if cand is not None:
                    self._process_candidate(s, cand, machine)
                    break

    def _process_candidate(self, s: MarketFeatureSnapshot, cand: TradeCandidate, machine: MachineBase) -> None:
        assert self.spec is not None
        self.counters["candidates"] += 1
        row = {
            "candidate_id": cand.candidate_id,
            "timestamp_ns": cand.timestamp_ns,
            "setup_type": cand.setup_type.value,
            "direction": cand.direction.value,
            "entry_reference_ticks": cand.entry_reference_ticks,
            "stop_ticks": cand.stop_ticks,
            "target_ticks": cand.target_ticks,
            "session": s.session,
            "trading_day": s.trading_day,
        }
        self._j("CANDIDATE", **row, evidence=cand.evidence, decision_snapshot=cand.decision_snapshot)
        decision = self.provider.decide(s, cand)
        proceed = validate_provider_output(cand, decision)
        self._j(
            "DECISION",
            candidate_id=cand.candidate_id,
            provider=decision.provider,
            decision=decision.decision,
            ai_enabled=decision.ai_enabled,
        )
        if not proceed:
            row["outcome"] = "provider_veto"
            self.candidates.append(row)
            machine.on_entry_rejected(s, "provider_veto")
            self._drain_transitions()
            return
        rd = self.risk.evaluate(s, cand, self.spec.tick_value)
        self._j(
            "RISK", candidate_id=cand.candidate_id, allowed=rd.allowed, reason=rd.reason, quantity=rd.quantity, risk_amount=rd.risk_amount
        )
        if not rd.allowed:
            self.counters[f"risk_reject:{rd.reason}"] += 1
            row["outcome"] = f"risk_reject:{rd.reason}"
            self.candidates.append(row)
            machine.on_entry_rejected(s, rd.reason)
            self._drain_transitions()
            return
        intent = ExecutionIntent(
            intent_id=f"{cand.candidate_id}:ENTRY",
            candidate_id=cand.candidate_id,
            timestamp_ns=s.timestamp_ns,
            instrument=s.instrument,
            active_contract=s.active_contract,
            direction=cand.direction,
            purpose=OrderPurpose.ENTRY,
            order_type=OrderType.MARKET,
            quantity=rd.quantity,
            reference_price_ticks=cand.entry_reference_ticks,
            stop_price_ticks=cand.stop_ticks,
            target_price_ticks=cand.target_ticks,
            max_slippage_ticks=self.sim.band_ticks,
        )
        order = self.sim.submit(intent, s.timestamp_ns, tag=cand.candidate_id)
        self.pending_entry = PendingEntry(
            order.order_id, cand, machine.model, rd, s.session, s.trading_day, s.market_regime, s.volatility_regime, str(s.news_day)
        )
        row["outcome"] = "order_submitted"
        row["quantity"] = rd.quantity
        self.candidates.append(row)
        self._j("ORDER_SUBMIT", intent=intent, arrival_ns=order.arrival_ns)

    # ------------------------------------------------------------------ orders & positions
    def _submit(self, purpose: OrderPurpose, order_type: OrderType, qty: int, now: int, **prices: Any) -> str:
        assert self.position is not None
        pos = self.position
        exit_dir = Direction.SHORT if pos.direction == Direction.LONG else Direction.LONG
        intent = ExecutionIntent(
            intent_id=f"{pos.position_id}:{purpose.value}:{now}",
            candidate_id=pos.candidate_id,
            timestamp_ns=now,
            instrument=pos.instrument,
            active_contract=pos.active_contract,
            direction=exit_dir,
            purpose=purpose,
            order_type=order_type,
            quantity=qty,
            max_slippage_ticks=self.sim.band_ticks,
            **prices,
        )
        order = self.sim.submit(intent, now, tag=pos.position_id)
        self._j("ORDER_SUBMIT", intent=intent, arrival_ns=order.arrival_ns)
        return order.order_id

    def _submit_exit(self, now: int, reason: str) -> None:
        if self.position is None or self.exit_order_id is not None:
            return
        for oid in (self.stop_order_id, self.target_order_id):
            if oid:
                self.sim.cancel(oid, f"superseded_by_{reason}")
        self.exit_reason = reason
        self.exit_order_id = self._submit(OrderPurpose.EXIT, OrderType.MARKET, self.position.quantity, now)

    def _exit_now(self, now: int, reason: str) -> None:
        """Immediate flatten against the current book (contract roll / kill switch)."""
        if self.pending_entry is not None:
            self.sim.cancel(self.pending_entry.order_id, reason)
            self._handle_executions()
        if self.position is None:
            return
        self._submit_exit(now, reason)
        self.sim.flush(now, self.replay.book, True)
        self._handle_executions()

    def kill(self, reason: str = ExitReason.KILL_SWITCH.value) -> None:
        self.risk.set_kill_switch(True)
        self._j("KILL_SWITCH", reason=reason)
        self._exit_now(self._clock, ExitReason.KILL_SWITCH.value)

    def _check_flatten(self, clock: int) -> None:
        assert self.position is not None
        local = self.replay.calendar.local_time(clock, self._flatten_tz)
        new_day = self.replay.current_trading_day is not None and self.position.trading_day != self.replay.current_trading_day.isoformat()
        start = self.replay.calendar.trading_day_start
        if new_day or (self._flatten_time <= local < start):
            self._submit_exit(clock, ExitReason.SESSION_END.value)

    def _handle_executions(self) -> None:
        fills, reports = self.sim.drain()
        for fe in fills:
            self._on_fill(fe.purpose, fe.order_id, fe.fill)
        for rep in reports:
            self._j("ORDER_REPORT", report=rep)
            pe = self.pending_entry
            if pe is not None and rep.intent_id == pe.order_id:
                self._on_entry_report(pe, rep.filled_quantity, rep.rejected_reason)

    def _on_fill(self, purpose: OrderPurpose, order_id: str, fill: Any) -> None:
        assert self.spec is not None
        self.counters["fills"] += 1
        row = {
            "timestamp_ns": fill.timestamp_ns,
            "order_id": order_id,
            "purpose": purpose.value,
            "price_ticks": fill.price_ticks,
            "price": str(self.spec.ticks_to_price(fill.price_ticks)),
            "quantity": fill.quantity,
            "fee": str(fill.fee),
            "slippage_ticks": fill.slippage_ticks,
            "liquidity": fill.liquidity,
        }
        self.fills.append(row)
        self._j("FILL", **row)
        if purpose == OrderPurpose.ENTRY:
            pe = self.pending_entry
            assert pe is not None
            if self.position is None:
                cand = pe.candidate
                self._position_seq += 1
                self.position = Position(
                    position_id=f"P{self._position_seq:05d}:{cand.candidate_id[:12]}",
                    instrument=cand.instrument,
                    active_contract=cand.active_contract,
                    direction=cand.direction,
                    spec=self.spec,
                    setup_type=cand.setup_type.value,
                    candidate_id=cand.candidate_id,
                    config_hash=cand.decision_snapshot.config_hash,
                    stop_ticks=cand.stop_ticks,
                    target_ticks=cand.target_ticks,
                    entry_reference_ticks=cand.entry_reference_ticks,
                    opened_ns=fill.timestamp_ns,
                    trading_day=pe.trading_day,
                    session=pe.session,
                    evidence=cand.evidence,
                    market_regime=pe.market_regime,
                    volatility_regime=pe.volatility_regime,
                    news_day=pe.news_day,
                )
                self.portfolio.open(self.position)
                self._j("POSITION_OPEN", position_id=self.position.position_id, candidate_id=cand.candidate_id)
            self.portfolio.add_entry_fill(self.position, fill, fill.slippage_ticks)
            return
        pos = self.position
        if pos is None:
            self.counters["orphan_exit_fills"] += 1
            self.risk.set_execution_error(True)
            self._j("EXECUTION_ERROR", detail="exit fill without position", order_id=order_id)
            return
        reason = {OrderPurpose.STOP: ExitReason.STOP.value, OrderPurpose.TARGET: ExitReason.TARGET.value}.get(purpose, self.exit_reason)
        entry = self.portfolio.add_exit_fill(pos, fill, reason, fill.slippage_ticks)
        if entry is None:
            # server-side OCO: the sibling order tracks the remaining position size atomically
            if purpose == OrderPurpose.TARGET and self.stop_order_id:
                self.sim.set_remaining(self.stop_order_id, pos.quantity)
            elif purpose == OrderPurpose.STOP and self.target_order_id:
                self.sim.set_remaining(self.target_order_id, pos.quantity)
            return
        self._on_position_closed(pos, entry)

    def _hint(self) -> None:
        for m in self.machines.values():
            m.clock_hint = (self._clock, self.replay.event_index)

    def _on_entry_report(self, pe: PendingEntry, filled: int, reason: str | None) -> None:
        machine = self.machines[pe.model]
        self._hint()
        kept = self.risk.on_entry_filled(pe.candidate.candidate_id, pe.risk.quantity, filled)
        self.pending_entry = None
        if filled == 0 or self.position is None:
            self.counters[f"entry_not_filled:{reason}"] += 1
            machine.on_entry_rejected(None, f"execution:{reason}")
            self._drain_transitions()
            return
        pos = self.position
        pos.risk_exposure = kept
        machine.on_trade_opened(None)
        self._drain_transitions()
        now = self._clock
        self.stop_order_id = self._submit(OrderPurpose.STOP, OrderType.STOP, pos.quantity, now, trigger_price_ticks=pos.stop_ticks)
        self.target_order_id = self._submit(OrderPurpose.TARGET, OrderType.LIMIT, pos.quantity, now, limit_price_ticks=pos.target_ticks)

    def _on_position_closed(self, pos: Position, entry: TradeLedgerEntry) -> None:
        for oid in (self.stop_order_id, self.target_order_id, self.exit_order_id):
            if oid:
                self.sim.cancel(oid, "position_closed")
        self.stop_order_id = self.target_order_id = self.exit_order_id = None
        self.risk.on_trade_closed(entry.net_pnl, pos.risk_exposure)
        machine = self.machines["TREND" if pos.setup_type == "TREND" else "MEAN_REVERSION"]
        self._hint()
        machine.on_trade_closed(None, entry.exit_reason)
        self._drain_transitions()
        self.position = None
        self.counters["trades"] += 1
        self.portfolio.mark(None)
        self._record_equity(self._clock)
        self._j("POSITION_CLOSE", trade=asdict(entry))
        # drop the reports produced by the cancellations above
        _, reports = self.sim.drain()
        for rep in reports:
            self._j("ORDER_REPORT", report=rep)

    def _record_equity(self, ts: int) -> None:
        p = self.portfolio
        dd = (p.peak - p.equity) / p.peak if p.peak else Decimal("0")
        self.equity_curve.append(
            {"timestamp_ns": ts, "equity": str(p.equity), "realized": str(p.realized), "unrealized": str(p.unrealized), "drawdown": str(dd)}
        )

    # ------------------------------------------------------------------ finish
    def finish(self) -> BacktestResult:
        if self._finished:
            raise RuntimeError("finish() called twice")
        if self.position is not None and self.exit_order_id is None:
            self._submit_exit(self._clock, ExitReason.END_OF_DATA.value)
        self.sim.flush(self._clock, self.replay.book, self.replay.quality.trade_safe)
        self._handle_executions()
        for o in self.sim.open_orders():
            self.sim.cancel(o.order_id, "end_of_data")
        self._handle_executions()
        self._drain_transitions()
        self._finished = True
        self.journal.close()
        return self.result()

    def result(self) -> BacktestResult:
        ledger = [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(x).items()} for x in self.portfolio.ledger]
        metrics = compute_metrics(self.portfolio.ledger, float(self.portfolio.initial_equity))
        fees = sum((x.fees for x in self.portfolio.ledger), Decimal("0"))
        gross = sum((x.gross_pnl for x in self.portfolio.ledger), Decimal("0"))
        q = self.replay.quality.flags
        summary = {
            "trades": len(ledger),
            "gross_pnl": str(gross),
            "fees": str(fees),
            "net_pnl": str(gross - fees),
            "ending_equity": str(self.portfolio.equity),
            "max_drawdown_mark_to_market": str(self.portfolio.max_drawdown.quantize(Decimal("0.000001"))),
            "entry_slippage_ticks_total": sum(x.entry_slippage_ticks for x in self.portfolio.ledger),
            "exit_slippage_ticks_total": sum(x.exit_slippage_ticks for x in self.portfolio.ledger),
            "fills": len(self.fills),
            "maker_fills": sum(1 for f in self.fills if f["liquidity"] == "MAKER"),
            "taker_fills": sum(1 for f in self.fills if f["liquidity"] == "TAKER"),
            "candidates": self.counters["candidates"],
            "state_transitions": len(self.transitions),
            "events": self.counters["events"],
            "snapshots": self.counters["snapshots"],
            "trade_safe_at_end": q.trade_safe,
            "data_quality_reasons": q.reasons,
            "ts_event_regressions": self.replay.quality.ts_event_regressions,
            "ai_enabled": False,
            "decision_provider": self.provider.name,
        }
        journal = self.journal.records if isinstance(self.journal, MemoryJournal) else None
        return BacktestResult(
            ledger=ledger,
            fills=list(self.fills),
            state_transitions=list(self.transitions),
            equity_curve=list(self.equity_curve),
            summary=summary,
            metrics=metrics,
            counters=dict(self.counters),
            journal=journal,
            candidates=list(self.candidates),
        )


def run_backtest(cfg: dict[str, Any], events: Iterable[MarketEvent], **kwargs: Any) -> BacktestResult:
    return BacktestEngine(cfg, **kwargs).run(events)
