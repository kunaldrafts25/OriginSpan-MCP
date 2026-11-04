"""Exact source-ground-truth regression through the production worker pipeline."""
from pathlib import Path
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import sys
import time
import uuid
from sqlalchemy import select
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from diamond_mcp.service import DiscoveryService,Config,Actor
from diamond_mcp.catalog import units,chunks

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("repository",type=Path)
    args=parser.parse_args()
    source=args.repository.resolve(strict=True)
    base=ROOT/".runtime/banking"/uuid.uuid4().hex[:8]
    e=DiscoveryService(Config(base,(source,)))
    actor=Actor("banking-validation",frozenset({"bank"}))
    app=e.register(actor,"bank","Bank of Anthos",source)["id"]
    # No embedded thread: exercise independent concurrent queue consumers.
    import os
    os.environ["DIAMOND_EXTERNAL_WORKERS"]="true"
    job=e.start(actor,"bank",app)["id"]
    started=time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(e.pipeline.run,"banking-"+str(n),job) for n in range(4)]
        for f in futures:f.result()
    ground=json.loads((ROOT/"evaluations/banking-ground-truth.json").read_text())
    routes=set()
    tables=set()
    fields=set()
    with e.store.engine.connect() as conn:
        for raw in conn.execute(select(units.c.payload).where(units.c.job==job,units.c.kind=="architecture")).scalars():
            value=json.loads(raw)
            if value["type"]=="API_ENDPOINT":routes.add(value["file"]+" :: "+value["name"])
        for raw in conn.execute(select(chunks.c.payload).where(chunks.c.job==job,chunks.c.kind=="declared_schema_field")).scalars():
            value=json.loads(raw)
            fields.add((value["entity"].casefold(),value["name"].casefold()))
            tables.add(value["entity"].casefold())
    expected={f+" :: "+r for f,rs in ground["declared_routes"].items() for r in rs}
    expected_fields={(table,name) for table,names in ground["ddl_fields"].items() for name in names}
    report=dict(status=e.job(actor,"bank",app,job)["status"],index_seconds=time.perf_counter()-started,
        repository=ground["repository"],reviewed_revision=ground["revision"],
        endpoint_count=len(routes),table_count=len(tables),ddl_column_count=len(fields),
        routes_match=routes==expected,tables_match=tables==set(ground["physical_tables"]),columns_match=fields==expected_fields,
        missing_routes=sorted(expected-routes),unexpected_routes=sorted(routes-expected),
        missing_columns=sorted(expected_fields-fields),unexpected_columns=sorted(fields-expected_fields),
        application_id=app,analysis_id=job,source_bytes=e.job(actor,"bank",app,job)["snapshot"]["bytes"])
    (ROOT/"artifacts").mkdir(exist_ok=True)
    (ROOT/"artifacts/production-banking.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    raise SystemExit(0 if report["status"]=="COMPLETED" and all(report[k] for k in ["routes_match","tables_match","columns_match"]) else 1)

if __name__=="__main__":main()
