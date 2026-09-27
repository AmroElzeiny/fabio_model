# Integration report

This document records how `Fabio_01_Market_Replay` (Package 1, "P1") and
`Fabio_02_Strategy_Execution` (Package 2, "P2") were merged into the single
`fabio_mechanical` system: what each package contributed, which conflicts were found,
how they were resolved, every behaviour that changed and why, and what is still limited.

Nothing here adds AI, new indicators, or result-driven optimization. Changes to trading
behaviour are listed one by one in section 5.

## 1. Starting point (audit)

| | Package 1 (market replay) | Package 2 (strategy/execution) |
|---|---|---|
| Layout | `src/fabio_market_replay` + a `contracts/` re-export shim | top-level packages named `fabio` and `contracts` |
| CLI | `fabio-replay` | `fabio` (demo-* commands only) |
| Python | >= 3.10 | >= 3.11 |
| Contract | `FABIO_MECHANICAL_V1`, hash `9a3f16…` (8 models) | `FABIO_MECHANICAL_V1`, its own hash (11 models) |
| Prices / time | integer ticks, UTC ns | `Decimal` prices, `datetime` |
| Own tests | 28 pass in isolation | 42 pass in isolation |

Both packages used the same contract version string, but their `MarketFeatureSnapshot`
definitions were incompatible. P2's FSMs could not read P1's snapshots: P2 expected balance,
breakout, acceptance, displacement, structure-break, impulse and reclaim fields plus
impulse/reclaim leg profiles. P1 never computed any of them. On top of that, units and
semantics differed in several shared concepts (section 3).

## 2. What came from each package

### From Package 1 (kept, and fixed where listed in section 6)
- The Databento `GLBX.MDP3` downloader: `get_cost`/`get_billable_size`/`get_record_count`
  before any download, immutable raw files, SHA256 manifests, batch resume with a request
  fingerprint, and continuous-symbol resolution → `ingest/databento.py`.
- The MBO order book: price-time queues, L2 aggregation, DOM imbalance, microprice, queue
  position, pulling, stacking and replenishment → `orderbook/book.py`. Also the MBP-10 book
  → `orderbook/mbp10.py`.
- Executed order flow: provider aggressor first with a labelled quote fallback, Delta/CVD,
  rolling delta, slope/acceleration, big prints, aggression persistence and non-binary
  absorption evidence → `orderflow/engine.py`.
- The footprint: diagonal and stacked imbalances, unfinished-auction evidence, arbitrary
  windows and time bars → `orderflow/footprint.py`.
- Volume profile: POC/VAH/VAL, shape, zone-based LVN/HVN, composite and arbitrary profiles
  → `profile/volume_profile.py`.
- Session auction features (`state_candidate`, value overlap/migration, efficiency, …)
  → `market/features.py`.
- The data-quality engine with a sticky `trade_safe` lock → `quality/engine.py`.
- Event replay → `replay/engine.py`. Session/DST handling → `market/session.py`.
- Research data helpers (event slices, session segmentation, regime tag, macro-event CSV
  hook, roll map) → `research/data_tools.py`.
- The `known_session.jsonl` fixture and all 28 P1 tests (ported).

### From Package 2 (kept, and fixed where listed in section 5)
- Frozen invariants, interpretation and search-space configs, and `FrozenInvariantError`
  → `configs/`.
- The Trend and Mean-Reversion FSMs (state graphs unchanged) → `strategy/fsm.py`.
- LVN qualification, the aggression gate, absorption blocking, POC targets, structural stops
  and the no-widen guard → `strategy/common.py`, `strategy/target_stop.py`.
- `DecisionProvider`, `MechanicalDecisionProvider` and `AIProviderStub` → `decision/`.
- The hard risk engine: sizing formula and all locks → `risk/engine.py`.
- Execution semantics: depth sweep, `default_slippage_ticks`, latency, fees, partial fills,
  queue model, gap rejection, and disabled MT5/futures adapter stubs → `execution/`.
- NQ/MNQ accounting and R multiples → `accounting/`.
- Metrics, explicit multi-metric score, plateau analysis, walk-forward, robustness matrix,
  seeded Monte Carlo, ablation and OOS lock → `research/`.
- The P2 FSM, risk and execution tests (ported to the canonical contract with consistent
  tick geometry, in `tests/fixtures/snapshots.py`).

### New integration components (in neither package)
- **The canonical contract** (`contracts/models.py`): one superset schema with a pinned hash.
- **The market-structure layer** (`market/structure.py`): balance areas, excursions, acceptance,
  failed auctions, reclaims, displacement z-score, impulse and reclaim legs with frozen leg
  profiles, and balance-POC target candidates. P2 needed these; nobody computed them.
- **An event-driven backtest** (`backtest/engine.py`) that runs from raw events to fills. P2
  backtested a list of pre-built snapshots.
- **An event-driven execution simulator.** Latency is modelled against the book at arrival,
  passive targets fill through MBO queue positions, and stops trigger on trades and fill
  through gaps.
- **Streaming event sources** (`ingest/sources.py`): Databento DBN files (`instrument_id`
  mapped to the real contract through definition records, contract-roll detection, snapshot
  completion marker), JSONL files, and generated data.
- **A synthetic raw-MBO generator** (`ingest/synthetic.py`) with Databento record layout and
  T→F→C matching, used for every end-to-end test.
- **Run manifests** (config, invariants, schema, code identity, dataset hashes), streaming JSONL
  journals and Markdown run reports.
- **Research protocol enforcement:** baseline freeze, development-only optimization with a
  runtime day filter, and write-once OOS locks.
- **One CLI, `fabio`**, with a benchmark and a self-check.

## 3. Conflicts found and how they were resolved

| # | Conflict | Resolution |
|---|---|---|
| 1 | Two different `MarketFeatureSnapshot` schemas under the same version | One canonical superset in `contracts/models.py`. It uses P1 units (ticks, UTC ns) and adds P2's strategy fields in tick units. The two pre-integration hashes are superseded by the pinned canonical hash `f4cd5a9f…bdbf` (`contracts/CONTRACT_SCHEMA_SHA256.txt`). A test fails if any schema changes without a re-pin. `contracts/legacy.py` documents every field mapping and migrates P1 snapshot JSON. |
| 2 | P2 needed balance/breakout/impulse/reclaim context that P1 never produced | New `market/structure.py` (definitions in `RULE_TRACEABILITY.md` §2). It reads P2's own interpretation keys (`balance_value_overlap_min`, `poc_migration_max`, `directional_efficiency_*`, `acceptance_event_count`, `pullback_depth_min`) plus operational settings under `structure:`. |
| 3 | Same field names with different meanings (`value_overlap`, `poc_migration_ticks`, `directional_efficiency`) | P1 keeps its session-level names. P2's balance-classification measures became `balance_value_overlap`, `balance_poc_migration_ticks` and `balance_directional_efficiency`. |
| 4 | P2 reused `impulse_*` for the mean-reversion reclaim leg, which collides when both models watch the same market | Separate `reclaim_leg_*` and `failed_auction_extreme_ticks` fields. |
| 5 | Session labels (P1 `NY_RTH`/`GLOBEX_OVERNIGHT`/`POST_RTH`, P2 `NEW_YORK`/`LONDON`) | One configurable calendar (`data.yaml`) with `NY_RTH`, `LONDON` (08:00 Europe/London), `POST_RTH`, `MAINTENANCE` and `GLOBEX_OVERNIGHT`. P2's `NEW_YORK` was renamed to `NY_RTH`. Labels are validated. |
| 6 | Unit of P2's `*_events` counters vs P1's per-trade snapshots (40 "events" = a few seconds of NQ trades) | "Events" are **structure periods** (`structure.period_seconds: 60`), exposed as `structure_period_index`. |
| 7 | LVN "relative volume": P1 `relative_volume_metric = 1 - vol/median` (clamped) vs P2 `relative_volume <= 0.55` | New `ProfileZone.volume_to_median_ratio` carries P2's semantics. P1's field is unchanged. |
| 8 | CVD slope units: P1 contracts/trade vs P2 normalized 0–1 | New `cvd_slope_normalized` (OLS slope / mean trade size, in [-1, 1]). P1's `cvd_slope` is kept. |
| 9 | Big prints: P1 threshold metrics vs P2 one non-directional percentile | `big_buy_print_percentile` / `big_sell_print_percentile`: the session mid-rank percentile of the largest print on each side in the aggression window. |
| 10 | Stacked imbalances: P1 computed them at a fixed ratio (3.0); P2 tunes the ratio (2.0) | The strategy computes stacks at the configured ratio from the aggression-window footprint (`stacked_imbalance_levels`). |
| 11 | `trade_safe` existed twice (P2 field, P1 flags) | A single source of truth: the `data_quality.trade_safe` flag, exposed read-only as a property. |
| 12 | Staleness: P1 latched it forever; P2 used `stale_ms` | Staleness is transient: `stale_ms` and `stale_data_flags` per decision. Integrity issues still latch. |
| 13 | Two parameters for one concept: `absorption_efficiency_threshold` (search space, unused) and `severe_opposing_absorption` (used, not searchable) | Unified to `severe_opposing_absorption`, now searchable over the old range [0.5, 0.95]. |
| 14 | Import roots named `contracts` and `fabio` (P2), plus a `src/` package (P1) | One package, `fabio_mechanical`. The repo root is the package, as requested, because a top-level `profile` module would shadow the stdlib. |
| 15 | Two CLIs | One `fabio` CLI (commands in `README.md`). |
| 16 | Python 3.10 vs 3.11 | `>=3.10`. |
| 17 | Configs (`default.yaml` vs `interpretation.yaml`) | Seven files, each strictly validated (section 4 of `ARCHITECTURE.md`). |
| 18 | Execution level: P2 filled against the snapshot book at decision time (latency was only a timestamp offset) and filled stops/targets exactly at price on a last-trade touch | An event-driven simulator on the live MBO book (section 5, item 18). |
| 19 | Dependencies | Removed `polars` (unused). Added `tzdata` (Windows zoneinfo). `databento` and `pyarrow` are optional extras. |
| 20 | Sample outputs/examples in obsolete schemas (P1 `examples/`, P2 `sample_reports/`, `BUILD_MANIFEST.json`) | Removed. Outputs are regenerated by `fabio self-check`, and one legacy P1 line is kept as a migration fixture. |
| 21 | Both packages hashed pydantic's JSON Schema output, which changes between pydantic releases (found in the clean-environment check: pydantic 2.9.2 and 2.13.5 gave different hashes for identical models) | The hash is computed from a library-independent contract descriptor: model/field names, normalized types, constraints, defaults, enum members (`contracts/schema.py`). It is identical across pydantic versions. The JSON Schema is still exported for tooling. |

## 4. Architecture decisions made during the merge
- **Snapshots are emitted only at packet boundaries that contain trades** (Databento `F_LAST`).
  Every snapshot therefore sees a consistent book, and decisions stay event-level. There are
  no candles.
- **The replay clock is `ts_recv`** when present, because Databento files are written in
  `ts_recv` order. Snapshot timestamps are therefore "when we knew it".
- **Session boundaries are injected automatically** at the CME trading-day change (18:00 ET),
  so Databento files need no synthetic boundary events.
- **The developing session profile is refreshed at most every `session_profile_refresh_ms`**
  (1000 ms) to keep throughput practical. The strategy does not read it for entries; it uses
  leg profiles and balance POCs.

## 5. Strategy/risk behaviour that changed, and why

Every item is a bug fix, a wiring of a parameter P2 declared but never used, or a
consistency guard needed once real multi-hour data flows through P2's FSMs. None adds an
indicator. Items 6 and 7 change entry conditions and matter most.

1. **FSM left `EXIT` → `BALANCED`.** P2 had no transition out of `EXIT`, so each machine
   could trade once per run.
2. **Setup age is measured from the setup start** (in structure periods). P2 counted every
   update since construction, which killed any multi-step setup after 40 updates.
3. **`ENTRY_READY` → `BALANCED` when risk or execution rejects.** P2 stayed stuck until expiry.
4. **Structural invalidation before entry.** If price trades through the stop level
   (LVN edge ± buffer, or the failed-auction extreme ± buffer) before entry, the setup resets.
   This is P2's invariant `stop_is_structural_invalidation` applied before the trade.
5. **A candidate needs a valid target in the trade direction and valid geometry**
   (stop < entry < target for longs). P2 could emit a long whose "target" was below entry.
   No target means no trade, because a fixed-RR target is excluded by the invariants.
6. **Trend aggression must be at location**: the aggression window must itself have traded
   inside the chosen LVN. P2's `WAIT_AGGRESSION` accepted aggression anywhere, at any later
   time, which contradicts "wait for aggression at location".
7. **Mean-reversion location semantics were aligned with trend.** The LVN retest must have
   happened, and the snap-back aggression must be at location (the aggression window traded
   inside the LVN). P2 required price to still sit inside the LVN on the very snapshot where
   snap-back aggression is measured. A snap-back moves price out of the LVN, and P1's
   absorption measure only clears once price has moved, so the two conditions could almost
   never both hold.
8. **LVN selection is direction-aware.** Longs choose LVNs at or below price; shorts at or
   above. P2 accepted `direction` but ignored it, and picked zones the pullback had already
   traded through, which invalidated the setup at once.
9. **`lvn_neighbor_ratio` is enforced** (zone volume / mean neighbouring volume ≤ 0.70). It was
   declared in P2's interpretation and search space but never read.
10. **`aggression_persistence` is enforced** as consecutive snapshot observations. The baseline
    value of 1 equals P2's behaviour.
11. **`pullback_depth_min/max` are enforced.** At the trend LVN touch, the retracement must lie
    in [0.15, 0.85] of the impulse, and `pullback_depth_min` also locks a leg. Both were
    declared and unused.
12. **Consistency guards.** Progress requires the excursion side to match the setup direction.
    A failed auction must be an attempt of at least `breakout_distance_ticks`. A setup is reset
    if the reference balance changes.
13. **The mean-reversion target is the POC of the balance that failed to break out,** captured
    at setup start. A developing balance otherwise absorbs the failed auction's own volume, and
    its POC drifts toward the entry.
14. **One trade per structural leg.** A traded impulse or reclaim leg id cannot start another
    entry; otherwise the machine re-armed on the same failed auction right after the exit.
15. **Risk.** `reset_day`/`reset_week` now actually run on CME trading-day and ISO-week changes;
    P2 never called them, so the 6-trade session cap and the daily loss lock became permanent
    after day one. The loss-streak lock resets daily (`loss_streak_reset: daily`, configurable)
    instead of halting forever. The cooldown is counted in structure periods. Exposure reserved
    for an entry that is not (fully) filled is released.
16. **Deterministic candidate ids** (a hash of config, setup, direction, time and event index)
    replace `uuid4`, so reruns are bit-identical.
17. **Positions are flattened** at `risk.flatten_time` (16:55 ET, before the CME halt), at end
    of data, on a contract roll, and on the kill switch. P2 left positions open and never used
    its own `SESSION_END` reason.
18. **Execution realism.** Orders arrive after `latency_ms` and match the book at arrival.
    Market orders sweep displayed depth inside a `max_slippage_ticks` protection band, plus P2's
    extra `default_slippage_ticks`, and the remainder is an IOC partial fill. Targets are passive
    limits that fill through MBO queue position or a trade-through. Stops trigger on a trade at or
    through the price and sweep the book, so a gap fills worse than the stop. OCO is
    server-side and atomic. A new entry guard, `target_already_reached`, stops an entry whose
    fill would land beyond its own target. Exit fees now include `fee_per_contract`; P2 charged
    exits commission only.
19. **Metrics.** P2 filled `trades_per_month`, `monthly_stability` and `regime_stability` with
    placeholders. They are now computed from timestamps and tags.
20. **Experimental flags.** `early_exit_enabled` became `experimental_early_exit_enabled` (OFF).
    The `experimental_trailing_enabled` flag was removed because no trailing implementation
    existed.

## 6. Package 1 measurements that changed, and why
- **Order book.** Sorted price levels with incremental sizes (O(log L) instead of scanning all
  orders on every event). `MODIFY` keeps priority only when price is unchanged and size does not
  increase (Databento semantics); P1 always re-queued. `MODIFY` to size 0 removes the order.
  `FILL` records attribute the following `CANCEL` to execution instead of "pulling".
  `BookSnapshot.ts_event_ns` is the time of the last book change.
- **Data quality.** Bounded duplicate window. `F_SNAPSHOT` records are exempt from ordering
  checks, and `CLEAR` restarts the snapshot requirement (Databento's daily 00:00 UTC re-seed
  would otherwise lock the whole day). Timestamp checks use the replay clock; `ts_event`
  regressions are a diagnostic counter. Crossed/locked checks run only at packet boundaries.
  Checksum-mismatch and invalid-record flags were added.
- **Profiles.** Untraded ticks inside the range count as zero volume for LVN/HVN detection.
  An LVN must be a valley with volume on both sides; profile-edge tails are excess, not LVNs.
  `retest_count` counts revisits, not prints inside the zone. Zone `age_events` and
  `persistence` are computed; P1 left them as constants.
- **Footprint.** The strategy-facing footprint covers the rolling aggression window (P1
  accumulated the whole session). Stacked imbalances must be on adjacent price levels.
- **Order flow / auction features.** Same definitions, computed incrementally (histogram
  percentiles, running sums, closed-form OLS). This was checked against direct computation in
  tests.
- **Sessions.** Added `LONDON` and the CME trading day (18:00 ET boundary, DST-safe).
- **Databento adapter.** Handles `Action`/`Side` enum objects, `UNDEF_PRICE`, snapshot
  completion, instrument-id → contract mapping through definitions, and roll detection. Prices
  are converted with exact integer arithmetic. Downloads require `--max-cost` (or `--yes`), and
  raw files are made read-only.

## 7. Remaining limitations (honest)
- **No real Databento data was used during the build.** No API key was available. The real
  DBN reading path was tested with DBN files encoded by `databento_dbn` itself, and all
  strategy tests use synthetic raw MBO. The first real run is what `FIRST_REAL_TEST.md` is for.
- **The structure definitions are interpretations** (balance window, periods, sampling, leg
  anchors). They are documented and deterministic, but not yet validated on real NQ. With P2's
  baseline thresholds, real days may produce few or no complete setups. Tuning belongs to the
  research phase, on development data only.
- **Throughput** is about 20–25k MBO events/s (see `TEST_REPORT.md`). A full NQ MBO day
  (millions of records) takes several minutes. It streams, so memory stays bounded.
- **Execution model limits.** The queue assumes FIFO and that our order joins behind all
  displayed size. Implied liquidity is absent from MBO. Our own fills have no market impact
  beyond the prices swept. Stops are native stop-market orders (no stop-limit protection band).
- **Calendar.** Holidays and early closes are not modelled: the flatten time is fixed and
  session windows are static. `news_day` needs an external calendar CSV (`--news-calendar`).
- **Schemas.** The backtest needs MBO. MBP-10/trades tools are kept for validation only.
- **Synthetic scenarios are illustrations.** They exercise every code path; the P&L on them
  says nothing about the real market.
