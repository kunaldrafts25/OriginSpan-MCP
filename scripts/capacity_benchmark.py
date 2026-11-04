"""Actual allocated-byte capacity exercise, not sparse-file extrapolation.

Runs through the production inventory/extraction/publication path. Semantic-model
and comparative quality benchmarks are separate; byte capacity is not quality.
"""
from pathlib import Path
import argparse
import json
import os
import sys
import time
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from diamond_mcp.service import DiscoveryService,Config,Actor


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--bytes",type=int,default=64*1024**2)
    p.add_argument("--files",type=int,default=128)
    p.add_argument("--workers",type=int,default=4)
    args=p.parse_args()
    if not 1<=args.files<=1000000 or not 1<=args.workers<=16 or args.bytes<args.files*100:
        raise SystemExit("Invalid capacity fixture arguments")
    base=ROOT/".runtime/capacity"/uuid.uuid4().hex[:8]
    source=base/"source"
    source.mkdir(parents=True)
    started=time.perf_counter()
    remaining=args.bytes
    for index in range(args.files):
        size=remaining//(args.files-index)
        code=f'class Entity{index}:\n    """Approved metadata capacity fixture."""\n    def lookup(self, external_reference): return external_reference\n#'.encode()
        with (source/f"entity_{index:07}.py").open("wb") as stream:
            stream.write(code)
            stream.write(b"x"*(size-len(code)))
        remaining-=size
    generation=time.perf_counter()-started
    os.environ["DIAMOND_EXTERNAL_WORKERS"]="true"
    engine=DiscoveryService(Config(base/"state",(source,)))
    a=Actor("capacity",frozenset({"capacity"}))
    app=engine.register(a,"capacity","Capacity fixture",source)["id"]
    job=engine.start(a,"capacity",app)["id"]
    stop=threading.Event()
    def worker(number):
        engine.pipeline.run("capacity-"+str(number),job,stop)
    started=time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(worker,n) for n in range(args.workers)]
        previous=None
        while not all(f.done() for f in futures):
            status=engine.job(a,"capacity",app,job)
            summary=(status["snapshot"]["files"],status["tasks"],status["status"])
            if summary!=previous:
                print(json.dumps({"captured_files":summary[0],"tasks":summary[1],"status":summary[2]}),flush=True)
                previous=summary
            time.sleep(3)
        for future in futures:future.result()
    elapsed=time.perf_counter()-started
    status=engine.job(a,"capacity",app,job)
    query_start=time.perf_counter()
    results=engine.search(a,"capacity",app,job,"external_reference","exact")
    query_time=time.perf_counter()-query_start
    report=dict(status=status["status"],requested_bytes=args.bytes,actual_selected_bytes=status["snapshot"]["bytes"],
        selected_files=status["snapshot"]["files"],evidence_count=status["evidence_count"],workers=args.workers,
        generation_seconds=generation,index_seconds=elapsed,query_seconds=query_time,query_results=len(results["findings"]),
        semantic_indexing=False,sparse_files=False,fixture="Repeated independent Python source declarations plus comment padding",
        limitations=["Capacity fixture is not a real-world quality or language-mix benchmark", "No terabyte throughput claim", "Process RSS not measured in this harness"])
    (ROOT/"artifacts").mkdir(exist_ok=True)
    destination=ROOT/"artifacts"/f"capacity-{args.bytes}.json"
    destination.write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2),flush=True)
    raise SystemExit(0 if status["status"]=="COMPLETED" and status["snapshot"]["bytes"]==args.bytes else 1)


if __name__=="__main__":main()
