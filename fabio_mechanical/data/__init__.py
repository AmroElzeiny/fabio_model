"""Data layer: instrument reference data, dataset manifests. Raw files live in data/raw (immutable)."""

from .instruments import InstrumentSpec, instrument_root, instrument_spec
from .manifest import code_identity, dataset_manifest, sha256_file, source_tree_sha256, synthetic_manifest

__all__ = [
    "InstrumentSpec",
    "code_identity",
    "dataset_manifest",
    "instrument_root",
    "instrument_spec",
    "sha256_file",
    "source_tree_sha256",
    "synthetic_manifest",
]
