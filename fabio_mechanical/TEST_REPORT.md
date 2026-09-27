# Test report

Final verification was run on a **clean copy** of the project in a **fresh virtual
environment**. It was repeated in the development environment.

| | Clean environment (release check) | Development environment |
|---|---|---|
| OS / Python | Windows 11, CPython 3.11.0 | same |
| Install | `pip install -e ".[all]"` into a new venv | editable |
| pydantic | 2.13.5 | 2.9.2 |
| databento / databento-dbn | 0.87.0 / 0.70.0 | not installed (DBN test skipped there) |
| pyarrow / pytest / ruff / mypy | 25.0.1 / 9.1.1 / 0.16.9 / 2.3.1 | 22.0.0 / 9.1.0 / 0.16.7 / 2.3.1 |

A second clean venv had a **non-editable** install **without extras** (no `databento`, no
`pyarrow`). From an unrelated working directory, `fabio schema hash`, `fabio config validate`
and `fabio backtest run --synthetic mean_reversion_short` all passed. This shows the configs and
contract pins ship inside the package and synthetic runs need no Databento dependency or key.

## Stage-15 checklist

| # | Check | Result |
|---|---|---|
| 1 | Install from a clean environment | PASS (editable with all extras; non-editable core-only) |
| 2 | Formatting / lint / type checks | PASS: `ruff format --check` 94 files formatted; `ruff check` all passed; `mypy` no issues in 65 source files (both environments) |
| 3 | Full pytest | PASS: **126 passed, 0 skipped** in 4 min 30 s (clean env). Dev env: 125 passed, 1 skipped (the DBN test needs `databento`). |
| 4 | Synthetic raw-MBO end-to-end | PASS: raw Databento-layout records → adapter → book → trades → Delta/CVD → profiles/LVN → structure → FSM → candidate → risk → fills → exit, for trend long/short and mean-reversion long/short (`tests/test_pipeline_e2e.py`). The same run from a real DBN file on disk gives an identical ledger. |
| 5 | Pure Fabio synthetic backtest | PASS: `fabio backtest run --input <DBN dir>` → 1 TREND long, exit TARGET at the prior balance POC, gross 2 720.00, fees 42.00, net 2 678.00, R 8.93, 5 fills (2 taker entry fills with 16 ticks of total entry slippage, 3 maker target fills), `trade_safe` true |
| 6 | Optimization smoke | PASS: `fabio research optimize --synthetic-days 3 … --mode grid --max-candidates 3` (58.6 s); `tests/test_research.py` covers reproducibility, all candidates retained, baseline first, plateau and sensitivity |
| 7 | Walk-forward smoke | PASS: `fabio research walk-forward --synthetic-days 3 …` (41.4 s), 2 windows, parameters frozen per window |
| 8 | Robustness smoke | PASS: `fabio research robustness --quick` (63 s) → 8 grid + 6 perturbation scenarios + tag breakdown |
| 9 | Monte Carlo smoke | PASS: `fabio research monte-carlo` (seeded, deterministic, both bootstrap modes) |
| 10 | AI disabled | PASS: `AI_ENABLED: false` enforced by config validation; `AIProviderStub` raises; `DecisionSnapshot.ai_enabled` is `Literal[False]`; no AI SDK imports (source scan); run summaries report `ai_enabled: false`, provider `MECHANICAL` |
| 11 | Optimizer cannot edit invariants | PASS: non-search keys and out-of-bounds values raise `FrozenInvariantError`; `invariant_hash()` unchanged after a search; the search space may not overlap invariants |
| 12 | `fabio --help` | PASS (and `--version`) |
| 13 | Documented first-real-test commands | PASS: every `fabio …` line in `FIRST_REAL_TEST.md`, `README.md`, `DATA_GUIDE.md` and `RESEARCH_PROTOCOL.md` is parsed by the CLI in `tests/test_cli.py`. The data commands were run end to end against a synthetic DBN + definitions pair (validate --deep, offline resolve, replay, features JSONL and Parquet, backtest, report). |
| 14 | No secrets | PASS: no `.env` in the tree; scans for Databento key patterns and `DATABENTO_API_KEY=` assignments found nothing |

## CLI verification (clean environment)

| Step | Exit (expected) | Seconds |
|---|---|---|
| `fabio --help` / `--version` | 0 (0) | 0.7 / 0.6 |
| `fabio schema hash` | 0 (0) | 0.9 |
| `fabio config validate` | 0 (0) | 1.1 |
| `fabio data cost …` without API key | 2 (2): `DATABENTO_API_KEY is not set`, no traceback | 0.8 |
| `fabio data download …` without `--max-cost` | 2 (2): refused | 0.8 |
| `fabio data synthetic --format dbn` | 0 (0) | 1.2 |
| `fabio data validate … --deep` | 0 (0): snapshot present, 0 `ts_recv` regressions, all instruments resolved | 1.9 |
| `fabio data resolve --definitions …` | 0 (0) | 1.7 |
| `fabio replay run` | 0 (0): `trade_safe` true, book initialized | 9.3 |
| `fabio features build` (JSONL / Parquet) | 0 / 0 | 13.1 / 9.3 |
| `fabio backtest run` | 0 (0) | 9.9 |
| `fabio report` | 0 (0) | 0.8 |
| `fabio research freeze-baseline / optimize / walk-forward` | 0 / 0 / 0 | 18.5 / 58.6 / 41.4 |
| `fabio research robustness --quick / ablation / monte-carlo` | 0 / 0 / 0 | 63.0 / 34.7 / 1.0 |
| `fabio research lock-oos / verify-oos` | 0 / 0 | 1.0 / 1.0 |
| `fabio research lock-oos` again on the same file (write-once) | 2 (2) | 1.0 |
| `fabio self-check` | 0 (0): all subsystems | 146.8 |
| `fabio bench --minutes 30` | 0 (0) | 31.6 |

## Test inventory (126 tests)

| File | Tests | Covers |
|---|---|---|
| `test_contracts.py` | 9 | version, pinned hash, library-independent descriptor, single schema definition, round trip, validators (order fields, candidate geometry, `ai_enabled`, crossed-book safety), legacy P1 migration |
| `test_config.py` | 7 | validation, hash determinism/sensitivity, AI rejected, experimental OFF, optimizer gate, search space vs invariants, labels/phases, alternate config dir |
| `test_orderbook.py` | 8 | add/cancel/modify/queue, Databento priority rules, fill vs pull attribution, DOM/microprice/queue-ahead/sweep, replenishment, contract roll, clear, MBP-10 |
| `test_orderflow_profile.py` | 13 | Delta/CVD, big prints, normalized CVD slope, delta z-score, absorption, footprint and stacks, rolling footprint, POC/VA, LVN valley rule, zero-volume ticks, revisits, composite, auction features, incremental = direct |
| `test_quality_session.py` | 9 | quality flags, Databento snapshot exemption, bounded dedupe, transient staleness, crossed-book lock, replay determinism/prefix/round trip, session reset, **NY/London DST**, CME trading-day bounds across DST |
| `test_structure.py` | 5 | balance detection, failed auction + reclaim + reclaim leg, accepted excursion, impulse lock/profile/out-of-balance/displacement, new value archive + roll reset |
| `test_fsm.py` | 21 | full trend/MR paths (long and short), deterministic ids, invalid transition, touch without aggression, unsafe/stale, absorption block, sessions, location, persistence, no target, invalidation, expiry, EXIT lifecycle + leg guard, rejection reset, balance change, MR target, direction-aware LVN, **no stop widening**, **AI disabled** + provider contract |
| `test_risk_accounting.py` | 6 | sizing, duplicate guard, **all risk locks**, caps, day/week resets and exposure release, NQ/MNQ P&L and fees and R, exact partial exits |
| `test_execution.py` | 7 | **latency against the book at arrival**, depth sweep + **partial fill** + protection band, gap/unsafe/target guards, **MBO queue position** + trade-through, stop trigger + **gap-through slippage** + no widening, fees + OCO resize, live adapters disabled |
| `test_pipeline_e2e.py` | 7 | raw MBO → exit for four setups, no trade without aggression, costs/latency/audit trail, **DBN file ingestion = in-memory** |
| `test_no_lookahead_restart.py` | 4 | **replay determinism**, **end-to-end no-lookahead (prefix invariance)**, out-of-order → `LookaheadError`, **restart from a pickled checkpoint** |
| `test_locks_integration.py` | 8 | **corrupted data lock**, checksum lock, **stale data lock**, **roll handling** + flatten on roll, **risk lock**/kill switch, session-end flatten, phase filter |
| `test_research.py` | 7 | reproducible candidates, optimizer completeness + **invariant protection**, walk-forward, robustness + Monte Carlo + ablation, OOS lock, baseline freeze, **optimizer cannot see OOS** |
| `test_cli.py` | 9 | help/version, documented commands parse, schema/config, clean failures without a key/cap, synthetic → replay → features → backtest → report → Monte Carlo, research freeze/optimize |
| `test_hygiene_performance.py` | 6 | no AI / no live-order code, no excluded indicators, no secrets, **bounded memory while streaming**, lazy sources, benchmark |

## Performance (`fabio bench --minutes 30`)

Synthetic MBO with 14.5% trade records (86 183 events, 12 494 trade packets):

| Metric | Clean env (pydantic 2.13.5) | Dev env (pydantic 2.9.2) |
|---|---|---|
| Replay | 18 791 events/s (2 724 snapshots/s) | 25 089 events/s (3 637 snapshots/s) |
| Full backtest | 15 769 events/s | 21 073 events/s |
| Peak traced replay state | 15.8 MB | 15.8 MB |

Replay memory stays bounded: 3× more data needs less than 2× the peak memory (asserted in the
tests), because nothing loads a whole dataset. Real NQ days have a lower trade share, so
events/s is typically higher. Estimate a day's run time as `record_count` (from
`fabio data cost`) divided by the backtest events/s.

## Original packages (audit baseline)

Run in place before the merge: Package 1, 28/28 passed; Package 2, 42/42 passed. Each worked in
isolation. They could not be connected (see `INTEGRATION_REPORT.md` §1).

## Not verified here

- **Real Databento downloads.** No API key was available in the build environment. The
  cost/download code paths are covered by fakes of the Databento client (cost, batch resume,
  checksums, manifests). The DBN reading path was exercised with real DBN files produced by
  `databento_dbn`.
- **Anything live.** There is no live path by design.
