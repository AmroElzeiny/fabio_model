"""Exact fixed-point price conversion (Databento prices are int64 in 1e-9 units)."""

from __future__ import annotations

from decimal import Decimal

FIXED_PRICE_SCALE = 1_000_000_000
UNDEF_PRICE = (1 << 63) - 1


def tick_nanos(tick_size: Decimal) -> int:
    n = tick_size * FIXED_PRICE_SCALE
    if n != n.to_integral_value():
        raise ValueError(f"tick size {tick_size} is not representable in 1e-9 units")
    return int(n)


def nanos_to_ticks(price_nanos: int, tick_ns: int) -> int:
    """Round-half-up integer division (exact; no float or Decimal on the hot path)."""
    q, r = divmod(price_nanos, tick_ns)
    if 2 * r >= tick_ns:
        q += 1
    return q


def price_nanos_to_ticks(price_nanos: int, tick_size: Decimal = Decimal("0.25")) -> int:
    return nanos_to_ticks(int(price_nanos), tick_nanos(tick_size))
