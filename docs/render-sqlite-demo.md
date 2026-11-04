# Self-contained Render free demo

For the temporary single-user demo, select local SQLite explicitly. The same
discovery engine, supervised worker, SQL analysis, graph and evidence tools run
without a PostgreSQL connection. This is not a fallback after a database error.
The default production storage remains PostgreSQL.

In the Render web service's Environment settings, use:

| Key | Value |
|---|---|
| ORIGINSPAN_AUTH_MODE | demo-token |
| ORIGINSPAN_STORAGE_MODE | sqlite-demo |
| ORIGINSPAN_STATE | /tmp/originspan |
| ORIGINSPAN_DEMO_TOKEN | Keep your existing private generated token |
| ORIGINSPAN_PROJECT | demo |
| ORIGINSPAN_NEURAL_ENABLED | false |
| ORIGINSPAN_PGVECTOR | false |
| ORIGINSPAN_MAX_ACTIVE_ANALYSES | 1 |
| ORIGINSPAN_MIN_FREE_BYTES | 33554432 |

ORIGINSPAN_DATABASE_URL is not used in this explicitly selected mode and can be
removed from the service environment. Do not delete the Render database merely
to test this mode. OIDC settings are not needed for demo-token authentication.

Push the updated code, then deploy the latest commit with `Dockerfile.render`.
The startup log will include `hosted_catalog_ready`, `storage: sqlite-demo` and
`ephemeral: true`. `/readyz` also reports `ephemeral: true`. Open the viewer,
enter project `demo` and your existing token, then upload the small source ZIP.

Catalog, source snapshots and evidence blobs are ephemeral. A service restart
starts an empty demo; re-register and upload again. To return to shared
PostgreSQL, explicitly change ORIGINSPAN_STORAGE_MODE to `postgres` and supply
a working database URL. Existing PostgreSQL records are not copied, rewritten or
deleted by changing the demo mode.

Validation: the actual Docker image passed startup, authenticated MCP,
upload/analysis, evidence retrieval, project isolation, denied review and a
fresh-state restart with a 512 MiB RAM limit, swap disabled, and no PostgreSQL
service. A deliberately invalid PostgreSQL URL was ignored only because
sqlite-demo was explicitly selected. The full Windows suite passed 75 tests,
with one external database fixture skipped. Raw results are in
`validation-results/production/render-free-sqlite-demo.json` and
`windows-sqlite-demo.xml`.

The Docker TLS investigation verified a CA bundle, PostgreSQL/libpq SSL support,
and successful standard PostgreSQL TLS negotiation to the supplied public Render
endpoint without passwords or SQL. Direct TLS negotiation failed on that
endpoint. Those probes do not establish why the live Render service's database
connection was rejected. TLS logs now include bounded reason codes; they never
print the connection URL, password or driver exception body. PostgreSQL's
production connection issue remains separate from this working demo path.
