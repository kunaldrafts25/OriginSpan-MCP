# Executed production validation

These results are measurements of the implemented 0.3 backend. They do not
certify untested customer environments or universal market superiority.

| Check | Executed result |
|---|---|
| Windows Python suite | 31 passed, 1 skipped, 0 failures/errors |
| Clean Linux suite | 31 passed, 1 skipped, 0 failures/errors |
| Real PostgreSQL / pgvector | PASSED |
| Real Keycloak / HTTP / durable worker | PASSED |
| Browser architecture/coverage/SQL/review | PASSED |
| TLS/OIDC production-container rehearsal | PASSED |
| Catalog outage alert and worker recovery | PASSED |
| Isolated catalog/state restore | PASSED |
| Older MCP SDK 1.27.0 client | PASSED |
| Final base-image Python advisory scan | No known vulnerabilities found |

The final readiness suites are `windows-readiness.xml` and
`linux-readiness.xml`; the earlier `*-production.xml` files retain the prior
run. See [deployment readiness](deployment-readiness.md) for the executed
recovery drill, security remediation and customer cutover requirements.

The standard suites intentionally skip the external PostgreSQL fixture; it is
executed separately against a real database. Base tests cover source/metadata
analysis, isolation, stale/invalid citations, actual process termination recovery,
lease fencing, cancellation, incremental deletion/reuse, >2000-file inventory,
parser failures/dead letters, SQL shapes, entity-specific field identity,
record-export exclusion and opened-handle root enforcement.

## Actual byte-capacity run

The generated fixture physically wrote **1,073,741,824
source bytes** across **1,024 files**. Four durable queue
workers completed inventory, extraction and publication with status
**COMPLETED**, producing 7,168 evidence items
in **90.62 seconds**. The subsequent exact query took
0.262 seconds in that run.

This fixture contains independent Python declarations with comment padding. It
did not perform neural indexing, did not use sparse files and did not measure
native process RSS. It demonstrates the removal of the old project byte ceiling,
not semantic quality on a real 1 GiB codebase or terabyte performance. Its raw
report is `validation-results/production/capacity-1073741824.json`.

## Real public banking regression

The production worker pipeline finished in 13.18 seconds
and preserved the reviewed exact source sets: **39 routes,
3 physical tables and 23 DDL columns**.
Route/table/column equality: True/True/True.
No application code or production database was run. The task is discovery
regression against the recorded public source baseline, not banking correctness.

This check exposed an evidence-ID collision between same-named fields in two
tables. Declaring entity is now part of identity, with a regression test.

## Real model plus native vector backend

The pinned multilingual-e5-small model ran locally through the production queue
and native PostgreSQL HNSW backend. A controlled-vector infrastructure test is
also recorded separately; it is not substituted for the real-model result.

| Frozen synthetic task | Lexical hit@10 | Neural hit@10 | Hybrid hit@10 |
|---|---:|---:|---:|
| demographic-birth | False | True | True |
| contract-expiration | True | True | True |
| ambiguous-identifier | True | True | True |

In particular, the birth-date definition used unfamiliar wording. Lexical search
missed its dictionary while semantic and hybrid search retrieved it. These are
three small frozen synthetic metadata tasks, not a real-world business-meaning
precision/recall estimate. No host AI produced answers, and no provider tokens or
cost were invented. Model revision/profile and raw outcomes are retained.

## Operational checks and remaining gates

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
