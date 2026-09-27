"""Canonical schema descriptor and SHA256 for FABIO_MECHANICAL_V1.

The hash is computed from a library-independent *descriptor* of the contract (model and field
names, normalized types, numeric constraints, defaults, enum members), not from pydantic's JSON
Schema output: that output changes between pydantic releases (verified during integration:
2.9 and 2.13 render the same models differently), which would make the "same" contract hash
differently on different installs. The pydantic JSON Schema is still exported for tooling.

``CONTRACT_SCHEMA_SHA256.txt`` pins the expected value; a mismatch means the contract changed
without a deliberate re-pin (and, for breaking changes, a new contract version).
"""

from __future__ import annotations

import hashlib
import json
import types
import typing
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .models import CONTRACT_MODELS, CONTRACT_VERSION

CONTRACTS_DIR = Path(__file__).resolve().parent
PINNED_HASH_FILE = CONTRACTS_DIR / "CONTRACT_SCHEMA_SHA256.txt"
VERSION_FILE = CONTRACTS_DIR / "CONTRACT_VERSION.txt"
BUNDLE_FILE = CONTRACTS_DIR / "schema_bundle.json"

_SCALARS: dict[Any, str] = {int: "int", float: "float", str: "str", bool: "bool", Decimal: "decimal", type(None): "null"}


class SchemaMismatchError(RuntimeError):
    pass


def _type_name(tp: Any, models: dict[str, Any], enums: dict[str, list[str]]) -> str:
    if tp is Any:
        return "any"
    if tp in _SCALARS:
        return _SCALARS[tp]
    if isinstance(tp, type) and issubclass(tp, Enum):
        enums[tp.__name__] = [str(m.value) for m in tp]
        return f"enum:{tp.__name__}"
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        if tp.__name__ not in models:
            models[tp.__name__] = None  # placeholder breaks recursion
            models[tp.__name__] = _describe_model(tp, models, enums)
        return f"model:{tp.__name__}"
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin is typing.Literal:
        return "literal<" + ",".join(sorted(repr(a) for a in args)) + ">"
    if origin in (typing.Union, types.UnionType):
        return "union<" + ",".join(sorted(_type_name(a, models, enums) for a in args)) + ">"
    if origin in (list, tuple, set, frozenset):
        return f"{origin.__name__}<" + ",".join(_type_name(a, models, enums) for a in args) + ">"
    if origin is dict:
        return "dict<" + ",".join(_type_name(a, models, enums) for a in args) + ">"
    raise TypeError(f"unsupported contract type {tp!r}")


def _constraints(field: Any) -> list[str]:
    out = []
    for meta in field.metadata:
        for attr in ("gt", "ge", "lt", "le", "min_length", "max_length"):
            value = getattr(meta, attr, None)
            if value is not None:
                out.append(f"{attr}={value}")
    return sorted(out)


def _default(field: Any) -> str:
    if field.is_required():
        return "<required>"
    if field.default_factory is not None:
        return f"factory:{getattr(field.default_factory, '__name__', 'callable')}"
    d = field.default
    if isinstance(d, Enum):
        return f"enum:{d.value}"
    return repr(d)


def _describe_model(model: type[BaseModel], models: dict[str, Any], enums: dict[str, list[str]]) -> dict[str, Any]:
    fields = []
    for name, field in model.model_fields.items():
        fields.append(
            {
                "name": name,
                "type": _type_name(field.annotation, models, enums),
                "constraints": _constraints(field),
                "default": _default(field),
            }
        )
    return {"fields": fields, "extra": str(model.model_config.get("extra")), "frozen": bool(model.model_config.get("frozen"))}


def contract_descriptor() -> dict[str, Any]:
    models: dict[str, Any] = {}
    enums: dict[str, list[str]] = {}
    for m in CONTRACT_MODELS:
        _type_name(m, models, enums)
    return {
        "contract_version": CONTRACT_VERSION,
        "top_level_models": [m.__name__ for m in CONTRACT_MODELS],
        "models": dict(sorted(models.items())),
        "enums": dict(sorted(enums.items())),
    }


def canonical_schema_bytes() -> bytes:
    return json.dumps(contract_descriptor(), sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_schema_hash() -> str:
    return hashlib.sha256(canonical_schema_bytes()).hexdigest()


def canonical_schema_bundle() -> dict[str, Any]:
    """Descriptor (hashed) plus pydantic JSON Schema (informational; may vary by pydantic version)."""
    return {
        "contract_version": CONTRACT_VERSION,
        "schema_sha256": canonical_schema_hash(),
        "descriptor": contract_descriptor(),
        "json_schema": {m.__name__: m.model_json_schema() for m in CONTRACT_MODELS},
    }


def pinned_schema_hash() -> str:
    return PINNED_HASH_FILE.read_text(encoding="utf-8").strip()


def verify_schema_pin() -> str:
    """Return the canonical hash, raising if it differs from the pinned value or version."""
    current = canonical_schema_hash()
    pinned = pinned_schema_hash()
    if current != pinned:
        raise SchemaMismatchError(f"canonical schema hash {current} != pinned {pinned}")
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if version != CONTRACT_VERSION:
        raise SchemaMismatchError(f"CONTRACT_VERSION.txt {version} != {CONTRACT_VERSION}")
    return current


def export_schema_bundle(path: str | Path = BUNDLE_FILE) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(canonical_schema_bundle(), indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return p


def payload_sha256(model: BaseModel) -> str:
    payload = model.model_dump(mode="json")
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


# Package 1 compatible name.
snapshot_hash = payload_sha256
