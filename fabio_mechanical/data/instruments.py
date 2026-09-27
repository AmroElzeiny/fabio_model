"""Instrument reference data (tick size / tick value) from configs/data.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    root: str
    tick_size: Decimal
    tick_value: Decimal
    point_value: Decimal

    def ticks_to_price(self, ticks: int | Decimal) -> Decimal:
        return Decimal(ticks) * self.tick_size


def instrument_root(symbol: str, known: list[str] | tuple[str, ...] = ("MNQ", "NQ")) -> str:
    """Map a raw contract (``NQH6``/``MNQZ5``) or root to its configured root (longest prefix)."""
    for root in sorted(known, key=len, reverse=True):
        if symbol.startswith(root):
            return root
    return symbol


def instrument_spec(cfg: dict[str, Any], symbol: str) -> InstrumentSpec:
    instruments = cfg["data"]["instruments"]
    root = instrument_root(symbol, tuple(instruments))
    if root not in instruments:
        raise KeyError(f"instrument {symbol} is not configured in data.yaml")
    spec = instruments[root]
    return InstrumentSpec(
        root=root,
        tick_size=Decimal(str(spec["tick_size"])),
        tick_value=Decimal(str(spec["tick_value"])),
        point_value=Decimal(str(spec["point_value"])),
    )
