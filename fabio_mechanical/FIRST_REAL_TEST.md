# First real NQ test

Goal: run the Pure Fabio mechanical baseline on **one** real day of CME NQ MBO data, then
check every stage (raw data, book, features, state machine, orders, costs) before you spend
money on more data. Do the steps in order. Do not start with a large date range.

The commands are single-line so they paste the same way into PowerShell and bash. Run them
from the project root (the `fabio_mechanical/` folder).

## 0. Install and self-test (no API key needed)

```bash
python -m venv .venv
```

Activate it: `.venv\Scripts\activate` on Windows, or `source .venv/bin/activate` on macOS/Linux. Then:

```bash
pip install -e ".[all]"
fabio config validate
fabio schema hash
pytest -q
fabio self-check --output-dir runs/self-check
```

`fabio schema hash` must print the pinned `CONTRACT_SCHEMA_SHA256`, and `pytest` must be
green before you touch real data.

Copy `.env.example` to `.env` and set `DATABENTO_API_KEY=...`. `.env` is git-ignored and is
read automatically by `fabio`.

## 1. Pick a short window

Use **one full UTC day** on a normal weekday (no US holiday), from `00:00` to `24:00 UTC`.
Databento publishes the GLBX.MDP3 order-book snapshot at **00:00 UTC** each day. A window
that starts later has no snapshot, so the book cannot be rebuilt: `missing_snapshot` locks
`trade_safe=false` for the whole run.

The examples below use Tuesday 2026-09-22. Replace the date everywhere with the day you choose.

## 2. Ask for the cost first (nothing is downloaded)

```bash
fabio data cost --symbol NQ.v.0 --schema mbo definition --start 2026-09-22T00:00 --end 2026-09-23T00:00
```

Write down `estimated_cost_usd`, `billable_bytes` and `record_count`. `record_count` also
tells you how long the replay will take: divide it by the events/second in `TEST_REPORT.md`
(roughly 20k/s).

## 3. Download MBO and definitions (with a spending cap)

```bash
fabio data download --symbol NQ.v.0 --schema definition --start 2026-09-22T00:00 --end 2026-09-23T00:00 --output data/raw/nq/glbx-mdp3-20260922.definition.dbn.zst --max-cost 1
fabio data download --symbol NQ.v.0 --schema mbo --start 2026-09-22T00:00 --end 2026-09-23T00:00 --output data/raw/nq/glbx-mdp3-20260922.mbo.dbn.zst --max-cost 10
```

- The cost is printed again before each download. If the estimate is above `--max-cost`,
  nothing is downloaded.
- An existing file is never overwritten. Each file gets a `*.manifest.json` (SHA256, bytes,
  cost estimate) and is made read-only.
- Keep the word `definition` in the definitions file name and keep both files in the same
  folder. That is how `fabio` finds the definitions automatically.

## 4. Validate the raw data

```bash
fabio data validate data/raw/nq/glbx-mdp3-20260922.mbo.dbn.zst data/raw/nq/glbx-mdp3-20260922.definition.dbn.zst --deep
```

Expect `"ok": true`, with:
- `checksum_manifest_valid: true` (the file is unchanged since download);
- `starts_with_snapshot: true` and `snapshot_records > 0` (the book can be seeded);
- `ts_recv_regressions: 0`;
- `unresolved_instrument_ids: []` (every instrument has a definition).

## 5. Resolve the active NQ contract

```bash
fabio data resolve --symbol NQ.v.0 --start 2026-09-22 --end 2026-09-23
fabio data resolve --definitions data/raw/nq/glbx-mdp3-20260922.definition.dbn.zst
```

The first command asks Databento which real contract `NQ.v.0` maps to. The second lists what
your definitions file contains. They must agree (for example `NQZ6`, tick size `0.25`).
Prices are never back-adjusted.

## 6. Replay event by event

```bash
fabio replay run --input data/raw/nq/glbx-mdp3-20260922.mbo.dbn.zst
```

Check:
- `final_trade_safe: true` and `data_quality_reasons: []`;
- `book_initialized: true`;
- `contract_rolls: 0` for a single day;
- `last_snapshot.balance` is filled in once 30 minutes of trading have been replayed;
- `events_per_second` (it tells you how long larger ranges will take).

The command exits with status 1 when `trade_safe` ended `false`. If that happens, stop and
look at the reasons before going on.

## 7. Build the MarketFeatureSnapshots

```bash
fabio features build --input data/raw/nq/glbx-mdp3-20260922.mbo.dbn.zst --output runs/first/features.jsonl --sample-ms 1000 --compact
```

This writes at most one snapshot per second (the first after each second boundary). Use it
to inspect the order-flow, profile and auction fields the strategy sees: `auction.balance_*`,
`auction.breakout_*`, `auction.impulse_*`, `impulse_profile.lvn_zones`, `delta_zscore`,
`cvd_slope_normalized`, `big_*_print_percentile`, `absorption_score` and `data_quality`.
`--compact` drops the per-price volume maps to keep the file small.

## 8. Run the Pure Fabio baseline

```bash
fabio backtest run --input data/raw/nq/glbx-mdp3-20260922.mbo.dbn.zst --output-dir runs/first/backtest
```

This is the default configuration: frozen invariants, P2's interpretation values, AI off, no
experimental extensions.

## 9–11. Read the outputs

Everything is in `runs/first/backtest/`:

| File | What to check |
|---|---|
| `state_transitions.csv` | every Trend/Mean-Reversion transition with time (UTC), price and reason, e.g. `VALID_BREAKOUT … WAIT_AGGRESSION → ENTRY_READY`, `setup_expired`, `structural_invalidation_before_entry` |
| `candidates.csv` | every entry candidate and its outcome (`order_submitted`, `risk_reject:<lock>`, …) |
| `trades.csv` | entry/exit prices, stop, target, quantity, gross, fees, net, R, MFE/MAE, exit reason(s), entry/exit slippage in ticks |
| `fills.csv` | every fill: price, quantity, fee, slippage ticks, MAKER/TAKER |
| `journal.jsonl` | full audit trail: `CANDIDATE` (with all evidence: LVN, aggression checks, decision snapshot hash), `DECISION`, `RISK`, `ORDER_SUBMIT`, `ORDER_REPORT`, `FILL`, `POSITION_OPEN/CLOSE`, `DATA_QUALITY`, `STATE_TRANSITION` |
| `summary.json` / `metrics.json` | P&L, fees, slippage totals, maker/taker counts, drawdown, metrics |
| `manifest.json` | config hash, invariants hash, schema hash, code identity, dataset SHA256 |
| `REPORT.md` | readable summary (rebuild it with `fabio report`) |

```bash
fabio report --run-dir runs/first/backtest
```

**Zero trades is a valid outcome for one day.** The baseline is strict: balance, then breakout
with displacement, structure break, locked impulse, a qualifying LVN, the retest, and
aggression at the LVN. Use `state_transitions.csv` to see how far each setup got and why it
stopped. That is the point of the first test.

## 12. Check no-lookahead and data quality

- In `journal.jsonl`, every `FILL` of an entry has `timestamp_ns >=` its `CANDIDATE`
  `timestamp_ns + latency_ms`. Fills come from the book at arrival time, never from a later
  "close".
- `summary.json` shows `trade_safe_at_end: true` and empty `data_quality_reasons`. Any
  `DATA_QUALITY` record in the journal marks the exact time trading was locked.
- `ts_event_regressions` is informational (exchange timestamps); ordering uses `ts_recv`.
- The no-lookahead, determinism and restart tests (`tests/test_no_lookahead_restart.py`)
  passed in step 0. Rerun the backtest: `trades.csv` and `state_transitions.csv` must be
  byte-identical.

## 13. Only then extend the range

Get the cost of a week first, then download it as daily batch files:

```bash
fabio data cost --symbol NQ.v.0 --schema mbo definition --start 2026-09-14T00:00 --end 2026-09-19T00:00
fabio data download --batch --symbol NQ.v.0 --schema definition --start 2026-09-14T00:00 --end 2026-09-19T00:00 --output data/raw/nq_week --max-cost 2
fabio data download --batch --symbol NQ.v.0 --schema mbo --start 2026-09-14T00:00 --end 2026-09-19T00:00 --output data/raw/nq_week --max-cost 40
fabio backtest run --input data/raw/nq_week --output-dir runs/week1/backtest
```

Before any optimization, set the development/validation/OOS dates in `configs/research.yaml`
and follow `RESEARCH_PROTOCOL.md`: freeze the baseline first, optimize only on development
days, and lock OOS before opening it.
