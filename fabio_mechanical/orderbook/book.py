"""Deterministic price-time MBO order book (Package 1, optimized and priority-corrected).

Semantics follow Databento MBO for CME Globex:

* ``TRADE``/``FILL``/``NONE`` never mutate the book; resting quantity is reduced by the
  ``CANCEL``/``MODIFY`` records that accompany every fill.
* ``MODIFY`` keeps queue priority only when price is unchanged and size does not increase.
* ``CLEAR`` empties the book; the book is ``initialized`` once a complete snapshot arrived.

Levels are kept in sorted price lists (bisect) with incrementally maintained aggregate
sizes, so best bid/ask and top-N depth are O(N) instead of O(orders).
"""

from __future__ import annotations

from bisect import bisect_left, insort
from dataclasses import dataclass

from fabio_mechanical.contracts import BookLevel, BookSnapshot, EventAction, MarketEvent, Side


@dataclass(slots=True)
class Order:
    order_id: int
    side: Side
    price_ticks: int
    size: int
    priority: int


class MBOBook:
    def __init__(self, top_n: int = 10) -> None:
        self.top_n = top_n
        self.orders: dict[int, Order] = {}
        self._queues: dict[Side, dict[int, dict[int, None]]] = {Side.BID: {}, Side.ASK: {}}
        self._level_size: dict[Side, dict[int, int]] = {Side.BID: {}, Side.ASK: {}}
        self._prices: dict[Side, list[int]] = {Side.BID: [], Side.ASK: []}
        self._priority = 0
        self.initialized = False
        self.last_ts_ns = 0
        self.last_sequence = 0
        self.instrument = ""
        self.active_contract = ""
        self.version = 0
        self.added_volume = {Side.BID: 0, Side.ASK: 0}
        self.removed_volume = {Side.BID: 0, Side.ASK: 0}
        self.executed_volume = {Side.BID: 0, Side.ASK: 0}
        self.replenishment_volume = {Side.BID: 0, Side.ASK: 0}
        self._recent_depletion: dict[tuple[Side, int], int] = {}
        self._pending_fill: dict[int, int] = {}
        self._cache: BookSnapshot | None = None
        self._cache_version = -1
        self._mutation_ts_ns = 0
        self._mutation_sequence = 0
        self._level_cache: dict[tuple[int, int, int], BookLevel] = {}

    # ------------------------------------------------------------------ state changes
    def clear(self) -> None:
        self.orders.clear()
        self._queues = {Side.BID: {}, Side.ASK: {}}
        self._level_size = {Side.BID: {}, Side.ASK: {}}
        self._prices = {Side.BID: [], Side.ASK: []}
        self._pending_fill.clear()
        self.initialized = False
        self.version += 1

    def _next_priority(self) -> int:
        self._priority += 1
        return self._priority

    def _ensure_level(self, side: Side, price: int) -> None:
        if price not in self._level_size[side]:
            insort(self._prices[side], price)
            self._level_size[side][price] = 0
            self._queues[side][price] = {}

    def _drop_level_if_empty(self, side: Side, price: int) -> None:
        if self._level_size[side].get(price, 0) <= 0 and not self._queues[side].get(price):
            self._level_size[side].pop(price, None)
            self._queues[side].pop(price, None)
            prices = self._prices[side]
            i = bisect_left(prices, price)
            if i < len(prices) and prices[i] == price:
                prices.pop(i)

    def _insert(self, order_id: int, side: Side, price: int, size: int) -> None:
        if side not in (Side.BID, Side.ASK) or size <= 0:
            return
        self._ensure_level(side, price)
        order = Order(order_id, side, price, size, self._next_priority())
        self.orders[order_id] = order
        self._queues[side][price][order_id] = None
        self._level_size[side][price] += size
        self.added_volume[side] += size
        depleted = self._recent_depletion.get((side, price), 0)
        if depleted:
            replenished = min(depleted, size)
            self.replenishment_volume[side] += replenished
            self._recent_depletion[(side, price)] = depleted - replenished

    def _reduce(self, order: Order, qty: int) -> int:
        removed = min(order.size, max(qty, 0))
        if removed <= 0:
            return 0
        executed = min(self._pending_fill.get(order.order_id, 0), removed)
        if executed:
            self._pending_fill[order.order_id] -= executed
            self.executed_volume[order.side] += executed
        self.removed_volume[order.side] += removed - executed
        key = (order.side, order.price_ticks)
        self._recent_depletion[key] = self._recent_depletion.get(key, 0) + removed
        order.size -= removed
        self._level_size[order.side][order.price_ticks] -= removed
        if order.size <= 0:
            self._queues[order.side][order.price_ticks].pop(order.order_id, None)
            self.orders.pop(order.order_id, None)
            self._pending_fill.pop(order.order_id, None)
            self._drop_level_if_empty(order.side, order.price_ticks)
        return removed

    def apply(self, event: MarketEvent) -> None:
        self.last_ts_ns = event.clock_ns
        self.last_sequence = event.sequence
        self.instrument = event.instrument
        self.active_contract = event.active_contract
        before = self.version
        self._apply(event)
        if self.version != before:
            self._mutation_ts_ns = event.clock_ns
            self._mutation_sequence = event.sequence

    def _apply(self, event: MarketEvent) -> None:
        action = event.action
        if action == EventAction.CLEAR:
            self.clear()
            return
        if action == EventAction.SNAPSHOT:
            if event.snapshot_complete:
                self.initialized = True
            return
        if action == EventAction.CONTRACT_ROLL:
            self.clear()
            return
        if action == EventAction.ADD:
            assert event.order_id is not None and event.price_ticks is not None
            existing = self.orders.get(event.order_id)
            if existing is not None:
                self._reduce(existing, existing.size)
            self._insert(event.order_id, event.side, event.price_ticks, event.size)
            self.version += 1
            return
        if action == EventAction.CANCEL:
            assert event.order_id is not None
            order = self.orders.get(event.order_id)
            if order is not None:
                self._reduce(order, order.size if event.size <= 0 else event.size)
                self.version += 1
            return
        if action == EventAction.MODIFY:
            assert event.order_id is not None and event.price_ticks is not None
            self._modify(event.order_id, event.side, event.price_ticks, event.size)
            self.version += 1
            return
        if action == EventAction.FILL:
            if event.order_id is not None and event.size > 0:
                self._pending_fill[event.order_id] = self._pending_fill.get(event.order_id, 0) + event.size
            return

    def _modify(self, order_id: int, side: Side, price: int, size: int) -> None:
        old = self.orders.get(order_id)
        if old is None:
            self._insert(order_id, side, price, size)
            return
        if size <= 0:
            self._reduce(old, old.size)
            return
        if old.side != side or old.price_ticks != price:
            self._reduce(old, old.size)
            self._insert(order_id, side, price, size)
            return
        if size > old.size:
            delta = size - old.size
            q = self._queues[side][price]
            q.pop(order_id, None)
            q[order_id] = None  # moves to the back: size increase loses priority
            old.priority = self._next_priority()
            old.size = size
            self._level_size[side][price] += delta
            self.added_volume[side] += delta
            return
        if size < old.size:
            self._reduce(old, old.size - size)

    # ------------------------------------------------------------------ queries
    def best_bid(self) -> int | None:
        p = self._prices[Side.BID]
        return p[-1] if p else None

    def best_ask(self) -> int | None:
        p = self._prices[Side.ASK]
        return p[0] if p else None

    def level_size(self, side: Side, price: int) -> int:
        return self._level_size[side].get(price, 0)

    def price_levels(self, side: Side) -> list[int]:
        """Prices from best to worst."""
        p = self._prices[side]
        return list(reversed(p)) if side == Side.BID else list(p)

    def levels(self, side: Side, n: int | None = None) -> list[BookLevel]:
        prices = self._prices[side]
        if side == Side.BID:
            chosen = prices[::-1] if n is None else prices[: -n - 1 : -1] if n else []
        else:
            chosen = prices if n is None else prices[:n]
        sizes = self._level_size[side]
        queues = self._queues[side]
        cache = self._level_cache
        if len(cache) > 50_000:
            cache.clear()
        out = []
        for p in chosen:
            key = (p, sizes[p], len(queues[p]))
            lvl = cache.get(key)
            if lvl is None:
                # immutable, validated once and re-used while the level is unchanged
                lvl = cache[key] = BookLevel(price_ticks=key[0], size=key[1], order_count=key[2])
            out.append(lvl)
        return out

    def current_priority(self) -> int:
        """Priority a newly arriving order would receive (strictly behind every resting order)."""
        return self._priority + 1

    def queue_ahead(self, side: Side, price: int, priority: int) -> int:
        """Displayed quantity resting at ``price`` with better time priority than ``priority``."""
        ahead = 0
        for oid in self._queues[side].get(price, {}):
            o = self.orders[oid]
            if o.priority >= priority:
                break
            ahead += o.size
        return ahead

    def queue_position(self, order_id: int) -> dict[str, int] | None:
        order = self.orders.get(order_id)
        if order is None:
            return None
        idx = 0
        ahead = 0
        for oid in self._queues[order.side][order.price_ticks]:
            if oid == order_id:
                break
            ahead += self.orders[oid].size
            idx += 1
        return {"orders_ahead": idx, "contracts_ahead": ahead}

    def sweep(self, side: Side, quantity: int, worst_price: int | None = None) -> list[tuple[int, int]]:
        """Non-mutating depth sweep of ``side`` (the side being consumed) from its best price."""
        out: list[tuple[int, int]] = []
        remaining = quantity
        for price in self.price_levels(side):
            if remaining <= 0:
                break
            if worst_price is not None:
                if side == Side.ASK and price > worst_price:
                    break
                if side == Side.BID and price < worst_price:
                    break
            take = min(remaining, self._level_size[side][price])
            if take > 0:
                out.append((price, take))
                remaining -= take
        return out

    def snapshot(self) -> BookSnapshot:
        # BookSnapshot.ts_event_ns is the time of the last book mutation (cached per version).
        if self._cache is not None and self._cache_version == self.version:
            return self._cache
        bids = self.levels(Side.BID, self.top_n)
        asks = self.levels(Side.ASK, self.top_n)
        best_bid = bids[0] if bids else None
        best_ask = asks[0] if asks else None
        spread = micro = None
        if best_bid and best_ask:
            spread = best_ask.price_ticks - best_bid.price_ticks
            denom = best_bid.size + best_ask.size
            if denom:
                micro = (best_ask.price_ticks * best_bid.size + best_bid.price_ticks * best_ask.size) / denom
        weighted_bid = sum(x.size / (i + 1) for i, x in enumerate(bids))
        weighted_ask = sum(x.size / (i + 1) for i, x in enumerate(asks))
        denom_depth = weighted_bid + weighted_ask
        imbalance = 0.0 if denom_depth == 0 else (weighted_bid - weighted_ask) / denom_depth
        snap = BookSnapshot(
            ts_event_ns=self._mutation_ts_ns,
            sequence=self._mutation_sequence,
            instrument=self.instrument,
            active_contract=self.active_contract,
            best_bid=best_bid,
            best_ask=best_ask,
            bids=bids,
            asks=asks,
            spread_ticks=spread,
            weighted_bid_depth=weighted_bid,
            weighted_ask_depth=weighted_ask,
            dom_imbalance=imbalance,
            microprice_ticks=micro,
            mbo_available=True,
            queue_metrics={"displayed_order_count": float(len(self.orders))},
        )
        self._cache = snap
        self._cache_version = self.version
        return snap

    def liquidity_metrics(self) -> dict[str, float]:
        added_bid = float(self.added_volume[Side.BID])
        added_ask = float(self.added_volume[Side.ASK])
        rem_bid = float(self.removed_volume[Side.BID])
        rem_ask = float(self.removed_volume[Side.ASK])
        return {
            "bid_stacking_volume": added_bid,
            "ask_stacking_volume": added_ask,
            "bid_pulling_volume": rem_bid,
            "ask_pulling_volume": rem_ask,
            "bid_executed_volume": float(self.executed_volume[Side.BID]),
            "ask_executed_volume": float(self.executed_volume[Side.ASK]),
            "bid_net_liquidity": added_bid - rem_bid,
            "ask_net_liquidity": added_ask - rem_ask,
            "bid_replenishment": float(self.replenishment_volume[Side.BID]),
            "ask_replenishment": float(self.replenishment_volume[Side.ASK]),
        }

    def reset_flow_metrics(self) -> None:
        self.added_volume = {Side.BID: 0, Side.ASK: 0}
        self.removed_volume = {Side.BID: 0, Side.ASK: 0}
        self.executed_volume = {Side.BID: 0, Side.ASK: 0}
        self.replenishment_volume = {Side.BID: 0, Side.ASK: 0}
        self._recent_depletion.clear()
