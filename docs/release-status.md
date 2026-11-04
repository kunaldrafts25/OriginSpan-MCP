# 0.3 implementation and certification status

The production plan preceded implementation and records the Ryze reference and
primary-source research. The backend is real durable indexing/retrieval; no
synthetic platform is required. The following are implemented mechanisms, not a
claim that every customer environment or repository format is certified.

| Area | Implemented/exercised | Qualification |
|---|---|---|
| MCP | Official transports, tools/resources/prompts, scoped bounded results/request IDs | Actual signed-in customer host acceptance remains external |
| Ingestion | Streaming scandir/content capture, content hashes/manifests, no fixed project size/file ceiling | Explicit per-artifact limits; managed volumes for large sources |
| Workers | Durable leases/DB-clock fencing, heartbeat, retries/dead letters, cancellation, recoverable publication | Multi-host storage durability/SLO requires staging validation |
| Incremental | Per-file extraction/model cache; deleted files disappear from new snapshots | Source is still re-hashed; no filesystem journal fast path |
| Retrieval | Indexed exact/full text, real local neural, RRF/diversification, native scoped pgvector HNSW | Local exact fallback; no distributed ANN/reranker certification |
| SQL | Embedded/static inventory, parameters/dynamic gaps, schema-aware SELECT/view/INSERT/UPDATE lineage | Unsupported dialect/runtime/ORM behavior remains explicit |
| Demographics | Semantic field/definition context with cited metadata | No record profiling/protected-trait inference; host AI interpretation |
| Architecture | Service/configuration/table evidence, system/service/component levels, Mermaid | Runtime topology/compiler completeness needs additional evidence |
| Security | OIDC/scopes/project isolation, hosts/origins, review/disclosure, opened-handle root checks | Customer IdP/TLS/secrets integration must be verified |
| Operations | Migrations, worker CLI, app-role provisioning, tested TLS overlay, outage alert/worker recovery and separate restore drill | Customer alert delivery/exporters, retention/GC and target-infrastructure recovery remain operator work |
| Measurement | Allocated 1 GiB, exact banking, real neural/native vector, process-kill and browser checks | No universal market-win or 1 TB certification claim |

The 1 GiB capacity fixture processed 1,073,741,824 bytes in 1,024 source files with
four workers in about 91 seconds. It used declarations and comment padding,
without neural indexing. Banking retained exact 39 routes, 3 tables and 23 DDL
columns. Neural retrieval found a held-out birth-date definition missed by
lexical search. Raw reports accompany production-validation.md.

The [2026-11-02 deployment rehearsal](deployment-readiness.md) passed using the
production image, local TLS and Keycloak. Both final OS suites passed 31 tests;
the real PostgreSQL test ran separately. PyJWT was patched and the final base
Python dependency advisory scan reported no known vulnerabilities.

Before rollout, validate the actual source mix, capacity/IOPS, RAM/CPU/model
concurrency, query/index SLO, identity, backups/restoration and host-client
integration. Terabyte certification requires an executed terabyte workload;
superiority requires a broader executed comparative suite. Neither is marked
complete by inference from small benchmarks or a version label.
