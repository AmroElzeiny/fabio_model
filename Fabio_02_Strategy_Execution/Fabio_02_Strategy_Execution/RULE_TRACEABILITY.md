# RULE_TRACEABILITY

## Fabio documented rules

Derived from the reviewed ChartFanatics Fabio Valentini playbook:

| Rule | Implementation |
|---|---|
| Read market state before trading | `TrendMachine`, `MeanReversionMachine` |
| Use Volume Profile location including LVNs/POC | `choose_lvn`, target engine |
| Wait for aggression at location; no aggression = no trade | `directional_aggression_ok` |
| Trend model is for out-of-balance continuation | `TrendMachine` |
| Trend uses the impulse that broke structure and profiles that leg | Trend states through `IMPULSE_PROFILE_READY` |
| Mean reversion requires failed breakout/reclaim before the LVN pullback | `MeanReversionMachine` |
| POC is the central target concept | `target_for` |
| Stop is near logical invalidation/aggressive print; never widen | `stop_for`, `validate_stop_update` |
| Small fractional account risk | `RiskEngine` |

Source: https://www.chartfanatics.com/strategies/auction-market-strategy

## Quantitative interpretations

These concepts are necessary to turn descriptive trading language into deterministic code, but their exact numbers are **not claimed to be Fabio's published values**:

- value-overlap threshold for balance;
- POC migration limit;
- directional efficiency;
- acceptance event count;
- minimum breakout distance and displacement z-score;
- minimum impulse length and pullback depth;
- formal LVN quality/age/retest rules;
- Delta/CVD/stacked-imbalance/big-print thresholds;
- absorption and rejection-speed thresholds;
- setup expiry and stale-data thresholds;
- session allowlists used by this mechanical implementation.

Every searchable interpretation is defined with units, bounded range and rationale in `configs/search_space.yaml`.

## Experimental extensions

All are disabled in baseline:

- `research.early_exit_enabled: false`
- `research.experimental_trailing_enabled: false`

No experimental feature is permitted to alter frozen invariants or hard risk controls.
