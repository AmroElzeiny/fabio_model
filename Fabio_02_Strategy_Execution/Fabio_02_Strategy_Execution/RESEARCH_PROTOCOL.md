# RESEARCH_PROTOCOL

## Required phases

1. **BASELINE PURE FABIO** — run frozen mechanical rules with default interpretations.
2. **DEVELOPMENT** — tune only whitelisted interpretation parameters.
3. **WALK-FORWARD** — optimize on each training/calibration window, freeze selected values, evaluate the following validation window.
4. **VALIDATION** — evaluate fixed configuration on untouched validation data.
5. **LOCKED OOS** — create an OOS manifest containing config hash, schema hash and code commit identifier before opening OOS data. Optimization is forbidden in this phase.
6. **ROBUSTNESS** — cost, latency, threshold, level-error, missing-event, session, volatility, regime, news-tag, roll-date and NQ/MNQ sensitivity.

## Optimization policy

The optimizer can only modify keys in `configs/search_space.yaml`. Frozen invariants are not present in the search space and mutation attempts raise `FrozenInvariantError`.

Candidate selection retains all raw metrics and uses a documented, explicit multi-metric score rather than net profit alone. Plateau analysis keeps near-best solutions to expose parameter stability instead of preferring a sharp isolated optimum.

## OOS protocol

Before OOS:

- freeze configuration;
- save SHA256;
- save schema SHA256;
- save current Git commit when available;
- do not tune after viewing OOS.

`fabio.research.locking` implements the manifest and mismatch guard.

## Monte Carlo

Deterministic seeded trade-order bootstrap and block bootstrap report drawdown, loss streak, recovery time, terminal equity and empirical ruin-style frequency. These are scenario statistics, not guarantees.
