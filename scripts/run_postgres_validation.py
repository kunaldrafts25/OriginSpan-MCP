"""Run real native-vector tests without printing credentials or DB connection URLs."""
from pathlib import Path
import os
import secrets
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
file=ROOT/".runtime/production.env"
file.parent.mkdir(exist_ok=True)
if not file.exists():
    file.write_text("DIAMOND_PRODUCTION_TEST_PASSWORD="+secrets.token_urlsafe(32)+"\n")
secret=file.read_text().strip().split("=",1)[1]
if "--prepare" in sys.argv:
    print("Retained isolated test credentials in .runtime/production.env")
else:
    env={**os.environ,"DIAMOND_TEST_PG_URL":"postgresql+psycopg://diamond:"+secret+"@127.0.0.1:55434/diamond"}
    code=subprocess.run([sys.executable,"-m","pytest","-q","tests/test_postgres_production.py"],cwd=ROOT,env=env).returncode
    import json
    (ROOT/"artifacts").mkdir(exist_ok=True)
    (ROOT/"artifacts/postgres-production.json").write_text(json.dumps(dict(status="PASSED" if code==0 else "FAILED",
        database="PostgreSQL 16 / pgvector 0.8.6",checks=["schema migration","concurrent claims","HNSW index","native vector retrieval","project isolation"],
        adapter="controlled vectors for infrastructure test; real model checked separately"),indent=2))
    raise SystemExit(code)
