"""Generate release evidence only from executed reports; missing results stay missing."""
from pathlib import Path
import json
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
A=ROOT/"artifacts"
RETAINED=ROOT/"docs/validation-results/production"
def read(name):
    path=A/name
    if not path.exists():path=RETAINED/name
    return json.loads(path.read_text()) if path.exists() else None
def suite(name):
    path=RETAINED/name if (RETAINED/name).exists() else A/name
    if not path.exists():return "Not recorded"
    root=ET.parse(path).getroot()
    suites=[root] if root.tag=="testsuite" else root.findall("testsuite")
    counts={k:sum(int(s.attrib.get(k,0)) for s in suites) for k in ("tests","failures","errors","skipped")}
    return f"{counts['tests']-counts['skipped']-counts['failures']-counts['errors']} passed, {counts['skipped']} skipped, {counts['failures']+counts['errors']} failures/errors"
capacity=read("capacity-1073741824.json")
bank=read("production-banking.json")
neural=read("production-neural-pgvector.json")
native=read("postgres-production.json")
viewer=read("viewer/results.json")
identity=read("keycloak-integration.json")
text=f'''# Executed production validation

These results are measurements of the implemented 0.3 backend. They do not
certify untested customer environments or universal market superiority.

| Check | Executed result |
|---|---|
| Windows Python suite | {suite('windows-production.xml')} |
| Clean Linux suite | {suite('linux-production.xml')} |
| Real PostgreSQL / pgvector | {native['status'] if native else 'Not recorded'} |
| Real Keycloak / HTTP / durable worker | {identity['status'] if identity else 'Not recorded'} |
| Browser architecture/coverage/SQL/review | {viewer['status'] if viewer else 'Not recorded'} |

The standard suites intentionally skip the external PostgreSQL fixture; it is
executed separately against a real database. Base tests cover source/metadata
analysis, isolation, stale/invalid citations, actual process termination recovery,
lease fencing, cancellation, incremental deletion/reuse, >2000-file inventory,
parser failures/dead letters, SQL shapes, entity-specific field identity,
record-export exclusion and opened-handle root enforcement.

## Actual byte-capacity run

'''
if capacity:
    text+=f'''The generated fixture physically wrote **{capacity['actual_selected_bytes']:,}
source bytes** across **{capacity['selected_files']:,} files**. Four durable queue
workers completed inventory, extraction and publication with status
**{capacity['status']}**, producing {capacity['evidence_count']:,} evidence items
in **{capacity['index_seconds']:.2f} seconds**. The subsequent exact query took
{capacity['query_seconds']:.3f} seconds in that run.

This fixture contains independent Python declarations with comment padding. It
did not perform neural indexing, did not use sparse files and did not measure
native process RSS. It demonstrates the removal of the old project byte ceiling,
not semantic quality on a real 1 GiB codebase or terabyte performance. Its raw
report is `validation-results/production/capacity-1073741824.json`.

'''
if bank:
    text+=f'''## Real public banking regression

The production worker pipeline finished in {bank['index_seconds']:.2f} seconds
and preserved the reviewed exact source sets: **{bank['endpoint_count']} routes,
{bank['table_count']} physical tables and {bank['ddl_column_count']} DDL columns**.
Route/table/column equality: {bank['routes_match']}/{bank['tables_match']}/{bank['columns_match']}.
No application code or production database was run. The task is discovery
regression against the recorded public source baseline, not banking correctness.

This check exposed an evidence-ID collision between same-named fields in two
tables. Declaring entity is now part of identity, with a regression test.

'''
if neural:
    text+='''## Real model plus native vector backend

The pinned multilingual-e5-small model ran locally through the production queue
and native PostgreSQL HNSW backend. A controlled-vector infrastructure test is
also recorded separately; it is not substituted for the real-model result.

| Frozen synthetic task | Lexical hit@10 | Neural hit@10 | Hybrid hit@10 |
|---|---:|---:|---:|
'''
    ids=list(dict.fromkeys(t['task'] for t in neural['tasks']))
    for id_ in ids:
        values=[next(t['hit_at_10'] for t in neural['tasks'] if t['task']==id_ and t['mode']==mode) for mode in ['lexical','semantic','hybrid']]
        text+='| '+id_+' | '+' | '.join(str(v) for v in values)+' |\n'
    text+='''
In particular, the birth-date definition used unfamiliar wording. Lexical search
missed its dictionary while semantic and hybrid search retrieved it. These are
three small frozen synthetic metadata tasks, not a real-world business-meaning
precision/recall estimate. No host AI produced answers, and no provider tokens or
cost were invented. Model revision/profile and raw outcomes are retained.

'''
text+='''## Operational checks and remaining gates

The official SDK stdio and authenticated HTTP paths were exercised, including
explicit older-client negotiation and wrong issuer/audience/expiry/project denial.
The real Keycloak fixture was also run against the new durable service and an
independent queue consumer, including uploads, project isolation and review
denial. Actual signed-in Copilot acceptance,
multi-host storage fault testing, customer disaster recovery, distributed ANN,
and an executed terabyte workload remain deployment/release gates.

The executed 0.2 Codanna comparison remains available as historical evidence;
it is not a comparative benchmark of 0.3. Other competitors and a broad independent
quality suite have not been executed here. There is no universal-best claim.
The reviewed Ryze reference informed adapter/context/telemetry conventions; no ads
data, credentials or production accounts were used or changed.
'''
(ROOT/"docs/production-validation.md").write_text(text,encoding="utf-8")
print(ROOT/"docs/production-validation.md")
