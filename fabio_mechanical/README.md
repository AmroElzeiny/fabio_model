# Fabio Mechanical

One integrated, deterministic research system for CME Globex NQ/MNQ:

```
Databento GLBX.MDP3 (MBO + definitions)
 → immutable raw files (SHA256 manifests)
 → event-by-event replay (ts_recv clock, packet boundaries)
 → MBO order book (price-time queues)
 → executed order flow (Delta/CVD, big prints, absorption evidence)
 → footprint · volume profiles (POC/VAH/VAL, LVN/HVN)
 → auction structure (balance, breakout, acceptance, failed auction, impulse & reclaim legs)
 → MarketFeatureSnapshot  (canonical contract FABIO_MECHANICAL_V1)
 → mechanical Fabio strategy: Trend / Mean-Reversion FSMs  (Market State → Location → Aggression)
 → TradeCandidate → hard risk engine → event-driven execution simulator
 → position management (stop + target, OCO) → accounting → journal
 → research: baseline freeze, optimization, walk-forward, OOS lock, robustness, Monte Carlo, ablation
```

There is **no AI in the runtime**. The only decision provider is the mechanical one;
`AIProviderStub` raises if called, and `AI_ENABLED: true` fails validation.

This project merges `Fabio_01_Market_Replay` and `Fabio_02_Strategy_Execution`. See
`INTEGRATION_REPORT.md` for what came from where and what changed.

## Install

Python 3.10 or newer.

```bash
python -m venv .venv
pip install -e ".[all]"
```

Activate the venv between the two commands (`.venv\Scripts\activate` on Windows,
`source .venv/bin/activate` elsewhere). The extras are optional: `databento` is needed only
for real data and `pyarrow` only for Parquet export. The core package, synthetic fixtures and
all tests run without a Databento key. Real downloads read `DATABENTO_API_KEY` from the
environment or from `.env` (copy `.env.example`).

## Quick start (synthetic data, no key)

```bash
fabio config validate
fabio schema hash
fabio backtest run --synthetic trend_long --output-dir runs/demo-trend
fabio report --run-dir runs/demo-trend
fabio self-check --output-dir runs/self-check
pytest -q
```

The synthetic scenarios are Databento-shaped raw MBO records that go through the real adapter:
`trend_long`, `trend_short`, `trend_long_no_aggression`, `mean_reversion_short`,
`mean_reversion_long`, `quiet_balance` and `roll`.

## First real NQ test

Follow `FIRST_REAL_TEST.md`: cost estimate → capped download → validation → contract
resolution → replay → features → baseline backtest → inspection.

## CLI

| Command | Purpose |
|---|---|
| `fabio data cost` | Databento cost / bytes / record count. Never downloads. |
| `fabio data download` | Capped download (`--max-cost`). Stream or `--batch`. Immutable output plus manifest. |
| `fabio data validate` | Checksum manifests; with `--deep`, a structural scan (snapshot present, `ts_recv` order, unresolved instruments). |
| `fabio data resolve` | Continuous symbol → real contract (API, or offline from definition files). |
| `fabio data synthetic` | Write a synthetic scenario as JSONL events or DBN + definitions. |
| `fabio replay run` | Event-by-event replay: data quality, throughput, last structure state. |
| `fabio features build` | Stream `MarketFeatureSnapshot`s to JSONL/Parquet (`--sample-ms`, `--compact`). |
| `fabio backtest run` | Pure Fabio backtest with full journal, trades, fills, state transitions, costs, manifest and report. |
| `fabio report` | Rebuild `REPORT.md` for a run directory. |
| `fabio research freeze-baseline` | Run and freeze the baseline (required before any optimization). |
| `fabio research optimize` | Reproducible search over `search_space.yaml`, development days only. |
| `fabio research walk-forward` | Rolling optimize → freeze → validate. |
| `fabio research robustness` | Cost/latency grid, threshold jitter, level error, missed updates, session, instrument. |
| `fabio research ablation` | Remove one evidence family at a time. |
| `fabio research monte-carlo` | Seeded trade-order and block bootstrap of a run's trades. |
| `fabio research lock-oos` / `verify-oos` | Write-once OOS lock manifest / verification. |
| `fabio schema hash` / `schema export` | Canonical contract hash / JSON schema bundle. |
| `fabio config validate` / `config show` | Strict validation and hashes / effective merged config. |
| `fabio self-check` | Synthetic end-to-end smoke of every subsystem. |
| `fabio bench` | Replay/backtest throughput and memory. |

`fabio --config-dir <dir>` runs any command against a copy of the configs, so you never have
to edit the package defaults.

## Layout

```
fabio_mechanical/            (repository root = the Python package)
  contracts/   canonical FABIO_MECHANICAL_V1 models, pinned schema hash, legacy adapters
  configs/     fabio_invariants · interpretation · search_space · risk · data · execution · research (+ validation)
  data/        instrument reference, dataset manifests, code identity; data/raw (immutable), data/cache
  ingest/      Databento downloader + MBO adapter, local JSONL, streaming sources, synthetic raw MBO
  replay/      event-by-event replay engine → MarketFeatureSnapshot
  orderbook/   MBO book, MBP-10 book
  orderflow/   Delta/CVD/aggression/absorption, footprint
  profile/     volume profile, LVN/HVN zones
  market/      sessions/calendar, session auction features, auction structure layer
  quality/     data-quality engine (hard trade_safe lock)
  strategy/    Trend & Mean-Reversion FSMs, location/aggression checks, targets/stops
  decision/    DecisionProvider, MechanicalDecisionProvider, AIProviderStub (disabled)
  risk/        hard risk engine
  execution/   event-driven execution simulator, adapters (live adapters are disabled stubs)
  accounting/  positions, ledger, NQ/MNQ PnL
  backtest/    event-driven backtest engine
  research/    metrics, optimize, walk-forward, robustness, Monte Carlo, ablation, protocol locks
  reporting/   CSV/JSON/Parquet export, run manifests, Markdown report
  journal/     streaming decision/execution journal
  cli/         `fabio` command line
  tests/       unit, integration, end-to-end (raw synthetic MBO), research, CLI, hygiene
  docs/        supplementary notes
```

## Documents

| File | Content |
|---|---|
| `FABIO_SPEC.md` | the mechanical baseline being implemented |
| `RULE_TRACEABILITY.md` | each rule → code → config key; every quantitative definition |
| `ARCHITECTURE.md` | data flow, processing order, contracts, configs, performance |
| `DATA_GUIDE.md` | Databento acquisition, budget, file handling, data quality |
| `FIRST_REAL_TEST.md` | the first real NQ run, step by step |
| `RESEARCH_PROTOCOL.md` | the scientific protocol and how it is enforced |
| `INTEGRATION_REPORT.md` | the merge: origins, conflicts, changes, limitations |
| `TEST_REPORT.md` | verification results |
| `docs/examples/synthetic_trend_long_run/` | the outputs of one backtest run (report, trades, fills, state transitions, manifest) |
| `docs/SYNTHETIC_SCENARIOS.md`, `docs/DATABENTO_NOTES.md` | test scenarios; Databento feed semantics |

## Safety boundaries

- No live order path exists; the MT5/futures adapters raise.
- There is no stop widening (`validate_stop_update` is the only stop-change path), no
  martingale, no averaging down.
- Risk locks sit below the decision layer and cannot be overridden by any provider.
- Unsafe or stale data blocks entries; unsafe data also rejects orders at arrival.
- The optimizer can only change `search_space.yaml` keys, within their bounds.
