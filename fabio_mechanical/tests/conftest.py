from __future__ import annotations

import copy
from typing import Any

import pytest

from fabio_mechanical.backtest import BacktestEngine, BacktestResult
from fabio_mechanical.configs import load_config
from fabio_mechanical.contracts import MarketEvent
from fabio_mechanical.ingest.synthetic import scenario_events

_EVENTS: dict[str, list[MarketEvent]] = {}
_RESULTS: dict[str, BacktestResult] = {}


def events_for(name: str) -> list[MarketEvent]:
    if name not in _EVENTS:
        _EVENTS[name] = scenario_events(name)
    return _EVENTS[name]


@pytest.fixture(scope="session")
def base_cfg() -> dict[str, Any]:
    return load_config()


@pytest.fixture
def cfg(base_cfg: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(base_cfg)


@pytest.fixture(scope="session")
def scenario() -> Any:
    return events_for


@pytest.fixture(scope="session")
def backtest(base_cfg: dict[str, Any]) -> Any:
    def run(name: str) -> BacktestResult:
        if name not in _RESULTS:
            _RESULTS[name] = BacktestEngine(copy.deepcopy(base_cfg)).run(events_for(name))
        return _RESULTS[name]

    return run
