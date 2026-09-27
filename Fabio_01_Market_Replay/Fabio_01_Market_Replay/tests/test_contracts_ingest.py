from decimal import Decimal
from pathlib import Path
import json

from fabio_market_replay.contracts import CONTRACT_VERSION, MarketFeatureSnapshot, canonical_schema_bundle, canonical_schema_hash, Side
from fabio_market_replay.ingest.databento_adapter import DatabentoDownloader, price_nanos_to_ticks, trades_record_to_trade
from fabio_market_replay.orderbook.mbp10 import MBP10Book


def test_contract_version_and_stable_hash():
    assert CONTRACT_VERSION == "FABIO_MECHANICAL_V1"
    h=canonical_schema_hash()
    assert len(h)==64 and h == canonical_schema_hash()
    bundle=canonical_schema_bundle()
    assert "MarketFeatureSnapshot" in bundle["models"]


def test_price_conversion_and_provider_side_fallback():
    assert price_nanos_to_ticks(20_000_000_000_000, Decimal("0.25")) == 80000
    rec={"ts_event":1,"sequence":2,"price":20_000_250_000_000,"size":3,"side":"N"}
    t=trades_record_to_trade(rec,instrument="NQ",active_contract="NQH6",best_bid_ticks=80000,best_ask_ticks=80001)
    assert t.price_ticks == 80001 and t.aggressor_side == Side.BID and t.initiator_source == "quote_fallback"


def test_mbp10_fallback_book():
    rec={"ts_event":1,"sequence":2,
         "bid_px_00":20_000_000_000_000,"bid_sz_00":12,"bid_ct_00":3,
         "ask_px_00":20_000_250_000_000,"ask_sz_00":8,"ask_ct_00":2}
    b=MBP10Book(); b.apply_record(rec,instrument="NQ",active_contract="NQH6")
    s=b.snapshot()
    assert s.best_bid.price_ticks==80000 and s.best_ask.price_ticks==80001
    assert s.queue_metrics["queue_available"] == 0.0


def test_corrupted_manifest_detection(tmp_path: Path):
    p=tmp_path/"x.dbn.zst"; p.write_bytes(b"abc")
    import hashlib
    manifest={"bytes":3,"sha256":hashlib.sha256(b"abc").hexdigest()}
    p.with_suffix(p.suffix+".manifest.json").write_text(json.dumps(manifest))
    assert DatabentoDownloader.validate_manifest(p)
    p.write_bytes(b"abcd")
    assert not DatabentoDownloader.validate_manifest(p)


class FakeMetadata:
    def get_cost(self, **kwargs): return 1.25
    def get_billable_size(self, **kwargs): return 1000
    def get_record_count(self, **kwargs): return 50
class FakeClient:
    metadata=FakeMetadata()

def test_cost_estimate_is_available_without_download():
    d=DatabentoDownloader(client=FakeClient())
    x=d.cost_estimate(symbols="NQ.v.0",schema="mbo",start="2026-01-01",end="2026-01-02")
    assert x["estimated_cost_usd"]==1.25 and x["record_count"]==50

class FakeBatch:
    def __init__(self, root): self.root=Path(root); self.submits=0
    def submit_job(self, **kwargs): self.submits+=1; return {"id":"JOB1"}
    def list_jobs(self, state): return [{"id":"JOB1"}] if state=="done" else []
    def download(self, job_id, output_dir):
        p=Path(output_dir)/"x.dbn.zst"; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b"abc"); return [p]
    def list_files(self, job_id):
        import hashlib
        return [{"filename":"x.dbn.zst","hash":"sha256:"+hashlib.sha256(b"abc").hexdigest()}]
class FakeClientBatch(FakeClient):
    def __init__(self, root): self.batch=FakeBatch(root); self.metadata=FakeMetadata()

def test_batch_resume_request_fingerprint(tmp_path: Path):
    c=FakeClientBatch(tmp_path); d=DatabentoDownloader(client=c); state=tmp_path/"state.json"
    paths=d.download_batch(symbols="NQ.v.0",schema="mbo",start="2026-01-01",end="2026-01-02",output_dir=tmp_path/"out",state_file=state,poll_seconds=0)
    assert paths and c.batch.submits == 1
    d.download_batch(symbols="NQ.v.0",schema="mbo",start="2026-01-01",end="2026-01-02",output_dir=tmp_path/"out",state_file=state,poll_seconds=0)
    assert c.batch.submits == 1
