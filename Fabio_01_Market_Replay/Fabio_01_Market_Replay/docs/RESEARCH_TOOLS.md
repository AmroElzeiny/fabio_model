# Research tools

`export/research.py` provides:

- event slice export;
- NY RTH versus other-session segmentation with `zoneinfo` DST handling;
- feature-state regime tags;
- an optional FOMC/CPI/NFP metadata CSV hook that is not required by the feature engine;
- a contract-roll map writer.

No optimization or strategy parameter search is performed in this package.
