# FIRST_TEST_GUIDE

## 1. Environment

```bash
python -m venv .venv
# activate the environment
python -m pip install -e .[test]
```

## 2. Verify contracts

```bash
python -m fabio.cli schema-hash
```

The hash must remain unchanged for consumers expecting the exact contract version unless the schema is intentionally versioned.

## 3. Unit/integration tests

```bash
pytest -q
```

## 4. Synthetic end-to-end baseline

```bash
python -m fabio.cli demo-backtest
```

## 5. Research smoke runs

```bash
python -m fabio.cli demo-optimize
python -m fabio.cli demo-wfo
python -m fabio.cli demo-robustness
python -m fabio.cli demo-monte-carlo
python -m fabio.cli demo-ablation
```

## 6. One-command package self-check

```bash
python -m fabio.cli self-check
```

Generated JSON/CSV artifacts are written to `sample_reports/`.

## 7. First real historical test

Feed strictly time-ordered `MarketFeatureSnapshot` objects from an external data package. Do not change strategy logic to fit results. Confirm bid/ask, trade-safe status, profile timestamps and all derived features use only information available at each event timestamp.
