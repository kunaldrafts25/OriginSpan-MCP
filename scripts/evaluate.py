"""Reproducible retrieval evaluation. Never claims host-AI answer quality.

Usage: python scripts/evaluate.py [--model approved/local/model]
Expectations are frozen in evaluations/manifest.json before retrieval runs.
"""
import argparse
import json
from pathlib import Path
import platform
import sys
from time import perf_counter
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from diamond_mcp.engine import Engine, Actor, Config
from diamond_mcp.store import uid, now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    args = parser.parse_args()
    manifest_path = ROOT / "evaluations/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    import hashlib
    report = dict(at=now(), manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        platform=platform.platform(), python=sys.version, host_model=None, host_client=None,
        host_answer_evaluation="NOT_RUN; no host model invoked", cost=None, token_usage=None,
        measurements={}, competitors=manifest["competitors"], tasks=[])
    state = ROOT / ".diamond" / "eval" / uid()[:8]
    sources = {s["id"]: ROOT / ".diamond/benchmarks" / s["id"] for s in manifest["sources"]}
    sources["ambiguity"] = ROOT / "evaluations/fixtures/ambiguous"
    actor = Actor("evaluation", frozenset({"benchmark"}))
    engine = Engine(Config(state, tuple(sources.values()), model_path=args.model))
    for source_id, source_path in sources.items():
        if not source_path.is_dir():
            report["measurements"][source_id] = dict(status="NOT_RUN", reason="Run prepare_benchmarks.py first")
            continue
        app = engine.register(actor, "benchmark", source_id, source_path)["id"]
        tracemalloc.start()
        started = perf_counter()
        job = engine.start(actor, "benchmark", app, background=False)
        cold = perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        if job["status"] != "COMPLETED":
            report["measurements"][source_id] = dict(status="FAILED", job=job)
            continue
        started = perf_counter()
        incremental = engine.start(actor, "benchmark", app, background=False)
        incremental_seconds = perf_counter() - started
        analysis = incremental["id"]
        measurement = dict(status=incremental["status"], cold_seconds=cold,
            unchanged_incremental_seconds=incremental_seconds, python_peak_allocated_bytes=peak,
            process_peak_rss="NOT_MEASURED; Python allocations exclude native model memory",
            evidence_count=job["evidence_count"], snapshot=job["snapshot"], neural=incremental.get("neural"),
            structure_reparsed=True, model_memory="not measured separately")
        report["measurements"][source_id] = measurement
        if source_id == "bank":
            structure = engine.store.get("benchmark", app, "structural", analysis)
            endpoints = [n for n in structure["nodes"] if n["type"] == "API_ENDPOINT"]
            tables = {t for q in structure["sql"] for t in q["tables"]}
            fields = {(f["table"].casefold(), f["name"].casefold()) for q in structure["sql"] for f in q["schema_fields"]}
            measurement["banking_gate"] = dict(endpoints=len(endpoints), tables=len(tables), columns=len(fields),
                passed=(len(endpoints), len(tables), len(fields)) == (39, 3, 23))
            ground = json.loads((ROOT / "evaluations/banking-ground-truth.json").read_text())
            expected_routes = {file + " :: " + route for file, routes in ground["declared_routes"].items() for route in routes}
            actual_routes = {n["evidence"]["file"] + " :: " + n["name"] for n in endpoints}
            expected_fields = {(table.casefold(), field.casefold()) for table, names in ground["ddl_fields"].items() for field in names}
            measurement["banking_gate"]["exact_sources_match"] = (expected_routes == actual_routes and fields == expected_fields and tables == set(ground["physical_tables"]))
        for task in [t for t in manifest["tasks"] if t["source"] == source_id]:
            for mode in ["lexical", "hybrid"] + (["semantic"] if args.model else []):
                started = perf_counter()
                result = engine.search(actor, "benchmark", app, analysis, task["query"], mode=mode)
                elapsed = perf_counter() - started
                files = [item["file"] for item in result["findings"]]
                expected = set(task["relevant_paths"])
                hits = expected.intersection(files)
                first = next((rank for rank, file in enumerate(files, 1) if file in expected), None)
                report["tasks"].append(dict(task=task["id"], source=source_id, mode=mode,
                    relevant_path_recall_at_10=len(hits)/len(expected), reciprocal_rank=1/first if first else 0,
                    returned_bytes=len(json.dumps(result).encode()), latency_seconds=elapsed, tool_calls=1,
                    paths=files, evidence_ids=[i["id"] for i in result["findings"]],
                    business_interpretation="NOT_EVALUATED"))
    report["disk_bytes"] = sum(p.stat().st_size for p in state.rglob("*") if p.is_file())
    report["limitations"] = ["Small fixed task set is not a universal ranking", "Python allocations omit native model RSS",
        "No host-AI answers, provider tokens or provider costs measured", "Compiler-reference precision requires separate reviewed fixtures",
        "Competitor statuses must be replaced only by recorded executed results"]
    out = ROOT / "artifacts"
    out.mkdir(exist_ok=True)
    dest = out / ("evaluation-neural.json" if args.model else "evaluation-base.json")
    dest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(dest)
    print(json.dumps(report["measurements"], indent=2))


if __name__ == "__main__":
    main()
