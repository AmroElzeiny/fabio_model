# Architecture

## 1. Data flow

```
Databento DBN (.dbn/.dbn.zst)      JSONL MarketEvents      synthetic raw MBO (Databento layout)
            │                               │                            │
            └──────────────► ingest/sources.py (streaming EventSource) ◄──┘
                                            │   DatabentoMBOAdapter: codes, UNDEF price, integer ticks,
                                            │   instrument_id→contract (definitions), CONTRACT_ROLL,
                                            │   SNAPSHOT(complete) after the F_SNAPSHOT block
                                            ▼
                               MarketEvent (canonical contract)
                                            │
                     ┌──────────────────────┴───────────────────────┐
                     ▼                                              ▼
        backtest/engine.py (per event)                     replay/engine.py
   1. execution.on_before_event  ── orders whose latency elapsed match the book as of the previous packet
   2. replay.process(event)      ── quality → book → [trade: flow, footprint, profile, structure, auction]
                                    → at packet boundary with a trade: MarketFeatureSnapshot
   3. execution.on_after_event   ── trades fill resting targets (queue) / trigger stops; stops sweep at boundary
   4. strategy on the snapshot   ── Trend FSM → Mean-Reversion FSM → TradeCandidate
                                    → DecisionProvider (mechanical) → RiskEngine → ExecutionIntent (arrives + latency)
   5. fills → Portfolio (positions, bracket OCO, ledger) → Journal
```

The core is single-threaded because event order is state. Parallelism is only allowed
between independent research runs (optimizer candidates, robustness scenarios).

## 2. No-lookahead guarantees
- A snapshot is built from events whose `ts_recv` is at or before its `timestamp_ns`, and only
  at a packet boundary where the book is consistent.
- Structure evaluations (balance windows, acceptance) use completed structure periods only.
  Leg profiles freeze when the leg locks.
- The strategy acts only after the event is fully applied. Orders reach the book `latency_ms`
  later, at the first packet boundary at or after arrival. Nothing fills against a candle close.
- `LookaheadError` is raised if snapshots are not strictly increasing in `event_index` and
  non-decreasing in time.
- Tests prove it: prefix invariance (decisions and fills before T are identical with or
  without data after T), bit-for-bit determinism, and checkpoint/restart equivalence.

## 3. Contract

`contracts/models.py` is the single schema. `contracts/schema.py` computes the canonical
SHA256 over a library-independent descriptor of all models: names, normalized field types,
constraints, defaults and enum members, for the 13 top-level models and every nested model.
Pydantic's JSON Schema output is not hashed, because it varies between pydantic releases.
`CONTRACT_SCHEMA_SHA256.txt` pins the hash, and `verify_schema_pin()` runs in every run manifest
and in the tests. Main models:

| Model | Producer → consumer |
|---|---|
| `MarketEvent` | adapters → replay |
| `TradePrint`, `BookSnapshot`, `FootprintSnapshot`, `VolumeProfileSnapshot`, `BalanceArea`, `AuctionFeatureSnapshot` | market engines → `MarketFeatureSnapshot` |
| `MarketFeatureSnapshot` | replay → strategy (the **only** strategy input) |
| `DecisionSnapshot` | FSM / decision provider (`ai_enabled` is typed `Literal[False]`) |
| `TradeCandidate` | FSM → risk (geometry validated in the model) |
| `ExecutionIntent`, `ExecutionReport`, `Fill` | backtest ↔ simulator |

Prices meant for exact arithmetic are integer ticks, money is `Decimal`, and time is UTC ns.

## 4. Configuration

| File | Content | Tunable by optimizer |
|---|---|---|
| `fabio_invariants.yaml` | frozen Fabio rules (decision order, sequences, POC target, structural stop, never widen, AI off, …) | never |
| `interpretation.yaml` | `strategy` (P2 quantitative definitions), `structure` (operational definitions added by the integration), `market` (P1 measurement definitions), `experimental` (all OFF) | only `strategy` keys listed in the search space |
| `search_space.yaml` | allowed ranges, units, rationale | — |
| `risk.yaml` | hard risk engine, flatten time | no |
| `execution.yaml` | latency, slippage, protection band, fees, partial fills, bracket model | no |
| `data.yaml` | dataset, instruments, sessions/calendar, quality thresholds, paths | no |
| `research.yaml` | phases (development/validation/OOS), optimizer, walk-forward, Monte Carlo, robustness grids | no |

Every file is validated by strict pydantic models (`configs/validation.py`: unknown keys,
bounds, ordering, session labels, disjoint phases). Cross-checks reject a search space that
overlaps the invariants or excludes the baseline value, `AI_ENABLED: true`, and experimental
flags in a baseline. `apply_strategy_params` is the only way the optimizer changes a config;
any other key raises `FrozenInvariantError`. Each run records `config_hash`, `invariant_hash`,
`search_space_sha256`, `schema_sha256`, code identity (package version, git commit if
available, source-tree SHA256) and the dataset SHA256.

## 5. Modules
- `ingest/`: downloader (cost-first, capped, immutable, manifests, batch resume), MBO adapter,
  sources, synthetic generator.
- `orderbook/`: MBO book (sorted levels, Databento priority rules, fill/pull attribution,
  queue-ahead, sweeps).
- `orderflow/`: incremental Delta/CVD, rolling window, z-score, normalized CVD slope,
  histogram percentiles, absorption evidence, footprint.
- `profile/`: the single `profile_from_vap` implementation (session, composite and leg
  profiles), LVN/HVN zones, `ZoneTracker`.
- `market/`: calendar, session auction features (P1), structure layer (balance, excursion,
  impulse and reclaim legs).
- `quality/`: data-quality engine.
- `replay/`: the event loop that assembles the canonical snapshot.
- `strategy/`, `decision/`, `risk/`, `execution/`, `accounting/`, `backtest/`, `journal/`,
  `research/`, `reporting/`, `cli/`: see README.

## 6. Performance and memory
- Everything streams: DBN files decode incrementally, sources are re-iterable generators,
  journals stream to disk, and the snapshot export writes as it goes.
- State is bounded: rolling windows, per-price maps (session/leg profiles, book levels), a
  bounded dedupe window and bounded archived balances. Measured memory stays flat as data
  grows (`tests/test_hygiene_performance.py`).
- Throughput (see `TEST_REPORT.md`) is about 20–25k MBO events/s on a laptop. The cost is
  dominated by one snapshot per trade packet (~0.2 ms). The session profile refreshes at most
  once per `session_profile_refresh_ms`.
- Correctness came before speed: the incremental computations were checked against direct
  recomputation in tests.
