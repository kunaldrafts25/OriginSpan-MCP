"""Publish concise measured results beside the implementation documentation."""
from pathlib import Path
import json
import statistics

ROOT = Path(__file__).resolve().parents[1]
neural = json.loads((ROOT / "artifacts/evaluation-neural.json").read_text())
codanna = json.loads((ROOT / "artifacts/codanna/scored.json").read_text())
tasks = {t["id"]: t for t in json.loads((ROOT / "evaluations/manifest.json").read_text())["tasks"]}
rows = []
for id_ in tasks:
    scores = [next(t for t in neural["tasks"] if t["task"] == id_ and t["mode"] == mode)["relevant_path_recall_at_10"] for mode in ["lexical", "semantic", "hybrid"]]
    comp = next(t for t in codanna["tasks"] if t["task"] == id_)
    rows.append("| " + id_ + " | " + " | ".join(f"{v:.2f}" for v in scores) + " | " + (f"{comp['relevant_path_recall_at_10']:.2f}" if comp["status"] == "SCORED" else comp["status"]) + " |")
text = """# Executed validation

Validated on Windows 11/Python 3.13 and a clean Python 3.13 Linux container.
The base suite passed 16 tests on both platforms, including stdio, signed HTTP
authorization, explicit older-client HTTP negotiation, application isolation,
invalid/stale citations, self-approval denial, disclosure, cancellation, ORM
declarations, recursive YAML and SCIP occurrence parsing.

The separate live Keycloak 26.4.0/PostgreSQL 18.6 run passed token verification,
HTTP MCP, managed upload, persistent analysis, PostgreSQL full text, project
isolation and review denial. The test-only realm is not production configuration.

Headless browser acceptance passed connection, application/snapshot selection,
graph rendering, evidence search, separate human acceptance, value/predicate
lineage, mobile viewport and no uncaught page errors. Screenshots are retained
under `artifacts/viewer/`. A snapshot-placeholder bug found by this check was
fixed before delivery.

Bank of Anthos retained the exact reviewed 39 routes, 3 physical tables and 23
DDL columns; `artifacts/banking-baseline.json` records source-level set equality,
not just matching counts.

## Relevant-file retrieval Recall@10

The frozen ten-task development set was scored by whether top-ten evidence or
symbol matches include the reviewed relevant source paths. This is retrieval
coverage, not business-answer accuracy, citation entailment, or a universal
ranking. The first local neural run used pinned multilingual-e5-small. Codanna
0.17.1 used its default local embedding profile and its semantic-context tool.
Its separate document search was not combined with symbol search, so mixed
dictionary tasks are not a complete comparison of its capabilities.

| Task | DIAMOND lexical | DIAMOND neural | DIAMOND hybrid | Codanna symbol semantic |
|---|---:|---:|---:|---:|
""" + "\n".join(rows) + "\n\n"
text += """Both tools received the same selected source revisions and ten-match limit.
Outputs were retained under a 160 KB cap; their actual context payloads differ.
DIAMOND's raw structured report records latency/bytes and Codanna's raw output
records its own context envelope. Indexing time includes different work; DIAMOND
cold-time tracing adds overhead and its first corpus includes model initialization.
These are reproducible development measurements, not a controlled performance win.

DIAMOND's hybrid ranking missed the transfer target on this set even though its
neural-only search retrieved it. Some clinical/commerce answers were ranked low.
This motivates a held-out evaluation set, result diversification and optional
reranking. There is no claim that neural retrieval or fusion always improves a
query, and all misses remain published.

"""
text += "| Corpus | Evidence | Cold neural index, seconds | Unchanged index, seconds |\n|---|---:|---:|---:|\n"
for name, measurement in neural["measurements"].items():
    text += f"| {name} | {measurement['evidence_count']} | {measurement['cold_seconds']:.2f} | {measurement['unchanged_incremental_seconds']:.2f} |\n"
text += """
Unchanged chunks reused their vectors; structural analysis still reruns. Python
allocation measurements exclude native model RSS. No host AI answered evaluation
questions; provider cost/token use and business-attribute precision/recall are
therefore unmeasured. Optional compiler/LSP reference precision needs a larger
reviewed corpus. No live database or application source was executed.

## Compatibility and comparative coverage

| Surface | Status |
|---|---|
| Official Python SDK 2.3.0 stdio client | Passed |
| Official Python SDK 2.3.0 HTTP client | Passed |
| HTTP protocol 2025-11-25 initialization and tools/list | Passed |
| VS Code Copilot actual signed-in agent session | Not run; configuration supplied |
| Other AI hosts | Not certified |
| Codanna 0.17.1 | Executed on all ten tasks; raw/scored reports retained |
| CodeGraphContext / Serena / semantic-code-mcp | Not run; no comparative score claimed |

The manifest's original competitor statuses are frozen research metadata. The
executed outcome files override them for Codanna. Other competitors require their
own isolated runtimes/services or approved language-server setups; they remain
unmeasured, not scored as failures. The broader comparative release gate remains
open. See release-status.md for additional qualifications.
"""
(ROOT / "docs/validation.md").write_text(text, encoding="utf-8")
print(ROOT / "docs/validation.md")
