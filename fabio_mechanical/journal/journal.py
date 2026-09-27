"""Append-only decision/execution journal (JSON lines).

Every record carries ``type``, ``timestamp_ns`` and ``event_index`` so a run can be audited:
state transitions, candidates with full evidence, provider decisions, risk verdicts, order
reports, fills, position lifecycle, data-quality changes, session/roll events.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any


def _default(x: Any) -> Any:
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, Enum):
        return x.value
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json")
    if hasattr(x, "__dataclass_fields__"):
        return {k: getattr(x, k) for k in x.__dataclass_fields__}
    return str(x)


def dumps(record: dict[str, Any]) -> str:
    return json.dumps(record, default=_default, sort_keys=True, separators=(",", ":"))


class Journal:
    def write(self, record: dict[str, Any]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        return None


class NullJournal(Journal):
    """Discards records (research sweeps keep only ledgers/metrics)."""

    def write(self, record: dict[str, Any]) -> None:
        return None


class MemoryJournal(Journal):
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def write(self, record: dict[str, Any]) -> None:
        self.records.append(json.loads(dumps(record)))

    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [r for r in self.records if r.get("type") == kind]


class JsonlJournal(Journal):
    """Streams to disk (bounded memory). Picklable: the file handle is reopened on restore."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: Any = self.path.open("a", encoding="utf-8", newline="\n")
        self.count = 0

    def write(self, record: dict[str, Any]) -> None:
        self._fh.write(dumps(record) + "\n")
        self.count += 1

    def flush(self) -> None:
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None and not self._fh.closed:
            self._fh.close()

    def __getstate__(self) -> dict[str, Any]:
        self.flush()
        state = dict(self.__dict__)
        state["_fh"] = None
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self._fh = self.path.open("a", encoding="utf-8", newline="\n")

    def records(self) -> Iterator[dict[str, Any]]:
        self.flush()
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)
