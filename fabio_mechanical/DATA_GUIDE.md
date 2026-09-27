# Data guide

## Sources and schemas

| Item | Value |
|---|---|
| Dataset | Databento `GLBX.MDP3` (CME Globex MDP 3.0) |
| Symbol | `NQ.v.0` (continuous, volume-ranked front NQ) or `MNQ.v.0`, with `stype_in=continuous` |
| Order-book schema used by the backtest | `mbo` (L3, every order add/cancel/modify/trade/fill) |
| Required companion | `definition` for the same dates (maps `instrument_id` → real contract such as `NQZ6`, plus tick size) |
| Validation schemas (optional) | `mbp-10`, `trades` |

Continuous symbols are used **only to choose the real contract** for each day. Prices are the
contract's own prices and are never back-adjusted. When the mapped `instrument_id` changes
between records (a roll), the adapter emits a `CONTRACT_ROLL` event. The book, flow, profiles
and structure reset, and any open position is flattened first.

## How the MBO feed is interpreted

- `T` (trade) and `F` (fill) records never change the book. The `C`/`M` records that follow
  a fill do. The `side` of a `T` record is the **aggressor** side (`B` = buyer lifted the ask).
- `M` (modify) keeps queue priority only when the price is unchanged and the size does not
  increase. `M` to size 0 removes the order.
- `R` (clear) empties the book. Records flagged `F_SNAPSHOT` rebuild it. Databento publishes a
  clear plus a snapshot at **00:00 UTC every day** for historical GLBX.MDP3 data. The adapter
  emits `SNAPSHOT(snapshot_complete=True)` after the last snapshot record.
- The book is only consistent at the end of a packet (`F_LAST`). Crossed-book checks and
  strategy snapshots happen only there.
- The replay clock is `ts_recv` (the order Databento files are written in). `ts_event`
  regressions are counted as a diagnostic.
- Prices are int64 in 1e-9 units. They are converted to integer ticks with exact integer
  rounding; `UNDEF_PRICE` becomes "no price".

## Spending the credits carefully

1. `fabio data cost` first, every time. It prints `estimated_cost_usd`, `billable_bytes` and
   `record_count`. Databento notes that estimates for ranges that are not 10-minute multiples,
   and definition estimates that are not whole days, are approximate; billing follows the bytes
   delivered.
2. Start with **one full UTC day** (`00:00` → next day `00:00`) of `mbo` + `definition`. See
   `FIRST_REAL_TEST.md`.
3. Always pass `--max-cost`. The download is refused when the estimate exceeds it.
4. Scale day by day with `--batch --split-duration day`. The resume state
   (`<output>/.batch_resume_<schema>.json`) refuses to attach an old job to a changed request.
   Downloaded files are SHA256-checked against Databento's batch metadata when it is supplied.
5. Keep raw data immutable. Features, backtests and optimizations are recomputed from raw
   files. Never pay twice for the same slice because a threshold changed.
6. Keep a running total of `estimated_cost_usd` against your credit.

```bash
fabio data cost --symbol NQ.v.0 --schema mbo definition --start 2026-09-22T00:00 --end 2026-09-23T00:00
fabio data download --symbol NQ.v.0 --schema mbo --start 2026-09-22T00:00 --end 2026-09-23T00:00 --output data/raw/nq/glbx-mdp3-20260922.mbo.dbn.zst --max-cost 10
fabio data download --batch --symbol NQ.v.0 --schema mbo --start 2026-09-14T00:00 --end 2026-09-19T00:00 --output data/raw/nq_week --max-cost 40
fabio data validate data/raw/nq_week --deep
```

## File conventions

- Raw files live under `data/raw/` and are git-ignored. Each stream download gets a
  `<file>.manifest.json` (cost estimate, SHA256, bytes) and is set read-only. Existing files
  are never overwritten.
- The definitions file must contain `definition` in its name. It is found automatically next
  to the MBO file, or anywhere among the `--input` paths, or you can pass it with `--definitions`.
- Directory inputs are searched recursively (Databento batch jobs create sub-folders). Files
  are ordered by name, so the dates in Databento names give chronological order. Day labels
  used by the research phase guards are parsed from `YYYYMMDD` / `YYYY-MM-DD` in the file names.
- Every run records a dataset manifest (per-file SHA256, bytes and a combined hash) in its
  `manifest.json`.

## Data quality (hard lock)

`quality/engine.py` sets `trade_safe=false` for the rest of the run on any of these:

| Flag | Meaning |
|---|---|
| `missing_snapshot` | book updates before a complete snapshot (usually a download that did not start at 00:00 UTC) |
| `crossed_book` / `impossible_book` | best bid >= best ask at a packet boundary, or a provider record that could not be converted |
| `timestamp_regression` | the replay clock went backwards |
| `out_of_order` / `gaps` | sequence regression / implausible sequence gap (snapshot records exempt) |
| `duplicated_event` | an identical record inside the dedupe window |
| `unknown_contract` | a contract outside `known_contract_prefixes` |
| `checksum_mismatch` | the file differs from its download manifest |

While `trade_safe` is false, the strategy takes no decisions, the risk engine rejects every
candidate (`data_quality_lock`), and the simulator rejects new entries (exits still execute).
Staleness is transient: `stale_ms` greater than `stale_data_ms_max` blocks decisions at that
moment only. `trade_safe=true` means only that the implemented integrity checks found no severe
issue. It says nothing about whether the market is tradable or profitable.

## Macro-event calendar (optional)

`--news-calendar calendar.csv` (columns `event,timestamp_utc,source,notes`) tags the matching
CME trading days as `news_day=true`. The tag feeds research breakdowns only; the baseline
strategy never reads it.
