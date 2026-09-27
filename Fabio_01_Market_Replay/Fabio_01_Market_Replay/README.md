# Fabio_01_Market_Replay

Independent, deterministic market-replay and feature package for CME Globex NQ/MNQ. It intentionally stops before strategy, AI, order routing, or live execution.

## Scope

- Historical Databento `GLBX.MDP3` acquisition with **cost estimation before download**, immutable raw files, checksums and manifests.
- Preferred L3 `mbo`, plus `trades`, `mbp-10`, definitions and continuous-symbol resolution.
- Event-by-event MBO replay, displayed queue reconstruction, L2 aggregation, DOM/depth, pulling, stacking and replenishment evidence.
- Executed order flow: provider aggressor side first; explicitly labelled quote fallback only when provider side is unavailable.
- Delta/CVD, footprint, big prints, aggression and non-binary absorption evidence.
- Session/developing/composite/arbitrary/leg profile primitives with POC/VAH/VAL and zone-based LVN/HVN.
- Auction-state feature output following the documented `Market State -> Location -> Aggression` structure without trade decisions.
- Versioned Pydantic + JSON Schema contracts. Main integration output: `MarketFeatureSnapshot`.
- Data-quality locks: severe issues set `trade_safe=false`.

## Install

```bash
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\\Scripts\\activate  # Windows
pip install -e '.[all]'
```

The package itself and synthetic tests do not require a Databento key. Real downloads require `DATABENTO_API_KEY`.

## CLI

```bash
fabio-replay schema-hash
fabio-replay generate-fixture --output tests/fixtures/known_session.jsonl
fabio-replay replay --input tests/fixtures/known_session.jsonl
fabio-replay derive-features --input tests/fixtures/known_session.jsonl --output examples/snapshots.jsonl

# Estimate BEFORE download
fabio-replay cost-estimate --symbol NQ.v.0 --schema mbo --start 2026-01-05 --end 2026-01-06
fabio-replay download --symbol NQ.v.0 --schema mbo --start 2026-01-05 --end 2026-01-06 --output data/raw/nq-20260105.mbo.dbn.zst
fabio-replay validate data/raw/nq-20260105.mbo.dbn.zst
```

## Price/time representation

Internal strategy-facing prices use integer ticks. Databento raw prices are converted from their fixed 1e-9 units using the instrument tick size. Timestamps are UTC nanoseconds. Historical raw DBN files are not rewritten.

## Continuous contracts

Use `NQ.v.0` / `MNQ.v.0` to resolve the most-active real contract. The system treats continuous symbology as a resolver to real instruments and never back-adjusts prices.

## Safety boundary

There is no entry/exit engine, position sizing, broker adapter, MT5 feed, AI model, or live order path in this package.
