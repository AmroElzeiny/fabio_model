# DATA_DOWNLOAD_GUIDE — spending the $125 historical credits efficiently

The package never downloads before calling Databento metadata cost endpoints. Start with narrow requests and expand only after verifying the expected records and feature output.

## Recommended sequence

1. **Definitions + symbology first.** Resolve `NQ.v.0` / `MNQ.v.0` to real instrument IDs and download contract definitions for the target dates. Continuous symbols are used only to pick the actual active contract; prices remain original/unadjusted.
2. **Cheap validation slice.** Pull `trades` for one or two representative sessions, then `mbp-10` for the same slice. Verify session filters, price/tick conversion, aggressor side, POC/VAH/VAL, and L2 metrics.
3. **MBO pilot.** Estimate one NY session of `mbo`. Download only after the cost fits the intended budget. Run replay + quality checks before scaling.
4. **Scale day-by-day with batch.** For multi-day MBO, use `--batch --split-duration day`. The resume state prevents attaching a stale job to a changed request and downloaded files are SHA256-checked against Databento batch metadata when available.
5. **Spend by research priority.** Prefer NQ first, NY sessions and representative volatility regimes. Add MNQ only when the NQ pipeline is verified. Do not duplicate the same raw slice in multiple encodings.
6. **Keep raw immutable.** Recompute features from raw data; do not repeatedly pay for the same data because a derived-feature threshold changed.

## Commands

```bash
fabio-replay cost-estimate --symbol NQ.v.0 --schema trades --start 2026-01-05 --end 2026-01-06
fabio-replay cost-estimate --symbol NQ.v.0 --schema mbp-10 --start 2026-01-05 --end 2026-01-06
fabio-replay cost-estimate --symbol NQ.v.0 --schema mbo --start 2026-01-05 --end 2026-01-06

fabio-replay download --batch --symbol NQ.v.0 --schema mbo \
  --start 2026-01-05 --end 2026-01-10 \
  --output data/raw/nq_mbo --state-file data/raw/nq_mbo.batch.json
```

## Budget guardrail

Before each request, record `estimated_cost_usd`, `billable_bytes`, and `record_count`. Keep a simple running total against the $125 credit. The CLI prints the pre-download estimate. Databento notes that `get_cost` can be less exact for ranges that are not discrete 10-minute multiples, and definition-schema estimates are most accurate on 24-hour multiples; actual billing follows bytes delivered.

## Sources

- https://databento.com/docs/reference-historical/basics/ — `get_cost`, `get_billable_size`, `get_record_count`.
- https://databento.com/docs/standards-and-conventions/symbology — continuous futures symbology and unadjusted prices.
- https://databento.com/docs/api-reference-historical/basics/encodings — batch/streaming behavior and large-request guidance.
