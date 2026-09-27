from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque

from fabio_market_replay.contracts import BookLevel, BookSnapshot, EventAction, MarketEvent, Side


@dataclass(slots=True)
class Order:
    order_id: int
    side: Side
    price_ticks: int
    size: int
    sequence: int


class MBOBook:
    """Deterministic price-time MBO book. Queue positions are observable only for displayed orders."""

    def __init__(self, top_n: int = 10) -> None:
        self.top_n = top_n
        self.orders: dict[int, Order] = {}
        self.queues: dict[Side, dict[int, Deque[int]]] = {
            Side.BID: defaultdict(deque), Side.ASK: defaultdict(deque)
        }
        self.initialized = False
        self.last_ts_ns = 0
        self.last_sequence = 0
        self.instrument = ""
        self.active_contract = ""
        self.added_volume = {Side.BID: 0, Side.ASK: 0}
        self.removed_volume = {Side.BID: 0, Side.ASK: 0}
        self.replenishment_volume = {Side.BID: 0, Side.ASK: 0}
        self._recent_depletion: dict[tuple[Side, int], int] = {}

    def clear(self) -> None:
        self.orders.clear()
        self.queues = {Side.BID: defaultdict(deque), Side.ASK: defaultdict(deque)}
        self.initialized = False

    def _remove_order(self, order_id: int, qty: int | None = None) -> int:
        order = self.orders.get(order_id)
        if order is None:
            return 0
        removed = order.size if qty is None else min(order.size, qty)
        remaining = order.size - removed
        self.removed_volume[order.side] += removed
        self._recent_depletion[(order.side, order.price_ticks)] = self._recent_depletion.get((order.side, order.price_ticks), 0) + removed
        if remaining <= 0:
            q = self.queues[order.side][order.price_ticks]
            try:
                q.remove(order_id)
            except ValueError:
                pass
            self.orders.pop(order_id, None)
        else:
            self.orders[order_id] = Order(order.order_id, order.side, order.price_ticks, remaining, order.sequence)
        return removed

    def apply(self, event: MarketEvent) -> None:
        self.last_ts_ns = event.ts_event_ns
        self.last_sequence = event.sequence
        self.instrument = event.instrument
        self.active_contract = event.active_contract
        if event.action == EventAction.CLEAR:
            self.clear()
            return
        if event.action == EventAction.SNAPSHOT:
            if event.snapshot_complete:
                self.initialized = True
            return
        if event.action == EventAction.CONTRACT_ROLL:
            self.clear()
            self.active_contract = event.active_contract
            return
        if event.action == EventAction.ADD:
            assert event.order_id is not None and event.price_ticks is not None
            if event.order_id in self.orders:
                self._remove_order(event.order_id)
            order = Order(event.order_id, event.side, event.price_ticks, event.size, event.sequence)
            self.orders[event.order_id] = order
            self.queues[event.side][event.price_ticks].append(event.order_id)
            self.added_volume[event.side] += event.size
            depleted = self._recent_depletion.get((event.side, event.price_ticks), 0)
            if depleted:
                replenished = min(depleted, event.size)
                self.replenishment_volume[event.side] += replenished
                self._recent_depletion[(event.side, event.price_ticks)] = max(0, depleted - replenished)
            return
        if event.action == EventAction.CANCEL:
            assert event.order_id is not None
            qty = event.size if event.size > 0 else None
            self._remove_order(event.order_id, qty)
            return
        if event.action == EventAction.MODIFY:
            assert event.order_id is not None and event.price_ticks is not None
            old = self.orders.get(event.order_id)
            if old is not None:
                self._remove_order(event.order_id)
            order = Order(event.order_id, event.side, event.price_ticks, event.size, event.sequence)
            self.orders[event.order_id] = order
            self.queues[event.side][event.price_ticks].append(event.order_id)
            return
        if event.action == EventAction.FILL:
            # Databento MBO Fill identifies the resting order filled but is documented as non-book-mutating.
            # The corresponding book-changing message(s) must drive displayed quantity.
            return

    def levels(self, side: Side) -> list[BookLevel]:
        aggregate: list[BookLevel] = []
        for price, q in self.queues[side].items():
            ids = [oid for oid in q if oid in self.orders]
            if not ids:
                continue
            size = sum(self.orders[oid].size for oid in ids)
            aggregate.append(BookLevel(price_ticks=price, size=size, order_count=len(ids)))
        aggregate.sort(key=lambda x: x.price_ticks, reverse=side == Side.BID)
        return aggregate

    def queue_position(self, order_id: int) -> dict[str, int] | None:
        order = self.orders.get(order_id)
        if order is None:
            return None
        q = self.queues[order.side][order.price_ticks]
        ahead = 0
        idx = 0
        for oid in q:
            if oid not in self.orders:
                continue
            if oid == order_id:
                break
            ahead += self.orders[oid].size
            idx += 1
        return {"orders_ahead": idx, "contracts_ahead": ahead}

    def snapshot(self) -> BookSnapshot:
        bids = self.levels(Side.BID)[: self.top_n]
        asks = self.levels(Side.ASK)[: self.top_n]
        best_bid = bids[0] if bids else None
        best_ask = asks[0] if asks else None
        spread = None
        micro = None
        if best_bid and best_ask:
            spread = best_ask.price_ticks - best_bid.price_ticks
            denom = best_bid.size + best_ask.size
            if denom:
                micro = (best_ask.price_ticks * best_bid.size + best_bid.price_ticks * best_ask.size) / denom
        weighted_bid = sum(x.size / (i + 1) for i, x in enumerate(bids))
        weighted_ask = sum(x.size / (i + 1) for i, x in enumerate(asks))
        denom_depth = weighted_bid + weighted_ask
        imbalance = 0.0 if denom_depth == 0 else (weighted_bid - weighted_ask) / denom_depth
        return BookSnapshot(
            ts_event_ns=self.last_ts_ns, sequence=self.last_sequence, instrument=self.instrument,
            active_contract=self.active_contract, best_bid=best_bid, best_ask=best_ask, bids=bids, asks=asks,
            spread_ticks=spread, weighted_bid_depth=weighted_bid, weighted_ask_depth=weighted_ask,
            dom_imbalance=imbalance, microprice_ticks=micro,
            queue_metrics={"displayed_order_count": float(len(self.orders))},
        )

    def liquidity_metrics(self) -> dict[str, float]:
        added_bid = float(self.added_volume[Side.BID]); added_ask = float(self.added_volume[Side.ASK])
        rem_bid = float(self.removed_volume[Side.BID]); rem_ask = float(self.removed_volume[Side.ASK])
        return {
            "bid_stacking_volume": added_bid,
            "ask_stacking_volume": added_ask,
            "bid_pulling_volume": rem_bid,
            "ask_pulling_volume": rem_ask,
            "bid_net_liquidity": added_bid - rem_bid,
            "ask_net_liquidity": added_ask - rem_ask,
            "bid_replenishment": float(self.replenishment_volume[Side.BID]),
            "ask_replenishment": float(self.replenishment_volume[Side.ASK]),
        }
