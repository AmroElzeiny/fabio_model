# Synthetic raw-MBO scenarios

`ingest/synthetic.py` generates Databento-shaped MBO records. Matching is FIFO:
`T` (aggressor) → `F` + `C` per filled resting order. Packets are `F_LAST`-terminated, and each
segment is seeded with `R` + `F_SNAPSHOT` records. The records go through the real
`DatabentoMBOAdapter`, so every test starts from raw data. Scenarios are seeded and
deterministic. Their P&L says nothing about real markets.

| Scenario | Designed sequence | Expected outcome (asserted in tests) |
|---|---|---|
| `trend_long` | prior-session balance A (80240) → overnight balance B (80160) → RTH impulse out of B with a thin zone → pullback into it → buy burst → run to A's POC | one TREND long, full trend state path, exit TARGET at 80240 |
| `trend_short` | mirror | one TREND short, exit TARGET at the prior POC below |
| `trend_long_no_aggression` | same structure; the LVN retest meets two-sided flow and then fails | no candidate (trend reaches `WAIT_AGGRESSION` and resets) |
| `mean_reversion_short` | overnight balance (MR not allowed) → London failed upside auction → fast reclaim with a thin zone inside value → pullback → sell burst | one MEAN_REVERSION short, full MR path, exit TARGET at the balance POC |
| `mean_reversion_long` | mirror | one MEAN_REVERSION long |
| `quiet_balance` | one hour of balance | no candidate |
| `roll` | NQH6 day, then NQM6 (new `instrument_id`, new snapshot) | one `CONTRACT_ROLL`, book/structure reset, `trade_safe` stays true |

`fabio data synthetic --scenario <name> --output x.jsonl` writes events. With `--format dbn` it
writes a real DBN file plus a definitions DBN file (needs the `databento` extra).
