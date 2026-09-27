"""Hard risk engine (Package 2). Sits below the decision layer and cannot be overridden.

Integration fixes (INTEGRATION_REPORT.md): daily/weekly counters are actually reset on CME
trading-day / ISO-week changes (Package 2 never called ``reset_day``/``reset_week``, so the
session trade cap and daily loss lock were permanent after the first day); the loss-streak
lock resets daily (``loss_streak_reset: daily``) instead of halting the system forever; the
cooldown is measured in structure periods; exposure reserved for an entry that is not (fully)
filled is released.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, Decimal
from typing import Any

from fabio_mechanical.contracts import MarketFeatureSnapshot, TradeCandidate


@dataclass
class RiskState:
    equity: Decimal
    day_start_equity: Decimal
    week_start_equity: Decimal
    daily_realized: Decimal = Decimal("0")
    weekly_realized: Decimal = Decimal("0")
    consecutive_losses: int = 0
    open_exposure: Decimal = Decimal("0")
    session_trades: int = 0
    last_trade_period: int = -10_000_000
    kill_switch: bool = False
    execution_error_lock: bool = False
    trading_day: str = ""
    iso_week: tuple[int, int] = (0, 0)
    reservations: dict[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    quantity: int = 0
    risk_amount: Decimal = Decimal("0")
    exposure: Decimal = Decimal("0")


class RiskEngine:
    def __init__(self, cfg: dict[str, Any], initial_equity: Decimal | None = None) -> None:
        self.cfg = cfg
        eq = Decimal(str(cfg["risk"]["initial_equity"])) if initial_equity is None else initial_equity
        self.state = RiskState(eq, eq, eq)
        self.seen_candidates: set[str] = set()

    # ------------------------------------------------------------------ calendar
    def reset_day(self) -> None:
        st = self.state
        st.day_start_equity = st.equity
        st.daily_realized = Decimal("0")
        st.session_trades = 0
        if self.cfg["risk"].get("loss_streak_reset", "daily") == "daily":
            st.consecutive_losses = 0

    def reset_week(self) -> None:
        self.state.week_start_equity = self.state.equity
        self.state.weekly_realized = Decimal("0")

    def roll_calendar(self, trading_day: str) -> bool:
        """Apply day/week resets when the CME trading day changes. Returns True on a new day."""
        st = self.state
        if not trading_day or trading_day == st.trading_day:
            return False
        first = st.trading_day == ""
        st.trading_day = trading_day
        week = date.fromisoformat(trading_day).isocalendar()[:2]
        if not first:
            self.reset_day()
            if week != st.iso_week:
                self.reset_week()
        st.iso_week = (int(week[0]), int(week[1]))
        return not first

    def set_kill_switch(self, on: bool = True) -> None:
        self.state.kill_switch = on

    def set_execution_error(self, on: bool = True) -> None:
        self.state.execution_error_lock = on

    # ------------------------------------------------------------------ gate
    def _locks(self, s: MarketFeatureSnapshot, c: TradeCandidate) -> str | None:
        r = self.cfg["risk"]
        st = self.state
        if st.kill_switch:
            return "kill_switch"
        if st.execution_error_lock:
            return "execution_error_lock"
        if not s.trade_safe:
            return "data_quality_lock"
        if s.stale_ms > self.cfg["strategy"]["stale_data_ms_max"]:
            return "stale_data_lock"
        if c.candidate_id in self.seen_candidates:
            return "duplicate_order_guard"
        if -st.daily_realized >= st.day_start_equity * Decimal(str(r["max_daily_loss_fraction"])):
            return "max_daily_loss"
        if r.get("weekly_loss_lock_enabled", True) and -st.weekly_realized >= st.week_start_equity * Decimal(
            str(r["max_weekly_loss_fraction"])
        ):
            return "max_weekly_loss"
        if st.consecutive_losses >= r["max_consecutive_losses"]:
            return "stop_after_loss_streak"
        if st.session_trades >= r["session_trade_cap"]:
            return "session_trade_cap"
        if s.structure_period_index - st.last_trade_period < r["cooldown_events"]:
            return "cooldown"
        return None

    def evaluate(self, s: MarketFeatureSnapshot, c: TradeCandidate, tick_value: Decimal) -> RiskDecision:
        reason = self._locks(s, c)
        if reason:
            return RiskDecision(False, reason)
        r = self.cfg["risk"]
        risk_frac = min(Decimal(str(r["risk_fraction"])), Decimal(str(r["max_risk_fraction"])))
        risk_amount = self.state.equity * risk_frac
        stop_ticks = abs(c.entry_reference_ticks - c.stop_ticks)
        if stop_ticks <= 0:
            return RiskDecision(False, "invalid_stop")
        per_contract = Decimal(stop_ticks) * tick_value
        qty_dec = (risk_amount / per_contract).to_integral_value(rounding=ROUND_DOWN)
        qty = int(max(Decimal("0"), min(qty_dec, Decimal(str(r["max_position_contracts"])))))
        if qty < 1:
            return RiskDecision(False, "insufficient_equity_for_min_contract")
        exposure = (per_contract * qty) / self.state.equity
        if exposure + self.state.open_exposure > Decimal(str(r["max_concurrent_exposure_fraction"])):
            return RiskDecision(False, "max_concurrent_exposure")
        st = self.state
        self.seen_candidates.add(c.candidate_id)
        st.session_trades += 1
        st.last_trade_period = s.structure_period_index
        st.open_exposure += exposure
        st.reservations[c.candidate_id] = exposure
        return RiskDecision(True, "allowed", qty, risk_amount, exposure)

    # ------------------------------------------------------------------ outcomes
    def on_entry_filled(self, candidate_id: str, requested: int, filled: int) -> Decimal:
        """Release exposure reserved for the unfilled part. Returns exposure kept for the position."""
        st = self.state
        reserved = st.reservations.pop(candidate_id, Decimal("0"))
        if requested <= 0:
            return Decimal("0")
        kept = reserved * Decimal(filled) / Decimal(requested)
        st.open_exposure = max(Decimal("0"), st.open_exposure - (reserved - kept))
        if filled == 0:
            st.session_trades = max(0, st.session_trades - 1)
        return kept

    def on_trade_closed(self, pnl: Decimal, released_exposure: Decimal = Decimal("0")) -> None:
        st = self.state
        st.equity += pnl
        st.daily_realized += pnl
        st.weekly_realized += pnl
        st.open_exposure = max(Decimal("0"), st.open_exposure - released_exposure)
        st.consecutive_losses = st.consecutive_losses + 1 if pnl < 0 else 0
