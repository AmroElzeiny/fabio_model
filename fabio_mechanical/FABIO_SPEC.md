# FABIO_SPEC: mechanical baseline

The system implements the public Fabio Valentini auction-market playbook as a strict
three-stage sequence: **Market State → Location → Aggression**. There are two separate
models. Nothing else can create an entry.

Source reviewed by the original packages: <https://www.chartfanatics.com/strategies/auction-market-strategy>
(and the referenced video's page). Only documented directional concepts are treated as Fabio
rules. Every number is a configurable quantitative interpretation (`RULE_TRACEABILITY.md`).

## Market state

- **Balance:** a value area whose two half-window profiles overlap and whose POC is stable,
  with low directional efficiency.
- **Out of balance:** price trading beyond the balance value edge after an efficient impulse.

## Trend model (out-of-balance continuation)

1. **Balance.** A reference balance (value area, POC, range) exists.
2. **Breakout.** Price leaves value by at least `breakout_distance_ticks`, and the move is out of
   balance.
3. **Displacement.** The one-period move in the breakout direction has a z-score of at least
   `displacement_zscore_min`.
4. **Structure break.** The excursion extreme is beyond the balance range extreme.
5. **Impulse profile.** The impulse leg (from the last trade on the far side of the balance POC
   to the extreme) locks once price retraces at least `pullback_depth_min` of it. Its volume
   profile is frozen at that moment.
6. **Location.** A qualifying LVN of the impulse profile is chosen: nearest to price, on the
   retracement side, and passing the volume, neighbour, width, age and retest limits.
7. **Retracement into the LVN.** Price trades inside the LVN while still out of balance, with
   the pullback depth in [`pullback_depth_min`, `pullback_depth_max`].
8. **Aggression at location.** Directional aggression in the trade direction, measured over the
   aggression window, which must itself have traded inside the LVN. It requires signed delta
   z-score, normalized CVD slope, a big print or stacked footprint imbalance, and no severe
   opposing absorption, on `aggression_persistence` consecutive observations. **An LVN touch
   alone is never an entry.**
9. **Entry.** A market order, arriving after latency, inside the protection band.
10. **Stop.** Beyond the LVN (structural invalidation) plus `stop_buffer_ticks`. It is never
    widened.
11. **Target.** The relevant balance POC: the nearest known balance POC beyond the entry in the
    trade direction (completed intraday balances and prior sessions). No POC means no trade.

## Mean-reversion model (failed auction back to value)

1. **Balance.** A reference balance exists, and its POC is recorded when the setup starts.
2. **Breakout attempt.** Price leaves value by at least `breakout_distance_ticks`.
3. **No acceptance.** Fewer than `acceptance_event_count` structure periods closed beyond the
   edge.
4. **Failed auction.** Price returns inside value without acceptance.
5. **Reclaim.** Price trades inside value again.
6. **Reclaim profile.** The leg from the failed-auction extreme back into value locks after a
   `pullback_depth_min` retracement. Its profile is frozen.
7. **LVN retest.** Price trades inside a qualifying LVN of the reclaim profile, on the retest
   side.
8. **Snap-back aggression.** Aggression toward value at location, with a rejection speed (reclaim
   velocity / attempt velocity) of at least `rejection_speed_min`.
9. **Entry.** A market order.
10. **Stop.** Beyond the failed-auction extreme plus `stop_buffer_ticks`.
11. **Target.** The POC of the balance whose breakout failed.

## Common rules

- **Data safety.** No decision on unsafe or stale data; risk and execution reject too.
- **Sessions.** Trend trades `NY_RTH`; mean reversion trades `LONDON` and `NY_RTH`
  (configurable, validated).
- **Setup lifecycle.**
  - A setup expires after `setup_expiry_events` structure periods from its start.
  - It resets if price trades through its invalidation level before entry, or if the
    reference balance changes.
  - One trade per structural leg.
- **Position management.**
  - Bracket: stop-market plus limit target, server-side OCO.
  - Flatten at `risk.flatten_time` (16:55 ET), at end of data, on a contract roll, and on the
    kill switch.
  - No trailing, scaling in, averaging down or martingale.

## Hard risk (below the decision layer)

- **Sizing.** Risk fraction `risk_fraction` (capped by `max_risk_fraction`). Contracts =
  floor(risk ÷ (stop ticks × tick value)), capped by `max_position_contracts`.
- **Locks.** Kill switch, execution error, data quality, stale data, duplicate candidate, daily
  loss, weekly loss, loss streak, session trade cap, cooldown, concurrent exposure.

## Baseline exclusions

No RSI, MACD, EMA/SMA filters, Bollinger, stochastics, ICT concepts, ML or any other
discretionary predictor. No blind LVN limit entry, no fixed-RR target, no martingale, no
averaging down, and no stop widening. Non-baseline extensions are named `experimental_*` and
are OFF (currently only `experimental_early_exit_enabled`).
