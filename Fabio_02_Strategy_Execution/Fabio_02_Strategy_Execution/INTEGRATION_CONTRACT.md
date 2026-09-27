# INTEGRATION_CONTRACT

`CONTRACT_VERSION = FABIO_MECHANICAL_V1`

## Main input

`MarketFeatureSnapshot` is the sole strategy input. It carries timestamp/instrument/session, structure and auction state, balance/POC/VAH/VAL, LVN/HVN zones, impulse/reclaim profiles, Delta/CVD, footprint features, aggression, absorption, big-print evidence, DOM/depth/pulling-stacking information and data-quality safety fields.

## Output path

`MarketFeatureSnapshot -> FSM -> TradeCandidate -> hard RiskEngine -> ExecutionIntent -> ExecutionAdapter -> ExecutionReport -> Portfolio/ledger`

## Schema hash

Run:

```bash
python -m fabio.cli schema-hash
```

The package also exports the complete JSON Schema in `sample_reports/contract_schema.json` during self-check.

## Future AI boundary

`DecisionProvider` defines the future extension seam. In V1 only `MechanicalDecisionProvider` is enabled. `AIProviderStub` raises immediately. A future AI layer may only veto/comment/context-score; it must not size risk, widen stops, bypass locks, submit orders or change the kill switch.

## Live execution

`MT5ExecutionAdapter` and `FuturesExecutionAdapter` are intentionally non-operational stubs. This package cannot place a live order.
