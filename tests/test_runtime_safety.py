from pathlib import Path
import json
import pytest
from sqlalchemy import select,update
from diamond_mcp.engine import Config,Actor
from diamond_mcp.service import DiscoveryService
from diamond_mcp.catalog import files,tasks,jobs,CapacityExceeded
from diamond_mcp.pipeline import BlobStore

A=Actor("agent",frozenset({"p"}))

def test_client_code_never_runs_and_unknown_records_are_not_opened(tmp_path,monkeypatch):
    source=tmp_path/"source"
    source.mkdir()
    marker=tmp_path/"executed.txt"
    (source/"mcp.py").write_text("raise RuntimeError('not a runtime dependency')")
    (source/"app.py").write_text("from pathlib import Path\nPath("+repr(str(marker))+").write_text('executed')\nraise SystemExit\n")
    (source/"records.csv").write_text("private-records-must-not-be-opened")
    (source/"unknown.legacy").write_text("unsupported content")
    e=DiscoveryService(Config(tmp_path/"state",(source,)))
    monkeypatch.setenv("DIAMOND_EXTERNAL_WORKERS","true")
    app=e.register(A,"p","Safety",source)["id"]
    job=e.start(A,"p",app)["id"]
    original=e.pipeline.blobs.capture
    def capture(path,*args,**kwargs):
        assert Path(path).suffix not in {".csv",".legacy"}
        return original(path,*args,**kwargs)
    monkeypatch.setattr(e.pipeline.blobs,"capture",capture)
    for _ in range(30):
        e.pipeline.step("test",job,isolate=False)
        if e.job(A,"p",app,job)["status"]=="COMPLETED":break
    assert e.job(A,"p",app,job)["status"]=="COMPLETED"
    assert not marker.exists()
    states={f["path"]:f["state"] for f in e.coverage(A,"p",app,job)["files"]}
    assert states["records.csv"]=="RECORD_DATA_EXCLUDED"
    assert states["unknown.legacy"]=="UNSUPPORTED"


def test_opened_handle_cannot_escape_approved_root(tmp_path):
    approved=tmp_path/"approved"
    approved.mkdir()
    other=tmp_path/"outside.py"
    other.write_text("private")
    blobs=BlobStore(tmp_path/"state")
    with pytest.raises(PermissionError):blobs.capture(other,allowed_root=approved)


def test_project_capacity_is_enforced_before_source_work(tmp_path,monkeypatch):
    source=tmp_path/"input"
    source.mkdir()
    (source/"app.py").write_text("def example(): pass")
    monkeypatch.setenv("DIAMOND_EXTERNAL_WORKERS","true")
    monkeypatch.setenv("DIAMOND_MAX_ACTIVE_ANALYSES","1")
    e=DiscoveryService(Config(tmp_path/"state",(source,)))
    one=e.register(A,"p","One",source)["id"]
    two=e.register(A,"p","Two",source)["id"]
    e.start(A,"p",one)
    with pytest.raises(CapacityExceeded):e.start(A,"p",two)


def test_finalize_commit_window_recovers_without_specific_job_argument(tmp_path,monkeypatch):
    source=tmp_path/"input"
    source.mkdir()
    (source/"app.py").write_text("def example(): pass")
    e=DiscoveryService(Config(tmp_path/"state",(source,)))
    monkeypatch.setenv("DIAMOND_EXTERNAL_WORKERS","true")
    app=e.register(A,"p","Recovery",source)["id"]
    job=e.start(A,"p",app)["id"]
    scanner=e.catalog.claim("scanner",job)
    e.pipeline.inventory(scanner)
    extraction=e.catalog.claim("extractor",job)
    e.pipeline.extract(extraction,isolate=False)
    # No reconcile call: simulate termination immediately after the extraction
    # transaction committed, before scheduling publication.
    restarted=DiscoveryService(e.config)
    for _ in range(10):
        restarted.pipeline.step("recovery",isolate=False)
        if restarted.job(A,"p",app,job)["status"]=="COMPLETED":break
    assert restarted.job(A,"p",app,job)["status"]=="COMPLETED"
