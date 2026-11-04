"""Executed explicitly against the isolated real pgvector/PostgreSQL fixture."""
import os
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select,text
from diamond_mcp.service import DiscoveryService,Config,Actor
from diamond_mcp.catalog import jobs

URL=os.getenv("DIAMOND_TEST_PG_URL")
pytestmark=pytest.mark.skipif(not URL,reason="Set DIAMOND_TEST_PG_URL for real PostgreSQL integration")

class Embeddings:
    profile={"model":"controlled-adapter-for-storage-test","revision":"1"}
    def encode(self,values,query=False):
        return [[1.0,0.0] if "birth" in v.lower() or "dob" in v.lower() else [0.0,1.0] for v in values]

def test_real_pgvector_leases_native_search_and_physical_project_isolation(tmp_path,monkeypatch):
    monkeypatch.setenv("DIAMOND_PGVECTOR","true")
    monkeypatch.setenv("DIAMOND_EXTERNAL_WORKERS","true")
    source=tmp_path/"input"
    source.mkdir()
    (source/"schema.sql").write_text("CREATE TABLE person (id INT,dob DATE);")
    (source/"dictionary.md").write_text("dob is a date of birth for a person")
    config=Config(tmp_path/"state",(source,),database_url=URL,shared=True)
    e=DiscoveryService(config,Embeddings(),migrate=True)
    # Managed source registration is an operator operation; shared MCP clients
    # still cannot supply arbitrary server paths.
    config.shared=False
    a=Actor("test",frozenset({"native-test"}))
    app=e.register(a,"native-test","Native vectors",source)["id"]
    job=e.start(a,"native-test",app)["id"]
    def step(owner):return e.pipeline.step(owner,job,isolate=False)
    for _ in range(30):
        with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(step,["w1","w2","w3"]))
        if e.job(a,"native-test",app,job)["status"] not in {"RUNNING","QUEUED"}:break
    assert e.job(a,"native-test",app,job)["status"]=="COMPLETED",e.coverage(a,"native-test",app,job)
    published = e.job(a,"native-test",app,job)
    assert type(published["snapshot"]["bytes"]) is int
    json.dumps(published)  # Viewer responses use strict JSON, without default=str.
    result=e.search(a,"native-test",app,job,"birth date","semantic")
    assert result["findings"]
    assert result["neural"]["backend"]=="pgvector-hnsw"
    table=e.retrieval.table("native-test",app,job)
    with e.store.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM pg_indexes WHERE tablename=:table AND indexdef LIKE '%hnsw%'"),dict(table=table)).scalar()==1
    with pytest.raises(PermissionError):e.search(Actor("foreign",frozenset({"different"})),"native-test",app,job,"birth")
    assert e.retrieval.table("other-project",app,job)!=table
