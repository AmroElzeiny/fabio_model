"""Single research run: a full event-driven backtest over a dataset (streamed)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fabio_mechanical.backtest import BacktestEngine, BacktestResult
from fabio_mechanical.backtest.engine import SnapshotTransform
from fabio_mechanical.journal import NullJournal

from .datasets import DatasetLike, events_of


def run_dataset(
    cfg: dict[str, Any],
    dataset: DatasetLike,
    *,
    transform: SnapshotTransform | None = None,
    day_filter: Callable[[str], bool] | None = None,
) -> BacktestResult:
    engine = BacktestEngine(cfg, journal=NullJournal(), snapshot_transform=transform, day_filter=day_filter)
    return engine.run(events_of(dataset))
