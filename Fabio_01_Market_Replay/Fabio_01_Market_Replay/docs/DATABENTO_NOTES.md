# Databento integration notes

- Dataset: `GLBX.MDP3`.
- Preferred order-book schema: `mbo` (L3/full order book).
- Validation/fallback schemas: `mbp-10` (L2) and `trades`.
- Definitions and symbology resolution are supported by the downloader.
- `NQ.v.0` / `MNQ.v.0` are volume-ranked continuous selectors used to map to a real contract. They are not used to fabricate a back-adjusted price series.
- MBO `side` on `Trade` is interpreted as aggressor side using provider semantics. Quote-based inference is only used when provider side is missing, and `initiator_source` reports `quote_fallback`.
- Databento MBO `Fill` is treated as non-book-mutating in accordance with the action definition; displayed book quantity changes must come from the appropriate book-changing records.
- Databento raw price units (1e-9) are converted to integer ticks with `Decimal` arithmetic.

Sources:
- https://databento.com/docs/schemas-and-data-formats/mbo
- https://databento.com/docs/schemas-and-data-formats/mbp-10
- https://databento.com/docs/standards-and-conventions/common-fields-enums-types
- https://databento.com/docs/standards-and-conventions/symbology
