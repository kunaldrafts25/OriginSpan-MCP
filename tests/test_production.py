import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from sqlalchemy import select,update
from diamond_mcp.engine import Config,Actor
from diamond_mcp.service import DiscoveryService
from diamond_mcp.catalog import jobs,tasks,files,chunks,LeaseLost
from diamond_mcp.sql_intelligence import analyze_sql

A=Actor("agent",frozenset({"p"}))


def setup(tmp_path,monkeypatch):
    monkeypatch.setenv("DIAMOND_EXTERNAL_WORKERS","true")
    source=tmp_path/"input"
    source.mkdir()
    (source/"api.py").write_text('''class Person:
    """C_ID is an external party reference; no individual records."""
    def find(self,C_ID):
        sql="SELECT p.dob FROM person p WHERE p.id=:id"
        return C_ID
''')
    (source/"schema.sql").write_text("CREATE TABLE person (id INT PRIMARY KEY,dob DATE); CREATE TABLE account (id INT, person_id INT);")
    (source/"dictionary.md").write_text("B7 is the birth date recorded for a person. C_ID can instead mean contract identifier in the contracts module.")
    (source/"compose.yaml").write_text("services:\n  api:\n    depends_on: [database]\n  database:\n    image: postgres\n")
    e=DiscoveryService(Config(tmp_path/"state",(source,)))
    app=e.register(A,"p","People",source)["id"]
    return e,source,app


def drain(e,job):
    for _ in range(100):
        e.pipeline.step("test-worker",job,isolate=False)
        with e.store.engine.connect() as conn:
            state=conn.execute(select(jobs.c.state).where(jobs.c.id==job)).scalar()
        if state not in {"QUEUED","RUNNING","CANCELLING"}:
            return state
    raise AssertionError("Queue did not drain")


def test_durable_recovery_incremental_cache_deletion_and_no_full_scan_search(tmp_path,monkeypatch):
    e,source,app=setup(tmp_path,monkeypatch)
    job=e.start(A,"p",app)["id"]
    # Fresh engine has no original API thread/process. It can finish the job.
    recovered=DiscoveryService(e.config)
    assert drain(recovered,job)=="COMPLETED"
    monkeypatch.setattr(recovered.store,"findings",lambda *a,**k: (_ for _ in ()).throw(AssertionError("whole index loaded")))
    found=recovered.search(A,"p",app,job,"dob","exact")
    assert found["findings"]
    assert recovered.sql_inventory(A,"p",app,job)["queries"]
    ids=recovered.search(A,"p",app,job,"id","exact")["findings"]
    assert {i.get("entity") for i in ids if i["kind"]=="declared_schema_field"}=={"person","account"}
    assert recovered.demographic_context(A,"p",app,job)["classification_status"]=="HYPOTHESES_REQUIRE_REVIEW"
    graph=recovered.architecture(A,"p",app,job)
    assert any(edge["target"]=="database" for edge in graph["edges"]) or "depends_on" in str(graph)
    second=recovered.start(A,"p",app)["id"]
    assert drain(recovered,second)=="COMPLETED"
    coverage=recovered.coverage(A,"p",app,second)["extractor_coverage"]
    assert all(row["reused"] for row in coverage)
    (source/"dictionary.md").unlink()
    third=recovered.start(A,"p",app)["id"]
    assert drain(recovered,third)=="COMPLETED"
    assert not any(i["file"]=="dictionary.md" for i in recovered.search(A,"p",app,third,"B7")["findings"])
    assert recovered.evidence_item(A,"p",app,job,found["findings"][0]["id"])


def test_concurrent_admission_and_fenced_expired_worker(tmp_path,monkeypatch):
    e,_,app=setup(tmp_path,monkeypatch)
    def start():
        try:return e.start(A,"p",app)["id"]
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        admitted=list(pool.map(lambda _:start(),range(6)))
    assert len([j for j in admitted if j])==1
    job=next(j for j in admitted if j)
    first=e.catalog.claim("first",job,lease_seconds=60)
    assert first
    assert e.catalog.claim("second",job) is None
    with e.store.engine.begin() as conn:
        conn.execute(update(tasks).where(tasks.c.id==first["id"]).values(lease_until=time.time()-1))
    replacement=e.catalog.claim("second",job)
    assert replacement["token"]!=first["token"]
    with e.store.engine.begin() as conn:
        with pytest.raises(LeaseLost):e.catalog.fence(conn,first)
    e.pipeline.inventory(replacement)
    assert drain(e,job)=="COMPLETED"


def test_cancelled_leases_cannot_publish_and_foreign_scopes_denied(tmp_path,monkeypatch):
    e,_,app=setup(tmp_path,monkeypatch)
    job=e.start(A,"p",app)["id"]
    task=e.catalog.claim("worker",job)
    assert e.cancel(A,"p",app,job)["status"]=="CANCELLED"
    with e.store.engine.begin() as conn:
        with pytest.raises(LeaseLost):e.catalog.fence(conn,task)
    assert e.app(A,"p",app)["current_analysis"] is None
    with pytest.raises(PermissionError):e.job(Actor("other",frozenset({"q"})),"p",app,job)


def test_no_total_repository_cutoff_and_oversized_coverage(tmp_path,monkeypatch):
    e,source,app=setup(tmp_path,monkeypatch)
    monkeypatch.setattr("diamond_mcp.pipeline.MAX_PARSE_BYTES",1024)
    (source/"large.sql").write_text("-- metadata\n"*300)
    # Inventory more than the old 2000-file ceiling without parsing every fixture.
    for index in range(2005):(source/f"note{index}.txt").write_text("approved metadata")
    job=e.start(A,"p",app)["id"]
    task=e.catalog.claim("scanner",job)
    e.pipeline.inventory(task)
    status=e.job(A,"p",app,job)
    assert status["snapshot"]["files"]>2000
    with e.store.engine.connect() as conn:
        assert conn.execute(select(files.c.state).where(files.c.job==job,files.c.path=="large.sql")).scalar()=="OVERSIZED"
    e.cancel(A,"p",app,job)


def test_schema_aware_insert_view_update_and_wildcards():
    schema={"person":{"id":"INT","dob":"DATE"},"target":{"birth":"DATE"}}
    inserted=analyze_sql("INSERT INTO target (birth) SELECT dob FROM person WHERE id=:id",schema=schema)
    assert inserted["write_targets"]==["target"]
    assert inserted["value_lineage"][0]["output"]=="birth"
    assert inserted["value_lineage"][0]["sources"]==["person.dob"]
    wildcard=analyze_sql("SELECT * FROM person",schema=schema)
    assert set(wildcard["outputs"])=={"id","dob"}
    viewed=analyze_sql("CREATE VIEW birthday AS SELECT dob FROM person",schema=schema)
    assert viewed["value_lineage"] and viewed["write_targets"]==["birthday"]
    mixed=analyze_sql("CREATE TABLE x (id INT); SELECT dob FROM person",schema=schema)
    assert mixed["value_lineage"][0]["sources"]==["person.dob"]


def test_cross_file_schema_binding_and_stale_proposals(tmp_path,monkeypatch):
    e,source,app=setup(tmp_path,monkeypatch)
    job=e.start(A,"p",app)["id"]
    assert drain(e,job)=="COMPLETED"
    table_ref=e.search(A,"p",app,job,"person","exact")["findings"]
    reference=next(i for i in table_ref if i["kind"]=="sql_table_reference" and i["file"]=="api.py")
    graph=e.graph(A,"p",app,job,reference["id"])
    assert any(edge["type"]=="SCHEMA_REFERENCE" for edge in graph["edges"])
    assert any(n["file"]=="schema.sql" for n in graph["nodes"])
    suggestion={"subject":"C_ID","meaning":"External party reference","evidence_ids":[reference["id"]]}
    proposal=e.propose(A,"p",app,job,[suggestion])["ids"][0]
    reviewer=Actor("human",A.projects,frozenset({"read","review"}))
    assert e.review(reviewer,"p",app,proposal,"ACCEPTED")["status"]=="ACCEPTED"
    second=e.start(A,"p",app)["id"]
    assert drain(e,second)=="COMPLETED"
    assert not e.validate(A,"p",app,job,[suggestion])["valid"]
    assert e.suggestions(A,"p",app,job)[0]["status"]=="NEEDS_REVALIDATION"


def test_dead_letter_is_partial_and_cannot_hide_parser_failure(tmp_path,monkeypatch):
    e,source,app=setup(tmp_path,monkeypatch)
    job=e.start(A,"p",app)["id"]
    scanner=e.catalog.claim("scanner",job)
    e.pipeline.inventory(scanner)
    monkeypatch.setattr("diamond_mcp.pipeline.parse_file",lambda *a: (_ for _ in ()).throw(ValueError("parser fixture failed")))
    for _ in range(40):
        e.pipeline.step("failing-worker",job,isolate=False)
        with e.store.engine.begin() as conn:
            conn.execute(update(tasks).where(tasks.c.job==job,tasks.c.state=="PENDING").values(available_at=0))
        if e.job(A,"p",app,job)["status"]=="PARTIAL":break
    assert e.job(A,"p",app,job)["status"]=="PARTIAL"
    coverage=e.coverage(A,"p",app,job)
    assert coverage["dead_letters"]
    assert all(f["state"]=="FAILED" for f in coverage["files"])


def test_killed_process_lease_reclaimed_by_real_worker(tmp_path,monkeypatch):
    import subprocess,sys,os
    e,source,app=setup(tmp_path,monkeypatch)
    job=e.start(A,"p",app)["id"]
    env={**os.environ,"PYTHONPATH":str(Path(__file__).resolve().parents[1]/"src")}
    code="from diamond_mcp.service import DiscoveryService,Config; from pathlib import Path; import sys,time; e=DiscoveryService(Config(Path(sys.argv[1]))); e.catalog.claim('crash-fixture',sys.argv[2]); time.sleep(60)"
    process=subprocess.Popen([sys.executable,"-c",code,str(e.config.state),job],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            with e.store.engine.connect() as conn:
                leased=conn.execute(select(tasks.c.id).where(tasks.c.job==job,tasks.c.state=="RUNNING")).scalar()
            if leased:break
            time.sleep(.05)
        assert leased,"Child did not acquire work"
        process.terminate()
        process.wait(5)
        # Accelerate the clock-dependent lease expiry; replacement execution is
        # the actual independent worker CLI with isolated parser processes.
        with e.store.engine.begin() as conn:
            conn.execute(update(tasks).where(tasks.c.id==leased).values(lease_until=time.time()-1))
        result=subprocess.run([sys.executable,"-m","diamond_mcp","--state",str(e.config.state),"--root",str(source),
            "worker","--analysis",job],env=env,capture_output=True,text=True,timeout=60)
        assert result.returncode==0,result.stderr
        assert e.job(A,"p",app,job)["status"]=="COMPLETED"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(5)
