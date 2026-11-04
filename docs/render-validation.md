# Render preparation validation — 2026-11-02

The default Render profile is model-free. Copilot or another host AI supplies
business interpretation; OriginSpan supplies discovery tools, persistent evidence
and explicit uncertainty. No model API key, local LLM, PyTorch installation or
embedding-model download is required by `Dockerfile.render`.

Executed checks:

| Check | Result |
|---|---|
| Official current Render Blueprint JSON schema | Passed |
| Windows full suite | 44 passed; 1 external-DB fixture skipped |
| Clean Linux full suite | 44 passed; 1 external-DB fixture skipped |
| Render image, one API and worker, 2 GiB container limit | Passed small synthetic metadata fixture |
| Render PostgreSQL URL normalization and startup migration | Passed against real PostgreSQL 16 |
| PORT=10000, TLS, Keycloak JWT, project isolation | Passed |
| Upload, analysis and source evidence retrieval | Passed |
| Model-free image | Torch and sentence-transformers absent; neural search explicitly unavailable |
| Dropped privileges | Supervisor and API/worker children used UID 10001 with zero effective capabilities |
| Persistent state and PostgreSQL across restart | Published evidence remained searchable |
| Worker failure | Supervisor stopped the sibling API and container exited with code 1 |
| Python dependency advisory scan of default Render image | No known vulnerabilities found |
| Distributable wheel | Source/assets match; generated fixture secrets excluded |

Raw reports: [schema](validation-results/production/render-schema.json),
[container acceptance](validation-results/production/render-container.json),
[Windows](validation-results/production/windows-render.xml),
[Linux](validation-results/production/linux-render.xml),
[dependency scan](validation-results/production/render-dependencies.json),
[package parity](validation-results/production/release-artifact.json).

The container check runs locally using the actual Dockerfile and a persistent
volume, PostgreSQL, TLS proxy and test Keycloak. It does not create Render
resources. Live Render account/API validation, the actual customer OIDC/client
login flow, and workload sizing remain customer-deployment checks. The optional
`Dockerfile.render-semantic` profile is separate and is not covered by this
model-free container acceptance result. No host AI answer-quality benchmark or
terabyte performance certification is implied.

Use [render-deployment.md](render-deployment.md) to push the standalone repo,
create the Blueprint and configure identity. The default Blueprint provisions
paid disk-backed resources; review Render's pricing before creating them.
