from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

from fabio_mechanical.accounting import Portfolio, Position
from fabio_mechanical.contracts import Direction, Fill
from fabio_mechanical.data.instruments import instrument_spec
from fabio_mechanical.risk import RiskEngine
from fabio_mechanical.strategy import TrendMachine

from .fixtures.snapshots import snapshot, valid_trend_long

NQ_TICK = Decimal("5.00")


def cand_and_snap(cfg: dict[str, Any]) -> tuple[Any, Any]:
    s = valid_trend_long()
    c = TrendMachine(cfg).update(s)
    assert c is not None
    return c, s


def test_position_sizing_and_duplicate_guard(cfg: dict[str, Any]) -> None:
    c, s = cand_and_snap(cfg)
    r = RiskEngine(cfg)
    d = r.evaluate(s, c, NQ_TICK)
    # risk = 100000 * 0.35% = 350; stop 3 ticks * $5 = $15/contract -> 23, capped at 10
    assert d.allowed and d.quantity == 10 and d.risk_amount == Decimal("350.0000")
    d2 = r.evaluate(s, c, NQ_TICK)
    assert not d2.allowed and d2.reason == "duplicate_order_guard"


def test_all_hard_locks(cfg: dict[str, Any]) -> None:
    c, s = cand_and_snap(cfg)

    def reason(mut: Any, snap: Any = None) -> str:
        r = RiskEngine(copy.deepcopy(cfg))
        mut(r)
        return r.evaluate(snap or s, c, NQ_TICK).reason

    assert reason(lambda r: r.set_kill_switch(True)) == "kill_switch"
    assert reason(lambda r: r.set_execution_error(True)) == "execution_error_lock"
    assert reason(lambda r: None, snapshot(1, safe=False)) == "data_quality_lock"
    assert reason(lambda r: None, snapshot(1, stale=10_000)) == "stale_data_lock"
    assert reason(lambda r: setattr(r.state, "daily_realized", Decimal("-2500"))) == "max_daily_loss"
    assert reason(lambda r: setattr(r.state, "weekly_realized", Decimal("-5000"))) == "max_weekly_loss"
    assert reason(lambda r: setattr(r.state, "consecutive_losses", cfg["risk"]["max_consecutive_losses"])) == "stop_after_loss_streak"
    assert reason(lambda r: setattr(r.state, "session_trades", cfg["risk"]["session_trade_cap"])) == "session_trade_cap"
    assert reason(lambda r: setattr(r.state, "last_trade_period", s.structure_period_index - 1)) == "cooldown"
    assert reason(lambda r: setattr(r.state, "open_exposure", Decimal("0.02"))) == "max_concurrent_exposure"


def test_max_position_cap_and_min_contract(cfg: dict[str, Any]) -> None:
    c, s = cand_and_snap(cfg)
    cfg2 = copy.deepcopy(cfg)
    cfg2["risk"]["max_position_contracts"] = 3
    assert RiskEngine(cfg2).evaluate(s, c, NQ_TICK).quantity == 3
    tiny = RiskEngine(cfg, initial_equity=Decimal("1000"))
    assert tiny.evaluate(s, c, NQ_TICK).reason == "insufficient_equity_for_min_contract"


def test_daily_weekly_resets_and_exposure_release(cfg: dict[str, Any]) -> None:
    c, s = cand_and_snap(cfg)
    r = RiskEngine(cfg)
    r.roll_calendar("2026-01-13")
    d = r.evaluate(s, c, NQ_TICK)
    kept = r.on_entry_filled(c.candidate_id, d.quantity, d.quantity // 2)
    assert kept == d.exposure / 2 and r.state.open_exposure == kept
    r.on_trade_closed(Decimal("-100"), kept)
    assert r.state.open_exposure == 0 and r.state.consecutive_losses == 1 and r.state.daily_realized == Decimal("-100")
    r.state.session_trades = 6
    assert r.roll_calendar("2026-01-14") is True
    assert r.state.session_trades == 0 and r.state.daily_realized == 0 and r.state.consecutive_losses == 0
    assert r.state.weekly_realized == Decimal("-100")  # same ISO week
    r.roll_calendar("2026-01-19")  # next week
    assert r.state.weekly_realized == 0
    # entry that never filled releases everything and does not count as a session trade
    c2 = TrendMachine(cfg).update(snapshot(1000))
    assert c2 is not None
    d2 = r.evaluate(snapshot(1000), c2, NQ_TICK)
    assert d2.allowed
    r.on_entry_filled(c2.candidate_id, d2.quantity, 0)
    assert r.state.open_exposure == 0 and r.state.session_trades == 0


def _pos(cfg: dict[str, Any], inst: str = "NQ", direction: Direction = Direction.LONG) -> Position:
    return Position(
        "p1",
        inst,
        "NQH6",
        direction,
        instrument_spec(cfg, inst),
        "TREND",
        "c",
        "h",
        stop_ticks=396,
        target_ticks=420,
        entry_reference_ticks=400,
        opened_ns=1,
        trading_day="2026-01-13",
        session="NY_RTH",
    )


def test_accounting_pnl_nq_mnq_fees_and_r(cfg: dict[str, Any]) -> None:
    for inst, tick_value in (("NQ", Decimal("5.00")), ("MNQ", Decimal("0.50"))):
        p = Portfolio(Decimal("100000"))
        pos = _pos(cfg, inst)
        p.open(pos)
        p.add_entry_fill(pos, Fill(timestamp_ns=1, price_ticks=400, quantity=2, fee=Decimal("4.20")), 0)
        le = p.add_exit_fill(pos, Fill(timestamp_ns=2, price_ticks=404, quantity=2, fee=Decimal("4.20")), "TARGET", 0)
        assert le is not None
        assert le.gross_pnl == 4 * tick_value * 2 and le.fees == Decimal("8.40") and le.net_pnl == le.gross_pnl - Decimal("8.40")
        assert le.r_multiple == (le.net_pnl / (4 * tick_value * 2)).quantize(Decimal("0.0001"))
        assert p.cash == Decimal("100000") + le.net_pnl and not p.positions


def test_partial_exits_are_exact(cfg: dict[str, Any]) -> None:
    p = Portfolio(Decimal("100000"))
    pos = _pos(cfg, direction=Direction.SHORT)
    p.open(pos)
    p.add_entry_fill(pos, Fill(timestamp_ns=1, price_ticks=401, quantity=2, fee=Decimal("0")), 0)
    p.add_entry_fill(pos, Fill(timestamp_ns=1, price_ticks=400, quantity=1, fee=Decimal("0")), 0)
    assert p.add_exit_fill(pos, Fill(timestamp_ns=2, price_ticks=398, quantity=1, fee=Decimal("0")), "TARGET", 0) is None
    le = p.add_exit_fill(pos, Fill(timestamp_ns=3, price_ticks=399, quantity=2, fee=Decimal("0")), "STOP", 0)
    assert le is not None
    # entries 401,401,400 (1202) exits 398,399,399 (1196): 6 ticks * $5 exactly, no rounding residue
    assert le.gross_pnl == Decimal("30.00") and p.cash == Decimal("100030.00")
    assert le.exit_reasons == "TARGET+STOP" and le.exit_reason == "STOP"
