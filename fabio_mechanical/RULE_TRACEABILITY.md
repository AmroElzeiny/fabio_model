# RULE_TRACEABILITY

## 1. Documented Fabio rules → implementation

| Rule (public playbook) | Code | Config / invariant |
|---|---|---|
| Read market state before trading | `market/structure.py` (balance/out-of-balance), `strategy/fsm.py` start states | invariant `context_before_entry`, `decision_order` |
| Two separate models (trend / mean reversion) | `TrendMachine`, `MeanReversionMachine` | `separate_models`, `allowed_setups` |
| Trend = out-of-balance continuation after an impulse that broke structure | `TrendMachine` states `VALID_BREAKOUT → DISPLACEMENT → STRUCTURE_BREAK → IMPULSE_LOCKED` | `trend_sequence` |
| Profile the impulse leg | `LegTracker` + frozen `impulse_profile` | `pullback_depth_min` |
| Location = LVN of that profile | `strategy/common.py::choose_lvn`, `lvn_qualifies` | `lvn_*` |
| Mean reversion = failed breakout, no acceptance, reclaim, reclaim-leg LVN | `MeanReversionMachine` states `BREAKOUT_ATTEMPT → … → WAIT_LVN_RETEST` | `mean_reversion_sequence` |
| No aggression, no trade; an LVN touch alone is never an entry | `aggression_evidence`, `at_location`, FSM `WAIT_AGGRESSION` / `SNAPBACK_AGGRESSION` | `lvn_requires_directional_aggression`, `lvn_touch_alone_is_never_entry` |
| POC is the target | `strategy/target_stop.py::target_for` | `primary_target_is_poc`, `no_fixed_rr_target` |
| Stop at logical invalidation; never widen | `stop_for`, `validate_stop_update`, `ExecutionSimulator.modify_stop`, pre-entry invalidation reset | `stop_is_structural_invalidation`, `never_widen_stop` |
| Small fractional account risk | `risk/engine.py` | `risk.yaml` |
| No trading on bad data | `quality/engine.py`, `is_trade_safe`, risk `data_quality_lock` / `stale_data_lock`, simulator `unsafe_data` | `unsafe_data_blocks_trading`, `stale_data_blocks_trading` |
| No AI | `decision/provider.py` (`AIProviderStub` raises), `DecisionSnapshot.ai_enabled: Literal[False]`, config validation | `ai_enabled: false`, `ai_can_override_risk: false` |

## 2. Market-structure definitions (integration layer, `market/structure.py`)

Package 2 consumed these fields but never defined how to compute them. The definitions below
are deterministic and causal. Their numbers are interpretations, not Fabio-authored values.

| Field | Definition | Parameters |
|---|---|---|
| structure period | `clock // period_seconds`; the unit of every `*_events` counter | `structure.period_seconds` (60) |
| balance / reference balance | at each period close, the last `balance_window_minutes` are split in two halves. The window is balanced when the value-area overlap of the halves ≥ min, abs(POC_2 − POC_1) ≤ max, and the efficiency of `sample_seconds` samples ≤ max. The reference = the window value area (VAL..VAH, POC, range). While an excursion is active it stays frozen, unless a non-overlapping balance forms (new value): then the old one is archived. | `balance_value_overlap_min`, `poc_migration_max`, `directional_efficiency_max_for_balance`, `structure.balance_window_minutes`, `structure.sample_seconds`, `market.value_area_fraction` |
| excursion, `breakout_side`, `breakout_distance_ticks` | trades beyond VAH (up) or VAL (down); the distance is the maximum excursion beyond the edge | — |
| `acceptance_events` | completed periods that closed beyond the edge during the excursion | — |
| `failed_auction`, `reclaim_inside_balance` | the excursion returned inside value with acceptance < `acceptance_event_count`; reclaim = price currently inside value after that | `acceptance_event_count` |
| `displacement_zscore` | (price − price one period ago) × breakout side, standardized by the mean/std of the absolute close-to-close moves of the last N active periods | `structure.displacement_lookback_periods`, `structure.displacement_min_periods` |
| impulse leg | from the last trade on the far side of the reference POC to the excursion extreme. `impulse_efficiency` = abs(extreme − origin) ÷ sampled path length. The leg locks (`impulse_id`, frozen `impulse_profile`) when retracement ≥ `pullback_depth_min` of it | `pullback_depth_min` |
| `out_of_balance` | an active excursion whose impulse efficiency ≥ `directional_efficiency_min` | `directional_efficiency_min` |
| `structure_break` | excursion extreme beyond the balance range extreme | — |
| reclaim leg | from the failed-auction extreme back into value; locks the same way (`reclaim_leg_id`, `reclaim_profile`) | `pullback_depth_min` |
| `rejection_speed` | reclaim-leg velocity ÷ excursion velocity (ticks/s, durations floored at 1 s), clipped to [0, 1] | — |
| `relevant_poc_candidates` | POCs of the reference balance, archived intraday balances and prior sessions (reset on a contract roll) | `structure.max_archived_balances`, `structure.max_prior_sessions` |
| LVN zone | a contiguous run of prices (untraded ticks count as 0) at or below the `lvn_quantile` volume, bounded by traded volume on both sides. Reports `volume_to_median_ratio`, `neighboring_volume_ratio`, width, age (periods since lock) and revisits | `market.lvn_quantile` |

## 3. Aggression measures (`orderflow/`, used by `strategy/common.py`)

| Measure | Definition |
|---|---|
| aggression window | last `market.rolling_trade_window` trades (50) |
| `delta_zscore` | z-score of the window delta against the session history of window deltas (needs `rolling_trade_window` samples) |
| `cvd_slope_normalized` | OLS slope of the window cumulative delta ÷ mean trade size, in [−1, 1] |
| `big_buy/sell_print_percentile` | session mid-rank percentile of the largest same-side print in the window |
| stacked imbalance | longest run of **adjacent** prices whose diagonal ratio ≥ `stacked_imbalance_ratio`, in the window footprint |
| `absorption_score` | Package 1's evidence average (volume per tick progress, adverse aggression without continuation, repeated same-level aggression, CVD/price divergence) over the window |
| `directional_aggression` | sign of the window delta when trade-count persistence agrees |
| at location | the aggression window's price range overlaps the chosen LVN |

## 4. Quantitative interpretations (not claimed to be Fabio's numbers)

Every key under `interpretation.yaml: strategy` is one. Those listed in `search_space.yaml`
carry units, bounds and a rationale and may be tuned only in the development phase. Keys that
are not searchable: `directional_efficiency_max_for_balance`, `data_quality_min`,
`stale_data_ms_max`, `session_windows`, and the whole `structure`/`market` sections.

## 5. Experimental extensions (all OFF in the baseline)

| Flag | Effect |
|---|---|
| `experimental_early_exit_enabled` | participation-based exit (opposing aggression, severe absorption, lost auction). Package 2 feature, renamed. |

A baseline freeze refuses any enabled experimental flag.

## 6. Integration changes to rules

See `INTEGRATION_REPORT.md` §5. Every change is listed there with its reason, including the
parameters Package 2 declared but never wired (`lvn_neighbor_ratio`, `aggression_persistence`,
`pullback_depth_min/max`), which are now enforced.
