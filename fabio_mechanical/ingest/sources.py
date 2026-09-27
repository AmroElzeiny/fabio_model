"""Uniform, streaming event sources for replay/backtest/research.

An ``EventSource`` yields canonical ``MarketEvent`` objects in replay order without loading the
dataset into memory. Sources are re-iterable (each ``iter_events()`` call restarts from disk or
from the deterministic generator), which the optimizer/walk-forward rely on.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from fabio_mechanical.contracts import MarketEvent
from fabio_mechanical.data.manifest import dataset_manifest, synthetic_manifest

from .databento import ContractResolver, DatabentoMBOAdapter, iter_dbn_records, manifest_path, validate_manifest
from .local import iter_jsonl_events

DBN_SUFFIXES = (".dbn", ".dbn.zst", ".zst")


class EventSource(Protocol):
    name: str
    trading_day: date | None

    def iter_events(self) -> Iterator[MarketEvent]: ...

    def manifest(self) -> dict[str, Any]: ...


@dataclass
class JsonlSource:
    path: Path
    trading_day: date | None = None
    name: str = ""

    def __post_init__(self) -> None:
        self.name = self.name or self.path.name

    def iter_events(self) -> Iterator[MarketEvent]:
        return iter_jsonl_events(self.path)

    def manifest(self) -> dict[str, Any]:
        return dataset_manifest([self.path])


@dataclass
class DatabentoSource:
    path: Path
    resolver: ContractResolver
    trading_day: date | None = None
    name: str = ""
    definition_paths: list[Path] = field(default_factory=list)
    on_invalid: Callable[[str], None] | None = None
    last_adapter: DatabentoMBOAdapter | None = None

    def __post_init__(self) -> None:
        self.name = self.name or self.path.name

    def checksum_ok(self) -> bool | None:
        """True/False when a download manifest exists, None when the file has no manifest."""
        return validate_manifest(self.path) if manifest_path(self.path).exists() else None

    def iter_events(self) -> Iterator[MarketEvent]:
        adapter = DatabentoMBOAdapter(self.resolver, on_invalid=self.on_invalid)
        self.last_adapter = adapter
        return adapter.events(iter_dbn_records(self.path))

    def manifest(self) -> dict[str, Any]:
        return dataset_manifest([self.path, *self.definition_paths])


@dataclass
class GeneratedSource:
    """Deterministic in-memory generator (synthetic MBO scenarios)."""

    factory: Callable[[], Iterable[MarketEvent]]
    spec: dict[str, Any]
    trading_day: date | None = None
    name: str = "synthetic"

    def iter_events(self) -> Iterator[MarketEvent]:
        return iter(self.factory())

    def manifest(self) -> dict[str, Any]:
        return synthetic_manifest(self.spec)


def is_dbn(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".dbn") or name.endswith(".dbn.zst")


def is_definition_file(path: Path) -> bool:
    return "definition" in path.name.lower()


def expand_inputs(inputs: Iterable[str | Path]) -> list[Path]:
    out: list[Path] = []
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            # recursive: Databento batch downloads land in per-job sub-folders
            found = [x for x in p.rglob("*") if x.is_file() and (is_dbn(x) or x.suffix == ".jsonl")]
            out.extend(sorted(found, key=lambda x: (x.name, str(x))))
        elif any(ch in str(raw) for ch in "*?["):
            out.extend(sorted(Path().glob(str(raw))))
        else:
            out.append(p)
    return out


def open_sources(
    inputs: Iterable[str | Path],
    *,
    definitions: Iterable[str | Path] | None = None,
    known_roots: tuple[str, ...] = ("MNQ", "NQ"),
) -> list[EventSource]:
    """Build ordered sources. DBN definition files among the inputs are used for resolution only."""
    paths = expand_inputs(inputs)
    def_paths = [Path(d) for d in (definitions or [])] + [p for p in paths if is_dbn(p) and is_definition_file(p)]
    data_paths = [p for p in paths if not (is_dbn(p) and is_definition_file(p))]
    for p in data_paths:
        if not p.exists():
            raise FileNotFoundError(p)
    sources: list[EventSource] = []
    resolver: ContractResolver | None = None
    for p in data_paths:
        if is_dbn(p):
            if resolver is None:
                if not def_paths:
                    def_paths = sorted(x for x in p.parent.iterdir() if is_dbn(x) and is_definition_file(x))
                if not def_paths:
                    raise FileNotFoundError(
                        f"no Databento 'definition' file found for {p.name}; download it with "
                        "`fabio data download --schema definition ...` or pass --definitions"
                    )
                resolver = ContractResolver.from_definition_files(def_paths, known_roots)
            sources.append(DatabentoSource(p, resolver, definition_paths=list(def_paths)))
        elif p.suffix == ".jsonl":
            sources.append(JsonlSource(p))
        else:
            raise ValueError(f"unsupported input {p}; expected .jsonl or .dbn/.dbn.zst")
    return sources


def chain_events(sources: Iterable[EventSource]) -> Iterator[MarketEvent]:
    for src in sources:
        yield from src.iter_events()


def combined_manifest(sources: list[EventSource]) -> dict[str, Any]:
    import hashlib
    import json

    parts = [s.manifest() for s in sources]
    digest = hashlib.sha256(json.dumps([p["dataset_sha256"] for p in parts]).encode()).hexdigest()
    return {"dataset_sha256": digest, "sources": parts}


__all__ = [
    "DatabentoSource",
    "EventSource",
    "GeneratedSource",
    "JsonlSource",
    "chain_events",
    "combined_manifest",
    "expand_inputs",
    "is_dbn",
    "manifest_path",
    "open_sources",
]
