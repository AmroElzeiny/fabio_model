"""Deterministic synthetic raw MBO generator (Databento GLBX.MDP3 record layout).

Records are plain dicts with Databento MBO field names/encodings (prices in 1e-9 units,
``action``/``side`` chars, ``flags`` with ``F_LAST``/``F_SNAPSHOT``) so they flow through the
real ``DatabentoMBOAdapter`` exactly like downloaded data. Matching is FIFO: every aggressive
order emits ``T`` (aggressor side) followed by ``F`` + ``C`` for each resting order it fills.

Scenarios are built from phases (balance / move / hold / burst) and are fully seeded. They
exist to exercise the complete pipeline, not to claim anything about real markets.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from fabio_mechanical.contracts import MarketEvent

from .databento import ContractInfo, ContractResolver, DatabentoMBOAdapter
from .prices import FIXED_PRICE_SCALE

F_LAST = 1 << 7
F_SNAPSHOT = 1 << 5
NS = 1_000_000_000
TICK_NANOS = 250_000_000  # 0.25 index points


def utc_ns(y: int, mo: int, d: int, h: int, mi: int, s: int = 0) -> int:
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp()) * NS


@dataclass
class SyntheticMBO:
    seed: int = 7
    instrument_id: int = 4242
    raw_symbol: str = "NQH6"
    depth_levels: int = 8
    records: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        self.ts = 0
        self.sequence = 1000
        self.next_oid = 1
        self.bids: dict[int, list[list[int]]] = {}
        self.asks: dict[int, list[list[int]]] = {}
        self._pending: list[dict[str, Any]] = []
        self.trades: list[tuple[int, int, int]] = []

    def value_area(self, minutes: int) -> tuple[int, int, int]:
        """(POC, VAH, VAL) of the generator's own trades over the last ``minutes`` (same algorithm as the replay)."""
        from fabio_mechanical.profile.volume_profile import value_area

        horizon = self.ts - minutes * 60 * NS
        vap: dict[int, int] = {}
        for ts, price, size in self.trades:
            if ts >= horizon:
                vap[price] = vap.get(price, 0) + size
        poc, vah, val = value_area(vap, 0.70)
        assert poc is not None and vah is not None and val is not None
        return poc, vah, val

    # ------------------------------------------------------------------ record plumbing
    def _rec(self, action: str, side: str, price: int | None, size: int, oid: int, flags: int = 0) -> None:
        self._pending.append(
            {
                "ts_event": self.ts,
                "ts_recv": self.ts + 15_000,
                "sequence": self.sequence,
                "instrument_id": self.instrument_id,
                "channel_id": 1,
                "action": action,
                "side": side,
                "price": (1 << 63) - 1 if price is None else price * TICK_NANOS,
                "size": size,
                "order_id": oid,
                "flags": flags,
            }
        )

    def _flush(self) -> None:
        if not self._pending:
            return
        self._pending[-1]["flags"] |= F_LAST
        self.records.extend(self._pending)
        self._pending = []
        self.sequence += 1
        self.ts += 40_000  # packets 40 microseconds apart

    def set_time(self, ts_ns: int) -> None:
        self.ts = max(self.ts, ts_ns)

    # ------------------------------------------------------------------ book helpers
    @property
    def best_bid(self) -> int:
        return max(self.bids)

    @property
    def best_ask(self) -> int:
        return min(self.asks)

    def _new_order(self, book: dict[int, list[list[int]]], price: int, size: int) -> int:
        oid = self.next_oid
        self.next_oid += 1
        book.setdefault(price, []).append([oid, size])
        return oid

    def seed_book(self, bid: int) -> None:
        """Clear + full snapshot (as Databento publishes at 00:00 UTC) around ``bid``/``bid+1``."""
        self.bids.clear()
        self.asks.clear()
        for i in range(self.depth_levels):
            for _ in range(2):
                self._new_order(self.bids, bid - i, self.rng.randint(3, 10))
                self._new_order(self.asks, bid + 1 + i, self.rng.randint(3, 10))
        self._rec("R", "N", None, 0, 0, F_SNAPSHOT)
        for book, side in ((self.bids, "B"), (self.asks, "A")):
            for price in sorted(book):
                for oid, size in book[price]:
                    self._rec("A", side, price, size, oid, F_SNAPSHOT)
        self._flush()

    def _refill(self, moved_up: bool = True) -> None:
        """Keep a 1-tick spread (new liquidity follows the move) and contiguous depth on both sides."""
        recs: list[tuple[str, int, int, int]] = []
        while self.best_ask - self.best_bid > 1:
            if moved_up:
                price = self.best_bid + 1
                recs.append(("B", price, self.rng.randint(3, 10), self._new_order(self.bids, price, 0)))
            else:
                price = self.best_ask - 1
                recs.append(("A", price, self.rng.randint(3, 10), self._new_order(self.asks, price, 0)))
        bb, ba = self.best_bid, self.best_ask
        for i in range(self.depth_levels):
            if bb - i not in self.bids:
                recs.append(("B", bb - i, self.rng.randint(3, 10), self._new_order(self.bids, bb - i, 0)))
            if ba + i not in self.asks:
                recs.append(("A", ba + i, self.rng.randint(3, 10), self._new_order(self.asks, ba + i, 0)))
        for book in (self.bids, self.asks):
            for price in [p for p in book if abs(p - (bb if book is self.bids else ba)) >= self.depth_levels + 4]:
                for oid, size in book.pop(price):
                    if size > 0:
                        self._rec("C", "B" if book is self.bids else "A", price, size, oid)
        for side, price, size, oid in recs:
            book = self.bids if side == "B" else self.asks
            for o in book[price]:
                if o[0] == oid:
                    o[1] = size
            self._rec("A", side, price, size, oid)
        self._flush()

    def noise(self) -> None:
        """Cancel/replace away from the touch (pulling/stacking activity)."""
        side = self.rng.choice(("B", "A"))
        book = self.bids if side == "B" else self.asks
        away = [p for p in book if abs(p - (self.best_bid if side == "B" else self.best_ask)) >= 2]
        if not away:
            return
        price = self.rng.choice(sorted(away))
        level = book[price]
        oid, size = level.pop(0)
        self._rec("C", side, price, size, oid)
        new_size = self.rng.randint(3, 10)
        self._rec("A", side, price, new_size, self._new_order(book, price, new_size))
        self._flush()

    def aggress(self, buy: bool, qty: int, replenish: bool = False) -> None:
        """Aggressive order of ``qty``; sweeps levels FIFO. ``replenish`` keeps the price level."""
        book = self.asks if buy else self.bids
        rest_side = "A" if buy else "B"
        aggr_side = "B" if buy else "A"
        if replenish:
            price = self.best_ask if buy else self.best_bid
            have = sum(o[1] for o in book[price])
            if have <= qty:
                add = qty - have + self.rng.randint(2, 6)
                oid = self._new_order(book, price, add)
                self._rec("A", rest_side, price, add, oid)
                self._flush()
        remaining = qty
        while remaining > 0:
            price = min(book) if buy else max(book)
            level = book[price]
            filled_here: list[tuple[int, int]] = []
            while level and remaining > 0:
                oid, size = level[0]
                take = min(size, remaining)
                filled_here.append((oid, take))
                remaining -= take
                if take == size:
                    level.pop(0)
                else:
                    level[0][1] = size - take
            total = sum(t for _, t in filled_here)
            self._rec("T", aggr_side, price, total, 0)
            self.trades.append((self.ts, price, total))
            for oid, take in filled_here:
                self._rec("F", rest_side, price, take, oid)
            for oid, take in filled_here:
                self._rec("C", rest_side, price, take, oid)
            if not level:
                del book[price]
            if not book:
                # never leave a side empty: add fresh liquidity one tick further
                nxt = price + 1 if buy else price - 1
                self._new_order(book, nxt, self.rng.randint(3, 10))
                self._rec("A", rest_side, nxt, book[nxt][0][1], book[nxt][0][0])
        self._flush()
        self._refill(moved_up=buy)

    def pull_touch(self, buy: bool, keep: int) -> None:
        """Cancel resting size at the touch that an up (buy) / down move would consume, keeping ``keep`` lots."""
        book = self.asks if buy else self.bids
        side = "A" if buy else "B"
        price = self.best_ask if buy else self.best_bid
        level = book[price]
        total = sum(o[1] for o in level)
        excess = total - keep
        for o in list(reversed(level)):
            if excess <= 0:
                break
            take = min(o[1] - (1 if o is level[0] else 0), excess)
            if take <= 0:
                continue
            o[1] -= take
            excess -= take
            self._rec("C", side, price, take, o[0])
        book[price] = [o for o in level if o[1] > 0]
        self._flush()

    def step_toward(self, target: int, sweep_size: int | None = None) -> None:
        """Move one tick toward ``target`` by sweeping the touch (optionally thinned to ``sweep_size``)."""
        if target == self.best_bid:
            return
        buy = target > self.best_bid
        if sweep_size is not None:
            self.pull_touch(buy, sweep_size)
        book = self.asks if buy else self.bids
        price = self.best_ask if buy else self.best_bid
        self.aggress(buy, sum(o[1] for o in book[price]))

    def small_size(self) -> int:
        return self.rng.choices((1, 2, 3, 4, 5), weights=(45, 25, 15, 10, 5))[0]

    # ------------------------------------------------------------------ phases
    def balance(
        self,
        center: int,
        half: int,
        seconds: int,
        step_s: float = 1.0,
        retarget: int = 4,
        core_weight: int = 3,
        core_offset: int = 0,
    ) -> None:
        """Two-sided rotation around ``center``; volume concentrates at ``center + core_offset`` (the POC)."""
        target = center
        steps = int(seconds / step_s)
        core = max(1, half // 3)
        mode = max(-half, min(half, core_offset))
        for i in range(steps):
            if i % retarget == 0:
                target = center + round(self.rng.triangular(-half, half, mode))
            if self.best_bid != target:
                self.step_toward(target, sweep_size=3)
            n = core_weight if abs(self.best_bid - (center + core_offset)) <= core else 1
            for _ in range(n):
                self.aggress(self.rng.random() < 0.5, self.small_size(), replenish=True)
            if self.rng.random() < 0.3:
                self.noise()
            self.ts += int(step_s * NS)

    def move(
        self,
        to: int,
        seconds: float,
        trades_per_tick: tuple[int, int] = (2, 4),
        sizes: tuple[int, int] = (1, 5),
        sweep_size: int = 5,
        bias: float = 0.7,
    ) -> None:
        """Directional move; ``trades_per_tick``/``sweep_size``/``bias`` control volume left at each price."""
        ticks = abs(to - self.best_bid)
        if ticks == 0:
            return
        dt = int(seconds * NS / ticks)
        up = to > self.best_bid
        for _ in range(ticks):
            n = self.rng.randint(*trades_per_tick)
            for _ in range(n):
                buy = self.rng.random() < (bias if up else 1 - bias)
                self.aggress(buy, self.rng.randint(*sizes), replenish=True)
            self.step_toward(to, sweep_size=sweep_size)
            self.ts += dt

    def hold(self, seconds: float, trades_per_step: tuple[int, int] = (2, 4), step_s: float = 1.0, sizes: tuple[int, int] = (1, 5)) -> None:
        for _ in range(int(seconds / step_s)):
            for _ in range(self.rng.randint(*trades_per_step)):
                self.aggress(self.rng.random() < 0.5, self.rng.randint(*sizes), replenish=True)
            self.ts += int(step_s * NS)

    def burst(self, buy: bool, trades: int, ticks: int, seconds: float, sizes: tuple[int, int] = (20, 50)) -> None:
        """Aggressive one-sided flow with large prints that progresses ``ticks`` over ``trades``."""
        start = self.best_bid
        dt = int(seconds * NS / trades)
        for i in range(1, trades + 1):
            planned = start + (1 if buy else -1) * round(ticks * i / trades)
            while (buy and self.best_bid < planned) or (not buy and self.best_bid > planned):
                self.step_toward(planned, sweep_size=self.rng.randint(15, 30))
            self.aggress(buy, self.rng.randint(*sizes), replenish=True)
            self.ts += dt

    def pause(self, seconds: float) -> None:
        self.ts += int(seconds * NS)

    # ------------------------------------------------------------------ output
    def resolver(self) -> ContractResolver:
        return ContractResolver(
            {self.instrument_id: ContractInfo(self.instrument_id, self.raw_symbol, "NQ", Decimal(TICK_NANOS) / FIXED_PRICE_SCALE)}
        )

    def events(self) -> Iterator[MarketEvent]:
        return DatabentoMBOAdapter(self.resolver()).events(self.records)


def _d(direction: int, center: int, offset: int) -> int:
    return center + direction * offset


def trend_day(direction: int = 1, seed: int = 11, aggression: bool = True) -> SyntheticMBO:
    """Prior-session balance A, overnight balance B, then an RTH trend continuation out of B.

    Designed sequence (NY 2026-01-13, EST): A (prior RTH) -> B (08:50-09:36) -> impulse out of B
    with a thin price zone -> pullback into that zone -> directional aggression -> run to A's POC.
    """
    m = SyntheticMBO(seed=seed)
    c_a = 80240 if direction > 0 else 80080
    c_b = 80160
    m.set_time(utc_ns(2026, 1, 12, 20, 20))  # 15:20 ET prior session
    m.seed_book(c_a)
    m.balance(c_a, 4, 40 * 60)
    m.set_time(utc_ns(2026, 1, 13, 13, 50))  # 08:50 ET
    m.seed_book(c_b)
    m.balance(c_b, 4, 46 * 60)
    m.move(c_b, 10, (2, 3))
    m.hold(20)  # quiet start of the impulse at the POC
    m.move(_d(direction, c_b, 5), 20, (1, 2))
    m.move(_d(direction, c_b, 18), 45, (6, 6), (3, 3), bias=1.0)
    m.hold(30, (3, 5))
    m.move(_d(direction, c_b, 24), 6, (0, 0), (1, 1), sweep_size=1, bias=1.0)  # thin (LVN) zone
    m.move(_d(direction, c_b, 40), 50, (6, 6), (3, 3), bias=1.0)
    m.hold(25, (3, 5))
    m.move(_d(direction, c_b, 22), 90, (1, 1), (1, 2), sweep_size=2)  # pullback into the thin zone
    if aggression:
        m.burst(direction > 0, 60, 4, 8)
        m.move(_d(direction, c_b, 84), 900, (2, 4))
    else:
        # retest without directional aggression: two-sided rotation, then failure through the LVN
        m.hold(180, (1, 2), sizes=(1, 2))
        m.move(_d(direction, c_b, 12), 240, (1, 2), (1, 2), bias=0.5)
    m.balance(m.best_bid, 4, 10 * 60)
    return m


def mean_reversion_day(direction: int = -1, seed: int = 23) -> SyntheticMBO:
    """Overnight balance (no MR allowed), then at the LONDON open a failed breakout, fast reclaim,
    LVN retest inside value and snap-back aggression toward the balance POC.

    ``direction`` is the TRADE direction (-1 short after a failed upside auction). Levels of the
    designed sequence are placed relative to the balance's measured POC/VAH/VAL.
    """
    m = SyntheticMBO(seed=seed)
    c = 80160
    up = -direction  # side of the failed excursion
    m.set_time(utc_ns(2026, 1, 13, 7, 5))  # 02:05 ET, GLOBEX_OVERNIGHT (MR not allowed)
    m.seed_book(c)
    m.balance(c, 24, 55 * 60, retarget=6, core_weight=1)
    poc, vah, val = m.value_area(30)
    edge = vah if up > 0 else val  # value edge on the breakout side
    room = abs(edge - poc)
    retest = poc + up * max(2, min(4, room - 1))  # LVN retest point inside value
    m.move(poc, 20, (1, 2))
    m.set_time(utc_ns(2026, 1, 13, 8, 1))  # 03:01 ET, LONDON
    m.move(edge + up * 16, 25, (2, 3))  # breakout attempt beyond value
    m.hold(8, (2, 3))
    m.move(retest + up * 3, 10, (5, 5), (3, 3), bias=1.0)
    m.move(retest - up * 2, 3, (0, 0), (1, 1), sweep_size=1, bias=1.0)  # thin zone around the retest point
    m.move(poc - up * 2, 6, (5, 5), (3, 3), bias=1.0)
    m.hold(15, (3, 5))
    m.move(retest, 60, (0, 0), (1, 1), sweep_size=1)  # slow, thin pullback into the LVN
    m.burst(up < 0, 70, 2, 10)
    m.move(poc - up * 8, 600, (2, 4))
    m.balance(m.best_bid, 4, 10 * 60)
    return m


def quiet_balance_day(seed: int = 5) -> SyntheticMBO:
    m = SyntheticMBO(seed=seed)
    m.set_time(utc_ns(2026, 1, 13, 14, 30))
    m.seed_book(80160)
    m.balance(80160, 6, 60 * 60)
    return m


def roll_day(seed: int = 9) -> SyntheticMBO:
    """Two contracts: NQH6 then NQM6 (new instrument_id, new snapshot) - continuous symbol roll."""
    m = SyntheticMBO(seed=seed)
    m.set_time(utc_ns(2026, 3, 12, 14, 30))
    m.seed_book(80160)
    m.balance(80160, 6, 20 * 60)
    m.instrument_id = 4343
    m.raw_symbol = "NQM6"
    m.set_time(utc_ns(2026, 3, 13, 0, 0))
    m.seed_book(80400)
    m.balance(80400, 6, 20 * 60)
    return m


def multi_contract_resolver(*days: SyntheticMBO) -> ContractResolver:
    contracts: dict[int, ContractInfo] = {}
    for d in days:
        contracts.update(d.resolver().contracts)
    contracts[4343] = ContractInfo(4343, "NQM6", "NQ", Decimal("0.25"))
    contracts[4242] = ContractInfo(4242, "NQH6", "NQ", Decimal("0.25"))
    return ContractResolver(contracts)


SCENARIOS = {
    "trend_long": lambda: trend_day(1),
    "trend_short": lambda: trend_day(-1, seed=12),
    "trend_long_no_aggression": lambda: trend_day(1, aggression=False),
    "mean_reversion_short": lambda: mean_reversion_day(-1),
    "mean_reversion_long": lambda: mean_reversion_day(1, seed=24),
    "quiet_balance": quiet_balance_day,
    "roll": roll_day,
}


def scenario_events(name: str) -> list[MarketEvent]:
    if name == "roll":
        m = roll_day()
        return list(DatabentoMBOAdapter(multi_contract_resolver(m)).events(m.records))
    return list(SCENARIOS[name]().events())


def scenario_records(name: str) -> list[dict[str, Any]]:
    return SCENARIOS[name]().records


def write_dbn(records: list[dict[str, Any]], path: str | Path, symbol: str = "NQ.v.0") -> Path:
    """Encode records as a real DBN file (requires the ``databento`` extra)."""
    import databento_dbn

    dbn: Any = databento_dbn  # the package stubs type enum members as str

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    start = records[0]["ts_recv"] if records else 0
    end = records[-1]["ts_recv"] + 1 if records else 1
    md = dbn.Metadata(
        dataset="GLBX.MDP3",
        start=start,
        stype_in=dbn.SType.CONTINUOUS,
        stype_out=dbn.SType.INSTRUMENT_ID,
        schema=dbn.Schema.MBO,
        symbols=[symbol],
        end=end,
    )
    actions = {
        "A": dbn.Action.ADD,
        "C": dbn.Action.CANCEL,
        "M": dbn.Action.MODIFY,
        "F": dbn.Action.FILL,
        "T": dbn.Action.TRADE,
        "R": dbn.Action.CLEAR,
        "N": dbn.Action.NONE,
    }
    sides = {"B": dbn.Side.BID, "A": dbn.Side.ASK, "N": dbn.Side.NONE}
    with p.open("wb") as f:
        f.write(bytes(md))
        for r in records:
            msg = dbn.MBOMsg(
                publisher_id=1,
                instrument_id=r["instrument_id"],
                ts_event=r["ts_event"],
                order_id=r["order_id"],
                price=r["price"],
                size=r["size"],
                action=actions[r["action"]],
                side=sides[r["side"]],
                ts_recv=r["ts_recv"],
                flags=r["flags"],
                channel_id=r["channel_id"],
                sequence=r["sequence"],
            )
            f.write(bytes(msg))
    return p


def write_definition_dbn(contracts: list[ContractInfo], path: str | Path, ts: int, symbol: str = "NQ.v.0") -> Path:
    import databento_dbn

    dbn: Any = databento_dbn  # the package stubs type enum members as str

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    md = dbn.Metadata(
        dataset="GLBX.MDP3",
        start=ts,
        stype_in=dbn.SType.CONTINUOUS,
        stype_out=dbn.SType.INSTRUMENT_ID,
        schema=dbn.Schema.DEFINITION,
        symbols=[symbol],
        end=ts + 1,
    )
    with p.open("wb") as f:
        f.write(bytes(md))
        for c in contracts:
            msg = dbn.InstrumentDefMsg(
                publisher_id=1,
                instrument_id=c.instrument_id,
                ts_event=ts,
                ts_recv=ts,
                min_price_increment=int(c.tick_size * FIXED_PRICE_SCALE),
                display_factor=FIXED_PRICE_SCALE,
                raw_symbol=c.raw_symbol,
                asset=c.root,
                security_type="FUT",
                instrument_class=dbn.InstrumentClass.FUTURE,
                security_update_action=dbn.SecurityUpdateAction.ADD,
            )
            f.write(bytes(msg))
    return p
