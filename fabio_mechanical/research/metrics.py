"""Trade-ledger metrics (Package 2).

Integration fix: Package 2 filled ``trades_per_month``, ``monthly_stability`` and
``regime_stability`` with placeholders (trade count / 1.0 when profitable). They are now
computed from trade timestamps and setup types.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any


def _get(x: Any, key: str, default: Any = 0) -> Any:
    return x.get(key, default) if isinstance(x, dict) else getattr(x, key, default)


def _f(x: Any) -> float:
    return float(x)


def compute_metrics(ledger: list[Any], initial_equity: float = 100000.0) -> dict[str, Any]:
    pnls = [_f(_get(x, "net_pnl")) for x in ledger]
    rs = [_f(_get(x, "r_multiple")) for x in ledger]
    mfes = [_f(_get(x, "mfe_ticks")) for x in ledger]
    maes = [_f(_get(x, "mae_ticks")) for x in ledger]
    n = len(pnls)
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    net = sum(pnls)
    expectancy = net / n if n else 0.0
    pf = (sum(wins) / abs(sum(losses))) if losses else (math.inf if wins else 0.0)
    wr = len(wins) / n if n else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    eq = initial_equity
    peak = eq
    maxdd = 0.0
    rets: list[float] = []
    longest = cur = 0
    for p in pnls:
        prev = eq
        eq += p
        rets.append(p / prev if prev else 0.0)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak if peak else 0.0)
        if p < 0:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    mean = statistics.fmean(rets) if rets else 0.0
    std = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    downside = math.sqrt(statistics.fmean([min(0.0, r) ** 2 for r in rets])) if rets else 0.0
    sharpe = mean / std * math.sqrt(max(n, 1)) if std else 0.0
    sortino = mean / downside * math.sqrt(max(n, 1)) if downside else 0.0
    net_return = net / initial_equity
    calmar = net_return / maxdd if maxdd else (math.inf if net_return > 0 else 0.0)
    tail = sorted(pnls)[: max(1, math.ceil(n * 0.05))] if pnls else []
    # time structure
    months: dict[str, float] = {}
    for x in ledger:
        ts = int(_get(x, "exit_time_ns", 0) or _get(x, "entry_time_ns", 0) or 0)
        key = datetime.fromtimestamp(ts / 1e9, tz=timezone.utc).strftime("%Y-%m") if ts else "unknown"
        months[key] = months.get(key, 0.0) + _f(_get(x, "net_pnl"))
    span_months = 0.0
    times = [int(_get(x, "entry_time_ns", 0) or 0) for x in ledger if _get(x, "entry_time_ns", 0)]
    if times:
        span_months = max(1.0, (max(times) - min(times)) / 1e9 / 86400 / 30.4375)
    regimes: dict[str, float] = {}
    for x in ledger:
        key = str(_get(x, "setup_type", "unknown"))
        regimes[key] = regimes.get(key, 0.0) + _f(_get(x, "net_pnl"))
    return {
        "trades": n,
        "net_pnl": net,
        "net_return": net_return,
        "expectancy": expectancy,
        "profit_factor": pf,
        "median_r": statistics.median(rs) if rs else 0.0,
        "max_drawdown": maxdd,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "win_rate": wr,
        "average_win": avg_win,
        "average_loss": avg_loss,
        "tail_loss_mean": statistics.fmean(tail) if tail else 0.0,
        "longest_loss_streak": longest,
        "average_mfe_ticks": statistics.fmean(mfes) if mfes else 0.0,
        "average_mae_ticks": statistics.fmean(maes) if maes else 0.0,
        "trades_per_month": (n / span_months) if span_months else float(n),
        "monthly_stability": (sum(1 for v in months.values() if v > 0) / len(months)) if months else 0.0,
        "regime_stability": (sum(1 for v in regimes.values() if v > 0) / len(regimes)) if regimes else 0.0,
        "cost_sensitivity": None,
    }
