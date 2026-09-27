# Fabio definition notes

The documented public playbook describes the workflow as **Market State → Location → Aggression**. It frames market state as balance versus out-of-balance, location through Volume Profile areas such as LVN/POC, and execution confirmation through order-flow aggression such as large prints, footprint imbalance, and CVD pressure.

This Package deliberately implements only the **measurement layer**. It does not encode the public page's entry, stop, target, or risk rules, because Package 1 is a replay/feature package rather than a strategy package.

Numerical thresholds not uniquely specified by the public material are configurable and versioned. In particular: value-area fraction, LVN/HVN quantiles, footprint imbalance ratio, stacked-imbalance count, big-print percentile, market-state thresholds, absorption scaling and iceberg-evidence scaling.

Source reviewed during implementation:
- https://www.chartfanatics.com/strategies/auction-market-strategy
- https://www.youtube.com/watch?v=tvERE-Beu2U (page metadata available; no unsupported transcript claims were used)
