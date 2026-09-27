# INTEGRATION_CONTRACT

## Version

`CONTRACT_VERSION = FABIO_MECHANICAL_V1`

Run:

```bash
fabio-replay schema-hash
```

A consumer must pin both the version and canonical schema SHA256. Any schema change requires a hash change; breaking changes require a new contract version.

## Primary output

`MarketFeatureSnapshot` is the only required strategy-facing payload. It contains:

- UTC nanosecond timestamp, instrument and active real contract.
- session label, best bid/ask and last-trade tick prices.
- price/structure context and auction-state feature snapshot.
- volume profiles and relevant LVN/HVN zones / POC candidates.
- Delta, CVD, rolling Delta, CVD slope/acceleration.
- footprint, aggression, absorption, big-print features.
- DOM/depth, pulling/stacking and displayed replenishment evidence.
- data-quality and stale-stream flags.

All price fields intended for exact arithmetic are integer ticks. Floating values are derived ratios/scores only.

## No trading decision

`DecisionSnapshot` is an integration placeholder with `decision="UNSET"`; Package 1 contains no entry, exit, stop, target, sizing, AI, broker, or live-order logic.

## Data-quality contract

A severe replay/data inconsistency sets `data_quality.trade_safe=false`. A downstream strategy must treat that flag as a hard data-safety lock. The package does not claim that `trade_safe=true` implies profitable or tradable conditions; it means only that the implemented data-integrity checks have not found a severe issue.

## Iceberg wording

The output exposes only **evidence scores** based on displayed replenishment and repeated execution behavior. It never claims hidden liquidity is known with certainty.
