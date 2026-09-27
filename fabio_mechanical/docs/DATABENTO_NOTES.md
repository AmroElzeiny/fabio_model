# Databento notes (GLBX.MDP3)

- Dataset `GLBX.MDP3`, order-book schema `mbo` (L3); `definition` is needed to map
  `instrument_id` → raw symbol/tick size; `mbp-10` / `trades` are validation schemas.
- `NQ.v.0` / `MNQ.v.0` (stype `continuous`) choose the most-active real contract per day. Prices
  are never back-adjusted.
- Historical MBO for GLBX.MDP3 includes a daily order-book snapshot at 00:00 UTC. It is preceded
  by a clear record (`action=R`), and all its records carry `F_SNAPSHOT`.
- `T`/`F` records do not change the book. The `C`/`M` records that follow a fill do. For trades,
  `side` is the aggressor side.
- `F_LAST` marks the last record of an event for the instrument; the book is consistent only
  there.
- Files are ordered by `ts_recv`. Prices are int64 in 1e-9 units, and `UNDEF_PRICE = 2^63 - 1`.
- The Python client exposes `action`/`side` as `databento_dbn.Action`/`Side` enums whose
  `.value` is the one-character code. The adapter accepts enums, strings or ints.
- `metadata.get_cost` / `get_billable_size` / `get_record_count` are called before every
  download. Databento documents that estimates for ranges that are not 10-minute (MBO) or whole-day
  (definition) multiples are approximate; billing follows the delivered bytes.

References:
- https://databento.com/docs/schemas-and-data-formats/mbo
- https://databento.com/blog/improvements-mbo-publishing
- https://databento.com/docs/standards-and-conventions/mbo-snapshot
- https://databento.com/docs/standards-and-conventions/symbology
- https://databento.com/docs/api-reference-historical/basics
