# Architecture

Data moves in one direction:

`raw provider / fixture -> normalized MarketEvent -> DataQuality + Book Replay -> TradePrint -> OrderFlow/Footprint/Profile -> Auction Features -> MarketFeatureSnapshot`

The replay core is single-threaded by design to preserve event order and deterministic state. Large historical ranges should be split into independent chronological files (for example daily Databento batch files) and streamed/chunked. Parallelism is safe only at independent research-slice boundaries where state dependencies are explicitly handled; it is not enabled inside a single ordered replay stream.

Raw files are immutable. Derived outputs can be cached under `data/cache` using data+config hashing. JSONL is always available; Parquet export is an optional production extra.
