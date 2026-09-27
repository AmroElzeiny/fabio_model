from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from fabio.config import config_hash

class OOSLockError(RuntimeError): pass
@dataclass(frozen=True)
class LockManifest:
    phase:str; config_hash:str; code_commit:str; schema_hash:str

def freeze_for_oos(path,config,code_commit,schema_hash):
    m=LockManifest('LOCKED_OOS',config_hash(config),code_commit,schema_hash);Path(path).write_text(json.dumps(m.__dict__,indent=2),encoding='utf-8');return m

def verify_oos_lock(path,config):
    d=json.loads(Path(path).read_text(encoding='utf-8'))
    if d['phase']!='LOCKED_OOS' or d['config_hash']!=config_hash(config):raise OOSLockError('OOS config differs from frozen manifest')
    return True

def assert_optimization_allowed(phase:str):
    if phase.upper() in {'LOCKED_OOS','OOS'}:raise OOSLockError('Optimization is forbidden on locked OOS')
