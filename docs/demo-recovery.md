# Self-restoring public Render demo — 0.5.1

Render Free loses filesystem changes, including SQLite, on idle spin-down,
restart and redeploy: https://render.com/docs/free . The former workflow restored
Bank of Anthos once, but the next empty-filesystem boot had no applications.

The lightweight Render image now contains the approved public Bank of Anthos
source snapshot and an index built by OriginSpan's static parsers at image-build
time. The sample is pinned to db35fea9fd090150e2398106aadb475576f80d94 and includes
the upstream Apache-2.0 license and original source notices.

On an empty state directory, before API/worker startup, the explicit
`sqlite-demo` + `demo-token` + project `demo` runtime restores this public sample.
The source, evidence blobs and a completed catalog are copied locally. Source
paths are rebased to the runtime directory. The catalog is published last. There
is no runtime GitHub request, application execution, upload or indexing delay.
The restored sample uses stable application/analysis IDs within the built image.

The manifest verifies source/catalog hashes, extractor version and catalog
schema. Existing catalogs and conflicting files are preserved; restoration
does not replace customer state. PostgreSQL, OIDC and other projects are not
seeded. `ORIGINSPAN_DEMO_SEED=off` disables this sample behavior.

This is recovery of one public presentation sample. Private uploads, proposals
and other modifications still need durable storage to survive Render Free resets.
It does not turn the free filesystem into persistent storage or remove the
platform's cold-start delay. It does not relax remote local-path restrictions.

Executed checks: 92 tests passed, one external fixture skipped; two empty-tmpfs
container boots at 512 MiB with outbound networking disabled. Both boots exposed
the completed 321-file sample, 4,076 evidence items, HTML country metadata and
eight supported JPQL resolutions without an upload. The second boot also proved
that the temporary mutation from the first boot was cleared while the public
sample was restored. See `docs/validation-results/demo-recovery/cold-boots.json`.
