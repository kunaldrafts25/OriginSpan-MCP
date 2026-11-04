"""Real pinned local-model checks on a held-out synthetic metadata fixture.

Uses the production queue, immutable blobs, parser processes and persisted
vectors. Model identity and retrieval outcomes are recorded, not inferred.
"""
from pathlib import Path
import argparse
import json
import os
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from diamond_mcp.service import DiscoveryService,Config,Actor

TASKS=[
    dict(id="demographic-birth",query="date of birth of a person",expected="party_dictionary.md"),
    dict(id="contract-expiration",query="when is it too late to extend an agreement",expected="agreement_dictionary.md"),
    dict(id="ambiguous-identifier",query="C_ID",expected="schema.sql")
]


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--model",required=True)
    p.add_argument("--postgres",action="store_true",help="Use the isolated real pgvector validation database")
    args=p.parse_args()
    base=ROOT/".runtime/neural"/uuid.uuid4().hex[:8]
    source=base/"source"
    source.mkdir(parents=True)
    (source/"party_dictionary.md").write_text("# Party vocabulary\nB7 is the calendar day on which an individual was born. It is not account creation time.\nC_ID denotes an external party reference.\n")
    (source/"agreement_dictionary.md").write_text("# Agreement vocabulary\nX17 is the last permissible day to renew a commercial agreement. C_ID denotes a contract identifier here.\n")
    (source/"schema.sql").write_text("CREATE TABLE party (C_ID INT PRIMARY KEY,B7 DATE); CREATE TABLE agreement (C_ID INT PRIMARY KEY,X17 DATE);")
    (source/"api.py").write_text('def find(cid):\n query="SELECT p.B7 FROM party p WHERE p.C_ID=:id"\n return cid\n')
    (base/"frozen-tasks.json").write_text(json.dumps(TASKS,indent=2))
    database=None
    if args.postgres:
        secret=(ROOT/".runtime/production.env").read_text().strip().split("=",1)[1]
        database="postgresql+psycopg://diamond:"+secret+"@127.0.0.1:55434/diamond"
        os.environ["DIAMOND_PGVECTOR"]="true"
    e=DiscoveryService(Config(base/"state",(source,),database_url=database,model_path=str(Path(args.model).resolve())),migrate=True)
    a=Actor("neural-validation",frozenset({"neural"}))
    app=e.register(a,"neural","Unfamiliar vocabulary",source)["id"]
    started=time.perf_counter()
    job=e.start(a,"neural",app,background=False)
    if job["status"]!="COMPLETED":
        print(json.dumps(e.coverage(a,"neural",app,job["id"]),indent=2))
        raise SystemExit("Neural indexing failed")
    report=dict(status="COMPLETED",backend=job["neural"]["backend"],profile=e.embeddings.profile,index_seconds=time.perf_counter()-started,tasks=[])
    for task in TASKS:
        for mode in ("lexical","semantic","hybrid"):
            started=time.perf_counter()
            result=e.search(a,"neural",app,job["id"],task["query"],mode)
            paths=[r["file"] for r in result["findings"]]
            report["tasks"].append(dict(task=task["id"],mode=mode,expected_file=task["expected"],hit_at_10=task["expected"] in paths,
                files=paths,seconds=time.perf_counter()-started))
    report["sql_inventory"]=e.sql_inventory(a,"neural",app,job["id"])
    report["limitations"]=["Synthetic held-out metadata fixture; not a universal benchmark","Host-AI answer correctness not evaluated"]
    (ROOT/"artifacts").mkdir(exist_ok=True)
    (ROOT/("artifacts/production-neural-pgvector.json" if args.postgres else "artifacts/production-neural.json")).write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!="sql_inventory"},indent=2))


if __name__=="__main__":main()
