# Deployment readiness — 2026-11-02

The standalone MCP passed an isolated deployment rehearsal using the production
image and Compose service definitions. It is ready for local use and a controlled
customer staging rollout. Customer production sign-off remains conditional on
the customer identity, DNS, certificates, storage/SLO and signed-in AI client.

## Executed checks

| Check | Result |
|---|---|
| Final Windows suite | 31 passed; 1 external-database test skipped |
| Final clean Linux suite | 31 passed; 1 external-database test skipped |
| Separate real PostgreSQL/pgvector test | Passed, including strict JSON byte counters |
| HTTPS Streamable HTTP MCP, SDK 2.3.0 | Passed with certificate/hostname verification enabled |
| Keycloak JWT authentication and project isolation | Passed; AI review denied |
| SDK 1.27.0 compatibility | Passed; negotiated protocol 2025-11-25 |
| API restart | Published evidence remained searchable |
| Database outage | Readiness failed; Prometheus alert reached firing state |
| Worker recovery | Same worker containers processed new work after DB recovery, without restart |
| Backup/restore | Quiesced catalog and complete state restored into separate database/storage; retrieval and blob hashes passed |
| Production TLS overlay | Compose, template substitution and certificate loading passed under non-root/read-only constraints |
| Python dependency advisory scan | No known vulnerabilities in the final base image at scan time |

Raw reports are in [validation-results/production](validation-results/production).
The dependency scan covers Python packages; it does not certify OS packages,
the optional neural dependency stack, the identity provider or penetration-test
results. The base rehearsal explicitly disabled neural inference; the separately
executed real local-model/HNSW results remain in production-validation.md.

## Fixes made during readiness work

- Queue workers retry temporary catalog connection failures with bounded backoff
  and no exception bodies in retry logs.
- PostgreSQL byte totals are returned as JSON integers. The actual API exercise
  caught a Decimal serialization failure missed by SQLite-only checks.
- The TLS proxy refreshes container DNS, preserves the complete Host header and
  keeps temporary files on writable tmpfs in a read-only container.
- Restore preserves application-role ownership. Table grants alone were
  insufficient for the application's index initialization.
- PyJWT was upgraded from 2.12.0 to 2.15.1 after the advisory scan. Upstream fixes
  are documented in the [PyJWT changelog](https://pyjwt.readthedocs.io/en/stable/changelog.html).
- Authenticated monitoring now exposes `diamond_catalog_available`, alongside
  existing operation counters. The rehearsal tested an actual alert transition.

## Reproduce

Prerequisites: Python environment with the package, PostgreSQL extra,
cryptography and PyYAML, plus a running Docker engine. Ports 9443/9096 must be
available. Run from the standalone repository, not inside a client source tree.

```text
python scripts/deployment_rehearsal.py prepare
docker build --target production -t diamond-mcp:0.3.0 .
python scripts/deployment_rehearsal.py run
python scripts/verify_edge_config.py
python scripts/deployment_rehearsal.py stop
```

This creates only the `diamond-readiness` Compose project. It uses a local test
CA and Keycloak development mode; neither is a customer production identity
deployment. Credentials, certificates, database dumps and restored state remain
under ignored `.runtime/readiness`. Restrict that directory to deployment
operators; keep backups on approved encrypted storage. The stop command retains
volumes and artifacts. Repeated drills allocate separate restore databases.

For older-client compatibility, run `scripts/verify_legacy_client.py` using an
environment actually containing MCP SDK 1.27.0. Do not downgrade the server SDK.

## Customer cutover requirements

Supply the target infrastructure, public resource URL, customer OIDC issuer/JWKS
and project/scope claim mappings. Provision a trusted certificate and distinct
database secrets through the deployment's secret-management process. Configure
alert notification routing and token renewal; the local scrape token is a short
lived test credential, not an unattended monitoring solution.

Validate restore onto the intended backup infrastructure, source/storage
durability, workload-specific capacity and latency targets, and signed-in
Copilot or another intended AI client. The rehearsal tested one host with two
workers, not multi-host failover. Retention/GC remains an operator-managed gap;
do not delete blobs referenced by retained analyses.

The earlier allocated 1 GiB fixture and banking/neural checks remain valid
measurements. A terabyte workload, comprehensive language/ORM coverage and a
universal market-superiority claim remain unverified.
