# FABIO_SPEC

## Mechanical baseline

The system implements the public playbook as a strict three-stage decision sequence: **Market State -> Location -> Aggression**.

### Trend

1. Confirm out-of-balance directional movement.
2. Require breakout/displacement and a structure-breaking impulse.
3. Lock the impulse leg and use its Volume Profile.
4. Identify a qualifying LVN.
5. Wait for retracement into that LVN.
6. Require same-direction aggression; a touch alone is never an entry.
7. Stop is beyond the relevant aggression/LVN invalidation with a tick buffer.
8. Baseline target is the relevant balance POC supplied by market context.

### Mean reversion

1. Start from balance/consolidation.
2. Observe a breakout attempt.
3. Require lack of acceptance / failed auction.
4. Require reclaim back inside value.
5. Lock the reclaim leg and profile it.
6. Wait for LVN retest.
7. Require snap-back aggression in the return-to-value direction.
8. Stop is beyond the failed-auction extreme plus buffer.
9. Baseline target is the reclaimed balance POC.

### Aggression evidence

Mechanical aggregation can use signed Delta z-score, CVD slope, big-print percentile and/or stacked footprint imbalance. Severe opposing absorption blocks the entry. Exact numeric definitions are tunable interpretations, not claimed as Fabio-authored thresholds.

### Baseline exclusions

No RSI, MACD, EMA filter, ICT concepts, ML, discretionary predictor, blind LVN limit entry, arbitrary fixed-RR target, martingale, averaging-down or stop widening.
