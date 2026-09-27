"""Run manifest: every run records config hash, code identity, schema hash and dataset hash."""

from __future__ import annotations

import platform
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from fabio_mechanical.configs import config_hash, invariant_hash, search_space_hash
from fabio_mechanical.contracts import CONTRACT_VERSION, verify_schema_pin
from fabio_mechanical.data.manifest import code_identity


def run_manifest(command: str, cfg: dict[str, Any], dataset: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "run_id": uuid.uuid4().hex,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "contract_version": CONTRACT_VERSION,
        "schema_sha256": verify_schema_pin(),
        "config_hash": config_hash(cfg),
        "invariant_hash": invariant_hash(),
        "search_space_sha256": search_space_hash(),
        "config_version": cfg.get("version"),
        "ai_enabled": cfg.get("AI_ENABLED"),
        "experimental": cfg.get("experimental", {}),
        "code": code_identity(),
        "dataset": dataset,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        **(extra or {}),
    }
