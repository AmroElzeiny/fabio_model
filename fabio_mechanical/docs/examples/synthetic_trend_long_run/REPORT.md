# Fabio Mechanical - run report

- run id: `619f9a60981c48a197b96064769273e0`  created: 2026-09-27T00:14:04.995868+00:00
- command: `backtest run`
- contract: FABIO_MECHANICAL_V1  schema sha256: `f4cd5a9f9d9ec62730f166ec8e2c884bcf983aa779a12ed129e1297c6152bdbf`
- config hash: `cbfdf133ba72e8bd58156c50ef6a99a97f9a91b9cb5d5a6945c3a0ddd3ac5624`  invariants: `73d3d613ed0ebbcb9220713d09ffde924c7681340389089eea9bc119e2e7e6da`
- code: 1.0.0 source sha256 `9ce79ebb4e9c02bc7ca5a20aac2a2ea23bda93761aefec18985d7847f66904db` git `None`
- dataset sha256: `297d746733f9a502633b64cf73f6408a0f90b1480641a3804584c418d045dd17`
- AI enabled: False  decision provider: MECHANICAL

## Summary

| metric | value |
|---|---|
| trades | 1 |
| gross_pnl | 2720.00 |
| fees | 42.0 |
| net_pnl | 2678.00 |
| ending_equity | 102678.000 |
| max_drawdown | None |
| entry_slippage_ticks_total | 16 |
| exit_slippage_ticks_total | 0 |
| fills | 5 |
| maker_fills | 3 |
| taker_fills | 2 |
| candidates | 1 |
| state_transitions | 18 |
| events | 69102 |
| snapshots | 17200 |
| trade_safe_at_end | True |

Data-quality reasons at end: none

## Metrics

| metric | value |
|---|---|
| average_loss | 0.0 |
| average_mae_ticks | -1.0 |
| average_mfe_ticks | 54.0 |
| average_win | 2678.0 |
| calmar | inf |
| cost_sensitivity | None |
| expectancy | 2678.0 |
| longest_loss_streak | 0 |
| max_drawdown | 0.0 |
| median_r | 8.9267 |
| monthly_stability | 1.0 |
| net_pnl | 2678.0 |
| net_return | 0.02678 |
| profit_factor | inf |
| regime_stability | 1.0 |
| sharpe | 0.0 |
| sortino | 0.0 |
| tail_loss_mean | 2678.0 |
| trades | 1 |
| trades_per_month | 1.0 |
| win_rate | 1.0 |

## Trades

| # | setup | dir | qty | entry | exit | stop | target | net | R | exit reason | entry (UTC) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | TREND | LONG | 10 | 20046.4000 | 20060.0000 | 20044.50 | 20060.00 | 2678.00 | 8.9267 | TARGET | 2026-01-13T14:40:59.333175+00:00 |

## Candidates

- order_submitted: 1

## State transitions (counts)

| model | from | to | reason | count |
|---|---|---|---|---|
| MEAN_REVERSION | BALANCED | BREAKOUT_ATTEMPT | breakout_attempt | 2 |
| MEAN_REVERSION | BREAKOUT_ATTEMPT | NO_ACCEPTANCE | no_acceptance | 2 |
| MEAN_REVERSION | NO_ACCEPTANCE | BALANCED | reference_balance_changed | 1 |
| TREND | ACTIVE_TRADE | EXIT | exit:TARGET | 1 |
| TREND | BALANCED | VALID_BREAKOUT | out_of_balance_breakout | 2 |
| TREND | DISPLACEMENT | STRUCTURE_BREAK | structure_break | 1 |
| TREND | ENTRY_READY | ACTIVE_TRADE | entry_filled | 1 |
| TREND | EXIT | BALANCED | post_exit_reset | 1 |
| TREND | IMPULSE_LOCKED | IMPULSE_PROFILE_READY | qualifying_lvn | 1 |
| TREND | IMPULSE_PROFILE_READY | WAIT_LVN_RETEST | wait_retest | 1 |
| TREND | LVN_TOUCHED | WAIT_AGGRESSION | wait_aggression | 1 |
| TREND | STRUCTURE_BREAK | IMPULSE_LOCKED | impulse_locked | 1 |
| TREND | VALID_BREAKOUT | DISPLACEMENT | displacement | 1 |
| TREND | WAIT_AGGRESSION | ENTRY_READY | directional_aggression_at_lvn | 1 |
| TREND | WAIT_LVN_RETEST | LVN_TOUCHED | lvn_retest | 1 |

Full detail: trades.csv, fills.csv, state_transitions.csv, candidates.csv, equity_curve.csv, journal.jsonl.
