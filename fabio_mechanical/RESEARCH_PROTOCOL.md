# RESEARCH_PROTOCOL

Rule zero: **never pick a good-looking result by hand and hide the rest.** Every search writes
every candidate with its raw metrics, and selection is an automatic, documented score.

## Phases

1. **Baseline Pure Fabio.** The frozen invariants plus the default interpretations, run on
   development data and then frozen. Nothing is optimized before this.
2. **Development.** Tune only the `search_space.yaml` keys, only on development days.
3. **Walk-forward.** Optimize on each training window, freeze, and evaluate the next window.
   All inside development.
4. **Validation.** Evaluate one frozen configuration on the validation days. No tuning.
5. **Locked OOS.** Write the lock manifest before opening OOS data. One evaluation, no tuning
   afterwards.
6. **Robustness.** Cost/latency grid, threshold jitter, level error, missed updates, session
   labels, NQ vs MNQ valuation, and tag breakdowns (session, setup, regime, volatility, news,
   day).

## How the protocol is enforced

| Requirement | Enforcement |
|---|---|
| Phases are configured and disjoint | `research.yaml: phases` validated (no overlap; OOS strictly after development). Research commands on real data refuse to run until the phases are set. |
| Baseline frozen before optimization | `fabio research freeze-baseline` writes the baseline manifest (config, invariants, schema, dataset hashes, code identity, summary). `optimize` and `walk-forward` require it, and their base config must hash identically. A baseline with experimental flags ON cannot be frozen. |
| The optimizer never sees OOS | Up front, sources labelled outside development are refused (`OOSLockError`). At runtime, a day filter keeps every snapshot of a non-development CME trading day away from the strategy and the metrics. |
| Invariants are untouchable | Only `apply_strategy_params` changes a config: search-space keys only, within bounds. Everything else raises `FrozenInvariantError`. Tests check `invariant_hash()` is unchanged after a search. |
| Reproducible searches | Candidates are fully determined by (search-space SHA256, mode, max_candidates, seed). The baseline is always candidate #0. The output records the seed, the search-space hash, the base config hash, and each candidate's config hash. |
| Plateau, not spikes | The output holds `plateau` (all candidates within `plateau_tolerance` of the best score) and `parameter_sensitivity` (mean score per value of each parameter). |
| Explicit selection | The weighted score (expectancy, profit factor, Sharpe, Calmar, drawdown penalty) is recorded in the output. Candidates below `min_trades` are ineligible. |
| OOS integrity | `fabio research lock-oos` is write-once and records config, invariants, schema, code and dataset hashes. `fabio backtest run --phase oos` needs `--oos-lock` and refuses any changed config, schema or invariant. |
| Randomness | Monte Carlo, jitter and missed-update tests are seeded (`deterministic_seed`). |

## Commands

```bash
fabio research freeze-baseline --input data/raw/nq_dev --output runs/research/baseline.json
fabio research optimize --input data/raw/nq_dev --baseline runs/research/baseline.json --output runs/research/optimize.json --mode random --max-candidates 50 --seed 42
fabio research walk-forward --input data/raw/nq_dev --baseline runs/research/baseline.json --output runs/research/wfo.json --train-days 10 --validation-days 5 --step-days 5
fabio research robustness --input data/raw/nq_dev --output runs/research/robustness.json
fabio research ablation --input data/raw/nq_dev --output runs/research/ablation.json
fabio backtest run --input data/raw/nq_val --phase validation --output-dir runs/research/validation
fabio research lock-oos --input data/raw/nq_oos --output runs/research/oos_lock.json
fabio backtest run --input data/raw/nq_oos --phase oos --oos-lock runs/research/oos_lock.json --output-dir runs/research/oos
fabio research monte-carlo --trades runs/research/oos/trades.csv --output runs/research/oos_mc.json
```

For synthetic smoke runs, replace `--input …` with `--synthetic mean_reversion_short` or
`--synthetic-days 5`. Synthetic data has no real calendar, so the phase guards do not apply.

## Monte Carlo

Seeded trade-order bootstrap alternates with block bootstrap. It reports the drawdown p50/p95,
the p95 loss streak, the p95 recovery length, the p05/p50 terminal equity, and the fraction of
paths that reach zero equity. These are scenario statistics, not guarantees.
