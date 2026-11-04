# OriginSpan rename and compatibility

The product is now **OriginSpan Discovery MCP**. The distribution is
`originspan-mcp`; use `originspan --help`, `python -m originspan` or
`python -m originspan.hosted`. The MCP server and viewer identify as OriginSpan.

New configuration uses `ORIGINSPAN_*`, `originspan_projects`,
`originspan_applications`, `originspan:read`/`analyze`/`propose`/`review`/`operate`
and `originspan://` resources. Legacy `DIAMOND_*` variables, JWT grants/scopes,
`diamond://` resources, the `diamond` console alias and `diamond_mcp` imports are
retained for compatibility. New environment values and claims take precedence;
project grants are never unioned across claim namespaces.

An existing `.diamond` state directory is reused when `.originspan` does not
exist. The local IDE launcher selects the state relative to its own repository,
not an arbitrary client working directory. Explicit state settings are honored.
No database tables, source snapshots, evidence IDs, model manifests, extraction
versions or embedding fingerprints are renamed. Existing metrics names remain
compatible with monitoring rules. Historical reports retain their original names
and results; the rename is not a rerun of those measurements.

The checkout remains at `D:\diamond-mcp` so existing interpreter paths, source
locations and model fingerprints remain valid. A fresh clone can use any folder
name. The original synthetic Diamond AI application is a separate legacy POC;
this rename applies to the standalone discovery product.

For a new Render deployment, the Blueprint uses OriginSpan resource names and
`/var/data/originspan`. For an existing deployment, retain its database, disk,
state path and resource URL. Renaming Blueprint resource names can create new
resources; review that diff before syncing. Update IdP/client scopes together
when switching clients to the new namespace. No Render resources are changed
by this repository rename.

Validation: Windows and Linux suites each passed 51 tests with one external
database fixture skipped. Both resource URI schemes were exercised through an
actual stdio client. The OriginSpan Render image passed its local authenticated
container rehearsal with legacy IdP claims, upload/analysis, persisted evidence,
non-root execution and worker-crash recovery. Existing local state was opened
successfully without moving it. The renamed wheel matches the source/assets.
