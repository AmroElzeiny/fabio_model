from __future__ import annotations
import csv, json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path

def _default(x):
    if isinstance(x,Decimal):return str(x)
    if is_dataclass(x):return asdict(x)
    if hasattr(x,'value'):return x.value
    return str(x)

def export_json(path,obj):Path(path).write_text(json.dumps(obj,indent=2,default=_default),encoding='utf-8')
def export_ledger_csv(path,ledger):
    rows=[asdict(x) if is_dataclass(x) else x for x in ledger]
    if not rows:Path(path).write_text('',encoding='utf-8');return
    with Path(path).open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0].keys()));w.writeheader();w.writerows([{k:_default(v) for k,v in r.items()} for r in rows])
