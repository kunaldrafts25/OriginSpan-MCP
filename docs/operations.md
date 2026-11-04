# Historical v0.2 operations

This document records the earlier release. For 0.3 use [production operations](production-operations.md) and [production validation](production-validation.md).


State defaults to `.diamond` under the process working directory. Prefer an
explicit short path on Windows for deeply nested JVM source packages. Back up the
metadata database and state together. Snapshots are immutable through public
interfaces, not write-protected against the operating-system administrator.

Local stdio needs no HTTP token. The viewer binds loopback and requires an
operator-generated token of at least 24 characters. A remote deployment requires
PostgreSQL, IdP configuration, HTTPS termination, secure storage permissions and
an appropriate backup/retention policy. The example Keycloak realm is a test
fixture, not a production identity configuration.

```powershell
python scripts/setup_integration.py
docker compose --env-file .diamond/integration.env -f infrastructure/compose.test.yaml up -d
python scripts/verify_keycloak.py
docker compose --env-file .diamond/integration.env -f infrastructure/compose.test.yaml stop
```

The fixture uses loopback ports 55433 (PostgreSQL) and 8890 (Keycloak). Keep the
generated ignored credential file with its database volume; regenerating it does
not reset an existing database password. The verification script starts MCP on
8765 temporarily and records a report without storing credentials/tokens.

`docker build -t diamond-mcp:0.2.0 .` creates a clean base/PostgreSQL image running
as a non-root user. The image does not bundle the optional embedding model.
Run base checks using `docker run --rm --entrypoint python diamond-mcp:0.2.0 -m pytest -q`.

One worker process owns analysis jobs in this preview. Jobs persist status and
results; an abrupt worker stop leaves a visible interrupted operation requiring
explicit recovery: `diamond analyze APPLICATION_ID --recover`. Do not recover a
job while its owner is active. Distributed leases, a queue service and multi-worker
job scheduling are follow-up work. Cancellation is cooperative at inventory,
file extraction and embedding-batch boundaries; it cannot interrupt a native
parser or an individual model forward pass.

Limits: 2 MB per artifact, 50 MB selected snapshot/expanded ZIP, 2000 selected
files, 160 KB MCP result, 50 search results per page, 200 graph nodes per page,
four-hop/200-node bounded impact traversal, 50 proposals per submission. Native
SCIP imports are limited to 2 MB/30000 occurrences. Limits are explicit; graph
and search responses provide total counts and continuation offsets.

Audit records operation, actor, project/application and timestamp without source
excerpts. Permission denials at MCP tool boundaries are recorded. SDK/webserver
diagnostics should be handled by the operator's logging policy; access logging
is disabled by the CLI. Secret filtering is best-effort, not a replacement for
approved inputs and storage/identity permissions.

Index version is `diamond-1`, extractor `diamond-corpus-1`. This initial schema is
created automatically for fresh databases. A rolling schema-migration framework,
native vector acceleration, retention/GC tooling and full multi-tenant production
operations are not included in this preview.
