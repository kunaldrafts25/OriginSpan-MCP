# DIAMOND production discovery plan — 2 November 2026

## Objective and honest release criteria

Build a real discovery backend behind MCP for code, schemas and approved metadata:
demographic-field discovery, SQL inventory/lineage and multilevel architecture.
The customer has explicitly retained metadata-only scope: no customer records,
live database profiling, source execution or automatic code changes.

A terabyte is a capacity-planning input, not a promise of instantaneous or perfect
understanding. The architecture must stream inventory, partition work and resume;
certification requires physically executed capacity/quality tests on named hardware.
No 1 TB claim will be inferred from a sparse file or extrapolated from a small demo.
Likewise, outperforming every discovery product is an evaluation goal, not a result.

## Reference findings

Read-only reference: `D:/ryzeall/Ryze-MCP-Enterprise/Ryze-MCP`.
Inspected actual response optimizer, tool decorators, logging middleware, adapter
and package structure. Useful patterns: domain adapters, secret-safe request
context, actionable errors, compact responses and layered provider boundaries.
The local audit is historical and disagrees with the current tests directory;
do not treat it as a current security certification. No ads tools, credentials,
customer context or handwritten legacy protocol implementation will be copied.

Primary online references:

* [Zoekt](https://github.com/sourcegraph/zoekt): indexed/sharded code search and symbol signals.
* [MCP security](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices): audience validation, confused deputy/SSRF and scope boundaries.
* [PostgreSQL locking](https://www.postgresql.org/docs/current/sql-select.html): transactional work claiming with SKIP LOCKED.
* [pgvector](https://github.com/pgvector/pgvector): native vectors/HNSW and filtering limitations.
* [OpenSearch filtered k-NN](https://docs.opensearch.org/latest/vector-search/filter-search-knn/efficient-knn-filtering/): authorization-aware ANN patterns for a larger deployment tier.
* [SQLGlot lineage](https://sqlglot.com/sqlglot/lineage.html): schema-aware static SQL, not runtime DB truth.
* [OpenTelemetry](https://opentelemetry.io/docs/languages/python/instrumentation/): traces, bounded telemetry and operational signals.

## Implementation sequence

1. **Persistent streaming ingestion and work queue.** Normalize analyses, source
   manifests, work items, evidence and graph edges. Use content-addressed blobs,
   incremental extraction cache and paginated database queries. Replace daemon
   ownership with leases/fencing, heartbeats, retries/dead letters, cancellation
   and atomic publication. Standalone worker CLI and multi-process execution use
   the same queue. No fixed total repository size/file-count cutoff; operator
   quotas, free-space checks and per-parser memory limits remain explicit.

2. **Bounded extraction and cross-file reduction.** Isolate parser work from API
   lifetimes, retain file hashes and extractor/model versions, reuse unchanged
   content and handle deleted files by manifest membership. Keep coverage for
   unsupported, oversized and failed artifacts. Persist SQL units, declared
   fields, schema keys, symbols and service evidence as first-class records.

3. **Database-backed retrieval.** Exact identifiers and lexical ranking must not
   load all evidence. Persist vector rows, use native pgvector search for the
   shared tier, and bounded streaming exact fallback locally. Scope every channel
   before selection, record rank provenance and diversify context by file/entity.
   Optional neural dependencies remain explicit; no hosted-model fallback.

4. **Discovery domain tools.** Dedicated demographic context packages use broad
   neural/lexical retrieval plus declared fields, relationships and dictionaries.
   Evidence-backed classifications remain hypotheses until reviewed. SQL tools
   inventory actual embedded/static queries, identify parameters/dynamic gaps,
   build schema-aware select/DML/view lineage and separate selection dependencies.
   Architecture tools aggregate system/service/container/component views from
   manifests, routes, imports, configured endpoints, databases and message topics;
   diagrams link back to evidence and label candidate edges.

5. **Operational MCP.** Keep the official SDK, OIDC scope/audience checks,
   application isolation and resource authorization. Add structured error codes,
   correlation IDs, capacity/backpressure signals, health/readiness, observable
   worker progress and metrics without source bodies or demographic values.
   Ship explicit DB schema versions/migration command, deployment manifests,
   backup/restore and capacity runbooks. Do not confuse an ads MCP's wrapper
   patterns with the indexing infrastructure this domain needs.

6. **Executed validation.** Preserve the exact banking route/table/column gate;
   test killed/restarted workers, concurrent claims and stale lease completion,
   incremental reuse/deletion, failed parser visibility and cross-project search.
   Measure actual processed bytes/files, memory, cold/incremental work and query
   latency on generated corpora and public projects. Publish capacity tests as
   measured sizes only. Compare frozen held-out discovery tasks against installed
   competing tools; list unexecuted cases separately. A signed-in Copilot session
   and customer-scale staging certification remain external release gates.

## Component boundaries

MCP/HTTPS → authorized discovery service → normalized catalog and durable queue.
Scanner → immutable manifests/content store → leased workers → evidence/vector/
SQL/graph indexes → atomic published analysis. The optional viewer only reads
published analyses and supports separate human review. It never renders every
node from a terabyte repository in one browser graph.

The first production implementation tier uses PostgreSQL plus shared blob storage
with independent workers. SQLite provides single-machine development/runtime
compatibility. Multi-host workers require a shared content volume; an object-store
adapter and distributed ANN sharding must be independently validated before
claiming a multi-node terabyte deployment. A production-readiness checklist must
record what was actually tested rather than rename a preview to production.
