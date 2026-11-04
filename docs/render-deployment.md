# Deploy the standalone OriginSpan MCP on Render

Use the standalone repository rooted at `D:\diamond-mcp`. The root contains
`render.yaml`, `Dockerfile.render`, `pyproject.toml` and `src/`. Do not select the
original synthetic Diamond AI application repository for this Blueprint.

Executed local checks and their limits are in [render-validation.md](render-validation.md).

## Resources and cost

The Blueprint creates a **paid** `1c-2g` Docker web service, a 10 GB persistent
disk and a paid `0.1c-256mb` PostgreSQL 16 database with 5 GB storage. Review the
dashboard's current prices before creating resources. No resources are created
by editing or validating these files. The 2 GB service is a small-pilot profile,
not a capacity guarantee; size it against your actual repositories and workload.

Render's free web tier cannot attach the persistent disk this deployment needs.
Do not change `plan` to `free` and expect saved uploads, indexes or analysis jobs
to survive. The database's external IP allow list is empty; the web service uses
its internal connection string. Both resources are in Singapore; change both
regions together before the first deployment if required.

The API and one durable worker run as child processes in one service. They use
the same `/var/data/originspan` directory. Render disks cannot be shared with a
separate worker service, so do not split these processes across Render services
without implementing shared source/blob storage. Do not enable replicas.

## 1. Push the repository

Commit and push the standalone source to your GitHub/GitLab/Bitbucket repository.
Keep `.runtime`, `.diamond`, `.venv`, credentials, database dumps and client source
snapshots out of Git; the supplied `.gitignore` excludes local state. Render
reads the pushed commit, not files still on your PC.

## 2. Configure identity

Use an existing OIDC provider that issues RS256 JWT access tokens. Configure:

- Issuer URL: the exact `iss` value in the provider's access tokens.
- JWKS URL: the trusted provider signing-key endpoint.
- Audience: `https://YOUR-SERVICE.onrender.com/mcp`.
- Required claims: `sub`, `iat`, `exp`, `iss`, `aud` and
  `originspan_projects`, for example `["demo"]`.
- Space-separated scopes: `originspan:read`, `originspan:analyze`,
  `originspan:propose`. Grant `originspan:review` only to separate human reviewers,
  and `originspan:operate` only to monitoring/operators.

The exact Render hostname may have a suffix. Read the actual service URL from
the dashboard, then configure that audience at your IdP. The runtime derives its
default resource URL from Render's `RENDER_EXTERNAL_URL`, so you don't need to
guess the hostname in YAML. Tokens for a guessed/wrong audience are rejected.

The provider must also support the authorization/login flow required by your
chosen MCP client; verifying JWTs alone does not register that client with the
IdP. A signed-in Copilot connection still needs provider/client acceptance.
Never paste access tokens or client secrets into Git or this Blueprint.

## 3. Create the Blueprint

1. In Render, choose **New → Blueprint** and connect the standalone repository.
2. Choose its branch and use `render.yaml` as the Blueprint path.
3. Review the service, database, region, disk and estimated cost.
4. Enter `ORIGINSPAN_OIDC_ISSUER` and `ORIGINSPAN_OIDC_JWKS` when prompted.
5. Create the resources. The database connection is wired automatically.

If you already created an empty web service manually, use a distinct resource
name or carefully reconcile the Blueprint with that existing service. Blueprint
resource names identify resources; do not sync over unrelated existing services.

Leave the Docker Command override blank. `Dockerfile.render` supplies the
entrypoint. The default image installs no model, PyTorch or embedding stack.
Copilot or another MCP client supplies the business reasoning through its own
model access. OriginSpan supplies source facts, schemas, SQL/lineage, relationships,
text/identifier search and cited context. Neural retrieval reports unavailable;
there is no hidden embedding API call or model download.

The entrypoint uses Render's `PORT`, defaults to 10000, checks disk writability,
normalizes Render's PostgreSQL URL for psycopg3, and migrates before launching
either child. It initializes only its state-directory ownership, then drops to
uid/gid 10001 before catalog operations, API and worker launch. No separate
pre-deploy migration is configured because that environment cannot see the disk.

## 4. Verify and connect

Visit these URLs, replacing the hostname with your actual service URL:

```text
https://YOUR-SERVICE.onrender.com/healthz
https://YOUR-SERVICE.onrender.com/readyz
https://YOUR-SERVICE.onrender.com/
https://YOUR-SERVICE.onrender.com/mcp
```

`/readyz` should return `{"ready":true,...}`. The viewer page is public static
HTML; access to application data requires an authorized token. `/mcp` is the
authenticated Streamable HTTP endpoint, not a normal browser page. An anonymous
MCP request should be rejected, not show your data.

In VS Code's Local harness MCP configuration, add:

```json
{
  "servers": {
    "originspan-render": {
      "type": "http",
      "url": "https://YOUR-SERVICE.onrender.com/mcp"
    }
  }
}
```

Use your client's supported provider sign-in flow. The AI uses the same project
ID supplied in `originspan_projects` (for example `demo`). Register an application,
upload an approved source ZIP, start an analysis, then poll it to completion.
Shared clients cannot register a path on your PC. ZIP imports retain their
50 MB archive bound; larger inputs require approved operator-managed snapshots.

For CLI upload, set `ORIGINSPAN_ACCESS_TOKEN` in your local terminal and run:

```powershell
.\.venv\Scripts\python.exe scripts/run_discovery.py --project demo upload https://YOUR-SERVICE.onrender.com APPLICATION_ID C:\approved\source.zip
```

## Optional embedding search and HNSW

An embedding model is a search aid, not the AI that reasons about the application.
It can retrieve a definition phrased as "the day a person was born" for a query
such as "birth date". Copilot can perform iterative discovery without it, using
OriginSpan's structural and text tools; direct embedding similarity is unavailable
in the default profile.

If you later want local embedding search, change these values in `render.yaml`:

```yaml
dockerfilePath: ./Dockerfile.render-semantic
plan: 2c-4g
# Under envVars:
# ORIGINSPAN_NEURAL_ENABLED: "true"
```

Edit the existing ORIGINSPAN_NEURAL_ENABLED env entry rather than duplicating it.
The optional Dockerfile installs CPU-only neural dependencies and explicitly
downloads the pinned public multilingual-e5-small model during image build.
It needs outbound access to PyPI, download.pytorch.org and Hugging Face. Runtime
model downloads remain disabled. This is a separate opt-in profile; validate it
under your Render resources before using it for customer workloads.

The initial Blueprint keeps `ORIGINSPAN_PGVECTOR=false`. With optional embeddings
enabled, neural search streams scoped vectors, with O(N) local-exact query cost.
Use native HNSW for larger indexes after explicit setup.

To enable HNSW, use the database's Render PSQL console/command as its owner to
run the explicit operator setup:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Then change `ORIGINSPAN_PGVECTOR` to `true` in **render.yaml**, commit/push it, and
sync the Blueprint. Do not rely on a dashboard-only edit of a value managed by
the Blueprint. Start a new analysis so its finalizer creates the native index.
Render documents pgvector support; OriginSpan does not install extensions through
MCP or implicitly under an application request.

## Redeploy, recovery and backups

Automatic deploys are initially off. Push a reviewed update and deploy/sync
explicitly. A disk-backed service has downtime during replacement. SIGTERM is
forwarded to API/worker; after a bounded grace period, remaining child/parser
processes are stopped. Unexpected API or worker exit fails the whole container
so Render can restart it. Durable work is reclaimed after lease expiry.

Back up the PostgreSQL catalog and complete state/blob disk together while
writers are paused. Render's database backup alone does not contain source
blobs; a disk snapshot alone does not contain the PostgreSQL catalog. Retain
the same `/var/data/originspan` mount path on restore and preserve DB object
ownership. See production-operations.md for recovery qualifications.

For a custom hostname, set `ORIGINSPAN_RESOURCE_URL` to its exact HTTPS `/mcp` URL,
update the IdP audience, and redeploy. The MCP transport allows that configured
Host/Origin. Render terminates TLS; no nginx container or customer certificate
files are required for its standard onrender.com URL.

## Local checks before deploying

```text
python scripts/validate_render.py
python -m pytest -q
docker build -f Dockerfile.render -t originspan-mcp:render .
python scripts/verify_render.py prepare
python scripts/verify_render.py run
python scripts/verify_render.py stop
```

Schema validation uses Render's official current public JSON Schema. If the
Render CLI is installed/authenticated, also run `render blueprints validate
render.yaml`; schema checks are separate from live platform validation. The
container rehearsal creates only a local `diamond-render-validation` Docker
project with synthetic metadata, test Keycloak and a local CA. It creates no
Render resources. Local credentials and volumes remain ignored for inspection.

Primary references: [Blueprint specification](https://render.com/docs/blueprint-spec),
[disks](https://render.com/docs/disks), [Docker](https://render.com/docs/docker),
[health checks](https://render.com/docs/health-checks),
[PostgreSQL extensions](https://render.com/docs/postgresql-extensions).
