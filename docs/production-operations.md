# Production deployment and recovery

The MCP transport, catalog and workers are distinct components. New analyses do
not depend on an HTTP request or a single API process staying alive. PostgreSQL
provides shared queue/catalog state; workers require the same managed source,
content and model paths. SQLite is the single-host compatibility tier.

## Install, migrate, start

Install Python 3.12+ in a clean virtual environment with `pip install -e
'.[postgres,semantic]'`. The semantic extra is optional for metadata-only lexical
operation. Download an approved model explicitly using `originspan model-download`;
source is never sent to an embedding API. `ORIGINSPAN_NEURAL_ENABLED=false` disables
automatic loading of an installed local model.

Configure `ORIGINSPAN_DATABASE_URL`, `ORIGINSPAN_STATE`, `ORIGINSPAN_TRUSTED_ROOTS`, and
customer OIDC issuer/JWKS/resource URL. Provision pgvector as the database owner;
run the application as a separate role with only its required schema privileges.
Use HTTPS termination and the exact configured audience/host. Tokens need
`originspan:read`, project grants, and appropriate analyze/propose/review scopes.

```text
originspan migrate
originspan serve --transport http --host 0.0.0.0
originspan worker --name worker-1
originspan worker --name worker-2
```

`infrastructure/compose.production.yaml` supplies database provisioning, separate
app credentials, a one-shot migration, API and scalable workers. Source volumes
are read-only. Generate distinct URL-safe database owner/application passwords,
provide actual customer IdP endpoints and source paths, then build the image and
run Compose. Do not run the loopback Keycloak test realm as a production IdP.
The tested `compose.edge.yaml` overlay supplies a non-root TLS proxy with
container-DNS refresh and writable temporary paths. Configure
`ORIGINSPAN_PUBLIC_HOST`, `ORIGINSPAN_TLS_DIR` (containing `fullchain.pem` and
`privkey.pem`) and, optionally, `ORIGINSPAN_TLS_BIND`/`ORIGINSPAN_TLS_PORT`.
The resource audience must match the external scheme, hostname and port.

```text
docker build --target production -t originspan-mcp:0.3.0 .
docker compose -f infrastructure/compose.production.yaml -f infrastructure/compose.edge.yaml up -d --scale worker=4
```

For local neural inference inside containers, build with
`--build-arg ORIGINSPAN_EXTRAS=postgres,semantic`, mount the approved model at the
same path in API and worker containers, and set `ORIGINSPAN_MODEL_PATH` consistently.
Weights are not embedded in the base image. API model warmup is asynchronous;
tools report WARMING rather than silently calling another provider.

## Large managed repositories

Register server-mounted sources using the trusted operator CLI; a remote MCP
client cannot gain host filesystem access by supplying a path. For example, run
the CLI against the shared catalog with `--root /sources register NAME --path
/sources/application`. Local stdio also supports approved paths. The small ZIP
upload remains a convenience import with a 50 MB archive bound. Large projects
use managed source volumes; this release does not provide arbitrary-size remote
ZIP uploads, private GitHub OAuth onboarding or an S3 source adapter.

Repository inventory has no fixed 50 MB/2000-file project ceiling. It streams
directory entries and captures selected files in 1 MiB blocks into hash-sharded
blob storage. Unchanged files reuse extraction/model cache. Unsupported and
record-export paths are inventoried without opening their contents. Files above
the native parser budget are captured and visibly marked OVERSIZED; they are not
silently treated as successfully analyzed. Source snapshots are per-file content
captures, not transactional checkouts of a concurrently changing filesystem.

Important budgets:

| Setting | Default / behavior |
|---|---|
| `ORIGINSPAN_MAX_ACTIVE_ANALYSES` | 8 active analyses per project; retryable BUSY response |
| `ORIGINSPAN_SOURCE_QUOTA_BYTES` | 0 = operator has not set a source-byte quota |
| `ORIGINSPAN_MIN_FREE_BYTES` | 256 MiB local; production Compose sets 1 GiB |
| `ORIGINSPAN_MAX_PARSE_BYTES` | 8 MiB per artifact; oversized coverage is explicit |
| `ORIGINSPAN_PARSE_TIMEOUT` | 90 seconds per parser request; pool is terminated on failure |
| `ORIGINSPAN_PARSER_MEMORY_BYTES` | 2 GiB address-space limit on Linux parser processes |
| Queue lease | 60 seconds, heartbeats every 10 seconds; shared leases use DB clock |
| Retry/dead letter | Maximum three attempts; bounded exponential backoff |
| Retrieval window | Bounded candidates and pages, not the entire index |
| MCP output | 160 KB per result |

The Windows parser process boundary is tested; Linux RLIMIT_AS does not apply on
Windows. Use container/job-level memory limits in a Windows production deployment.
Native SCIP imports retain their bounded occurrence-reader constraints and do not
make arbitrary giant compiler indexes automatically understood.

## Failure and cancellation

Worker termination leaves a lease. Another worker reclaims it after expiry and
uses a new fencing token. Expired/cancelled workers cannot publish results.
Temporary database connection failures trigger bounded worker retries without
logging SQL, connection strings or exception bodies. Completion is transactional
and a restarted worker recovers the commit/finalize
window. Failed files/dead letters are visible; eligible partial results are
published as PARTIAL rather than represented as complete extraction.

`cancel_analysis` revokes pending work. Parser process deadlines and heartbeat
fencing bound recovery; current neural batches finish before their next boundary.
An explicit local `analyze --recover` marks old v0.2 process-owned jobs interrupted;
new durable jobs resume automatically without being restarted from scratch.

Do not delete content blobs, manifests or cache rows while analyses may reference
them. A privileged retention/GC workflow is not implemented in this release.
Back up PostgreSQL and the complete state/blob volume together. A full `pg_dump`
includes physical per-snapshot vector tables and indexes. Pause all API/CLI
writers and workers while capturing the catalog and state checkpoint. Provision
the same application role before restoring; restore as the database owner with
dumped ownership preserved. Grants alone do not permit index initialization.
Restore into an isolated database/storage pair at the original container paths,
run `originspan migrate`, then start workers and validate a
representative analysis before switching traffic. Keep database-owner credentials
out of API/worker environments.

## Monitoring and security

`/healthz` reports liveness and `/readyz` checks catalog connectivity. They expose
no source or tenant counts. `/ops/metrics` requires an OIDC `originspan:operate`
scope. Tool calls carry correlation IDs; optional JSON logs contain operation,
status and ID only. OpenTelemetry spans/counters/histograms are instrumented via
the API; configure an SDK/exporter in the deployment to export them. The included
Prometheus endpoint reports process-local tool counts/duration totals and a
`diamond_catalog_available` gauge. Alert on scrape failures and a zero gauge.
Monitoring credentials need `originspan:operate` and managed token renewal.

The [deployment rehearsal](deployment-readiness.md) exercised TLS/OIDC, an actual
catalog outage/alert, recovery by the same workers, and a separate database/state
restore. Its generated monitoring configuration is a local test fixture;
configure customer notification routing, credentials and storage independently.

Separate human reviewers approve hypotheses; MCP exposes no approval tool.
Excerpts require application opt-in. Known secrets are filtered; automated
filtering cannot guarantee every sensitive value is removed from approved input.
Use clean trusted runtime environments. Containers invoke Python with `-I`;
local IDE configuration uses the absolute trusted launcher rather than importing
packages from a client's working directory.

## Certification boundaries

The actual 1 GiB generated-source run and real banking/neural/PostgreSQL tests are
recorded separately. They are not a one-terabyte certification or a universal
competitor ranking. Production rollout still requires the customer's hardware,
source distribution, storage durability/IOPS, model concurrency, retention,
capacity/SLO, disaster-recovery and signed-in host-client acceptance checks.
