# Data quality

The independent quality engine detects:

- sequence gaps beyond configurable tolerance;
- out-of-order sequence numbers;
- timestamp regressions;
- duplicate events;
- update-before-snapshot initialization;
- unknown contract prefixes;
- crossed/locked impossible books;
- stale streams;
- manifest/checksum corruption.

Pydantic rejects negative event sizes at the contract boundary. Adapter code should validate provider fields before conversion.

Severe issues set `trade_safe=false`. Quality flags remain attached to every exported market snapshot so a downstream package cannot accidentally lose the safety state.
