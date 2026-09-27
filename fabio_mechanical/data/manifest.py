"""Dataset manifests and hashes, plus code identity for run provenance."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable

PACKAGE_DIR = Path(__file__).resolve().parents[1]


def sha256_file(path: str | Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def dataset_manifest(paths: Iterable[str | Path], *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Content-addressed manifest of the input files (streamed hashing, no full reads into RAM)."""
    files = []
    for p in sorted({Path(x).resolve() for x in paths}, key=lambda x: str(x)):
        files.append({"name": p.name, "path": str(p), "bytes": p.stat().st_size, "sha256": sha256_file(p)})
    body: dict[str, Any] = {"files": [{k: f[k] for k in ("name", "bytes", "sha256")} for f in files]}
    if extra:
        body["extra"] = extra
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return {"dataset_sha256": digest, "files": files, **({"extra": extra} if extra else {})}


def synthetic_manifest(spec: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return {"dataset_sha256": digest, "files": [], "extra": {"synthetic": spec}}


def source_tree_sha256(root: Path = PACKAGE_DIR) -> str:
    """Deterministic hash of every Python source file of the package (code identity without git)."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if rel.startswith(("tests/", ".venv", "build/")) or "/__pycache__/" in f"/{rel}":
            continue
        h.update(rel.encode())
        h.update(b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
        h.update(b"\0")
    return h.hexdigest()


def git_commit(root: Path = PACKAGE_DIR) -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def code_identity() -> dict[str, Any]:
    from fabio_mechanical import __version__

    return {"package_version": __version__, "git_commit": git_commit(), "source_sha256": source_tree_sha256()}
