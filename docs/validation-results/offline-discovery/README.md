# Offline discovery: executed validation

Historical record: the [later feasibility follow-up](../offline-feasibility/README.md)
includes successful real-source runs with external networking disabled. Earlier
failures below are retained for audit and do not describe the latest gate status.

Date: 3 November 2026. These are engineering checks, not a model-quality or
enterprise-readiness certification.

## What actually ran

| Check | Result |
| --- | --- |
| Real local reasoning runtime | Qwen3.5-2B Q4_K_M via llama.cpp b11960; no hosted model |
| MCP transport | Actual stdio client/server, scoped to one completed analysis |
| Public banking sample | Bank of Anthos, revision `db35fea9fd090150e2398106aadb475576f80d94` |
| Banking report | Six sections completed with explicit coverage gaps |
| Banking elapsed time | 410.783 seconds, after indexing |
| Banking usage | 14,231 reported input tokens; 2,162 output tokens; reasoning counters unavailable |
| Banking runtime memory | 1,762.8 MiB peak RSS; GPU memory unavailable; memory limit not hit |
| Hardware | Windows, i5-1135G7, 4 cores/8 threads, about 7.7 GiB RAM, integrated graphics |
| Packaging | Wheel built with package-index access disabled |

The banking run used six model calls and fifteen MCP calls. It collected 12/60
ORM records and 12/124 documentation records and shortened demographic context
to fit the laptop profile. These continuations and omissions are recorded, not
treated as complete coverage. Indexing time is excluded from the reported run.

The reviewed prototype output exposed irrelevant demographic gaps and weak
SQL/handoff interpretations. Citation existence did not prevent incorrect
interpretation. Follow-up changes added distinct-field context selection,
shorter response constraints, metadata-only gap instructions, preserved nested
predicate fields and better handoff evidence ordering. The banking smoke is not
a measured accuracy improvement for those changes; repeat the frozen tasks to
evaluate them. Do not present this small model as superior to Copilot.

The runtime conversion came from `unsloth/Qwen3.5-2B-GGUF`, revision
`f6d5376be1edb4d416d56da11e5397a961aca8ae`, not an official Qwen GGUF conversion.
Model SHA-256:
`aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223`.
Runtime and bundled library checksums are stored in the installed profile.

A final Windows protocol smoke with the refined runtime completed in 14.507
seconds: 347 input tokens, 126 output tokens, 1,615.0 MiB peak RSS. It returned a
schema-constrained, source-cited interpretation of one supplied field. This is
protocol evidence, not a discovery benchmark or OS-level network-denial test.

## Boundaries tested

Automated checks cover project isolation, actual MCP stdio, inherited remote
database configuration being overridden with the local catalog, model/runtime
hash changes, added runtime libraries, invalid citations, disallowed model
actions, source excerpt filtering, checkpoint/resume scope binding, inference
locks, context preflight, memory-limit termination, and frozen-suite tampering.

Model fixtures used in unit tests are explicitly synthetic. They do not count as
business-meaning evaluation. The real model run is reported separately above.

The disconnected container result and exact regression totals are recorded in
the accompanying `executed.json`. An initial two-CPU container attempt stopped
after one section on a local inference timeout, while external access remained
blocked. The timeout was then made explicitly bounded at 300 seconds and the
workflow rerun with four CPUs. Both the larger report and a smaller-output
compatibility question still hit that deadline. **The disconnected full-workflow
gate failed on this machine and remains open**; network denial itself worked.
No online fallback was added. Resolve the Linux-container performance/timeout
failure on the intended workstation before claiming a certified offline deployment.

## Qualification still required

- Domain-owner review of the 60-task draft and exact source subsets; version the
  approved checksum before comparative evaluation.
- Full held-out runs and independent scorecards for a selected office model.
- Workstation GPU memory, concurrency, recovery, latency and disconnected tests.
- Approved target schemas and mapping rules for a meaningful handoff evaluation.
- Same-model, same-source Copilot baseline for quality and whole-session token
  comparisons. No measured token-saving percentage is claimed here.
- Licensing/security review of provisioned artifacts and operational controls.

Profiles remain **UNQUALIFIED**. Fine-tuning remains conditional on recurring
held-out errors after retrieval improvements; no training has been performed.
