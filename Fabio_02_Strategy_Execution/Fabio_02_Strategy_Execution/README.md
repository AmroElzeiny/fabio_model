# Fabio_02_Strategy_Execution

Mechanical, research-first implementation of a Fabio Valentini-style Auction Market Theory strategy. **No AI calls exist in this package.** The enabled decision path is `MechanicalDecisionProvider`; `AIProviderStub` raises if invoked.

## Scope

`Market State -> Location -> Aggression`, with two explicit models:

- Trend continuation: out-of-balance -> structure-breaking impulse -> impulse Volume Profile -> LVN retest -> directional aggression -> entry.
- Mean reversion: failed breakout -> no acceptance -> value reclaim -> reclaim-leg Volume Profile -> LVN retest -> snap-back aggression -> entry toward balance POC.

The package includes contracts, configuration, deterministic FSMs, target/stop logic, hard risk locks, bid/ask and depth-aware execution simulation, NQ/MNQ accounting, event-driven backtesting, optimization, walk-forward validation, OOS locking, robustness, Monte Carlo, ablation, journaling and report export.

## Source basis

Primary codified public source reviewed before implementation:
- https://www.chartfanatics.com/strategies/auction-market-strategy
- Referenced video: https://youtu.be/tvERE-Beu2U

Only explicitly documented directional concepts are treated as Fabio rules. Numeric thresholds not specified by the source are isolated in `configs/interpretation.yaml` and `configs/search_space.yaml` as tunable quantitative interpretations.

## Install / run

```bash
python -m pip install -e .[test]
python -m fabio.cli schema-hash
pytest -q
python -m fabio.cli self-check
```

`self-check` exports reproducible sample outputs under `sample_reports/`.

## Safety boundaries

- No live orders.
- MT5 and futures adapters are stubs only.
- No stop widening, martingale, averaging down or recovery grid.
- OOS configuration can be cryptographically frozen and verified.
- Risk locks sit below the decision layer and are not overrideable by the future AI interface.
