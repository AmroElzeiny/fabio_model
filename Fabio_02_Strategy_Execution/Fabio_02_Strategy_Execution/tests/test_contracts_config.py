from pathlib import Path
import json
import pytest
from contracts.models import *
from contracts.schema import schema_sha256, canonical_schema
from fabio.config import load_config, load_invariants, apply_strategy_params, FrozenInvariantError, config_hash
from fabio.fixtures.synthetic import snapshot

def test_contract_version_and_schema_hash_stable_shape():
    assert CONTRACT_VERSION=='FABIO_MECHANICAL_V1'
    assert len(schema_sha256())==64
    assert 'MarketFeatureSnapshot' in canonical_schema()['models']

def test_serialization_roundtrip():
    s=snapshot(1)
    s2=MarketFeatureSnapshot.model_validate_json(s.model_dump_json())
    assert s2==s

def test_config_hash_deterministic():
    c=load_config();assert config_hash(c)==config_hash(dict(c))

def test_optimizer_cannot_mutate_invariants():
    c=load_config()
    with pytest.raises(FrozenInvariantError):apply_strategy_params(c,{'never_widen_stop':False})

def test_invariants_frozen():
    inv=load_invariants();assert inv['frozen'] is True;assert inv['rules']['never_widen_stop'] is True
