"""Aggregated L2 (Databento MBP-10) book for validation/fallback (Package 1).

It cannot recover individual queue order IDs; queue metrics are explicitly unavailable.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fabio_mechanical.contracts import BookLevel, BookSnapshot
from fabio_mechanical.ingest.prices import price_nanos_to_ticks


class MBP10Book:
    def __init__(self) -> None:
        self.bids: list[BookLevel] = []
        self.asks: list[BookLevel] = []
        self.ts_event_ns = 0
        self.sequence = 0
        self.instrument = ""
        self.active_contract = ""

    @staticmethod
    def _get(rec: Any, key: str, default: Any = None) -> Any:
        return rec.get(key, default) if isinstance(rec, dict) else getattr(rec, key, default)

    def apply_record(self, rec: Any, *, instrument: str, active_contract: str, tick_size: Decimal = Decimal("0.25")) -> None:
        self.ts_event_ns = int(self._get(rec, "ts_event", 0))
        self.sequence = int(self._get(rec, "sequence", 0))
        self.instrument = instrument
        self.active_contract = active_contract
        bids: list[BookLevel] = []
        asks: list[BookLevel] = []
        levels = self._get(rec, "levels")
        rows: list[tuple[Any, Any, int, int, int, int]] = []
        if levels is not None:
            for level in levels:
                rows.append(
                    (
                        self._get(level, "bid_px"),
                        self._get(level, "ask_px"),
                        int(self._get(level, "bid_sz", 0) or 0),
                        int(self._get(level, "ask_sz", 0) or 0),
                        int(self._get(level, "bid_ct", 0) or 0),
                        int(self._get(level, "ask_ct", 0) or 0),
                    )
                )
        else:
            for i in range(10):
                suf = f"{i:02d}"
                rows.append(
                    (
                        self._get(rec, f"bid_px_{suf}"),
                        self._get(rec, f"ask_px_{suf}"),
                        int(self._get(rec, f"bid_sz_{suf}", 0) or 0),
                        int(self._get(rec, f"ask_sz_{suf}", 0) or 0),
                        int(self._get(rec, f"bid_ct_{suf}", 0) or 0),
                        int(self._get(rec, f"ask_ct_{suf}", 0) or 0),
                    )
                )
        for bp, ap, bs, asz, bc, ac in rows:
            if bp is not None and bs > 0:
                bids.append(BookLevel(price_ticks=price_nanos_to_ticks(int(bp), tick_size), size=bs, order_count=bc))
            if ap is not None and asz > 0:
                asks.append(BookLevel(price_ticks=price_nanos_to_ticks(int(ap), tick_size), size=asz, order_count=ac))
        self.bids = sorted(bids, key=lambda x: x.price_ticks, reverse=True)[:10]
        self.asks = sorted(asks, key=lambda x: x.price_ticks)[:10]

    def snapshot(self) -> BookSnapshot:
        bb = self.bids[0] if self.bids else None
        ba = self.asks[0] if self.asks else None
        spread = ba.price_ticks - bb.price_ticks if bb and ba else None
        wb = sum(x.size / (i + 1) for i, x in enumerate(self.bids))
        wa = sum(x.size / (i + 1) for i, x in enumerate(self.asks))
        denom = wb + wa
        imb = 0.0 if denom == 0 else (wb - wa) / denom
        micro = None
        if bb and ba and bb.size + ba.size:
            micro = (ba.price_ticks * bb.size + bb.price_ticks * ba.size) / (bb.size + ba.size)
        return BookSnapshot(
            ts_event_ns=self.ts_event_ns,
            sequence=self.sequence,
            instrument=self.instrument,
            active_contract=self.active_contract,
            best_bid=bb,
            best_ask=ba,
            bids=self.bids,
            asks=self.asks,
            spread_ticks=spread,
            weighted_bid_depth=wb,
            weighted_ask_depth=wa,
            dom_imbalance=imb,
            microprice_ticks=micro,
            mbo_available=False,
            queue_metrics={"queue_available": 0.0},
        )
