# Offline discovery

See the [latest feasibility results](validation-results/offline-feasibility/README.md)
for the successful real-source disconnected run and the remaining model errors.
[Initial checks](validation-results/offline-discovery/README.md) retain the earlier
timeout failures and laptop measurements for comparison.

OriginSpan now has a local reasoning controller over its existing MCP evidence
tools. The inference runtime and code under analysis are separate: the controller
can launch only the operator-pinned local runtime, never a repository script.

## What is implemented

- Explicit local GGUF/runtime profiles, SHA-256 verification including bundled
  runtime shared libraries, immutable revision labels and laptop/office budgets.
- A real MCP stdio client with explicit project/application/analysis scope.
- A bounded six-section workflow: purpose/APIs, architecture, demographic fields,
  source-query lineage, transformations and a draft mapping handoff.
- Schema-constrained model output, citation-reference validation, targeted
  evidence requests, unknowns, checkpoint/resume and readable Markdown reports.
- Compact semantic inputs omit index bookkeeping while retaining unfamiliar
  client metadata. Citation choices are restricted to supplied references.
- Architecture and SQL retain parser facts; a missing approved target schema is
  reported directly. Local AI interprets purpose, selected field evidence and
  transformations. A bounded summary is not a classification of every field.
- Field meanings use a separate typed annotation contract. Source identifiers
  and names come from evidence; all proposed meanings remain unapproved. Missing
  annotations are marked unresolved rather than filled with a category.
- Optional candidate meanings come from the caller, with unresolved/ambiguous
  outcomes always available. No customer-field ontology is mandatory. Explicit
  uncertainty and competing unreviewed definitions receive conservative review
  guards; these checks do not prove business correctness.
- Recorded per-call usage. Unavailable reasoning/cache counters remain null.
- Manual, explicitly approved metadata export. There is no online provider path.
- A frozen 60-task evaluation draft, batch runner and reviewer-based scorecards.

This is not an office-model quality certification. Model profiles remain
UNQUALIFIED; owner review and the separate security/resource/disconnection gates
are required. `evaluate-offline` evaluates answer scorecards only, not enterprise
readiness. API/event test execution and target-system writes are not implemented.

## Provision models explicitly

Prepare a supported llama.cpp runtime and a licensed GGUF conversion on a
connected provisioning machine. Verify publisher checksums, retain license notices
and record conversion provenance. Copy all runtime dependencies to the offline
machine. Do not use floating `main`/`latest` revisions. No CLI command below
downloads a reasoning model.

```powershell
originspan --state D:\OriginSpanState models add laptop-qwen-v1 `
  --model D:\ApprovedModels\model-Q4_K_M.gguf `
  --runtime D:\ApprovedRuntime\llama-server.exe `
  --model-id Qwen/Qwen3.5-2B --revision EXACT_MODEL_REVISION `
  --runtime-version EXACT_RUNTIME_VERSION --license Apache-2.0 --tier laptop
originspan --state D:\OriginSpanState models verify laptop-qwen-v1
originspan --state D:\OriginSpanState models list
```

`models verify` verifies files, not licensing, model quality or protocol
compatibility. The GGUF converter/revision must be recorded accurately; do not
represent a third-party quantization as an official upstream conversion.
Use a versioned profile name to upgrade; rollback selects the previous profile.

Candidate profiles: Qwen3.5-2B for a small laptop prototype, and Qwen3.8-27B,
Qwen3-Coder-30B-A3B or Devstral Small 2 24B for workstation qualification. These
are candidates, not benchmark winners. Office planning assumes 24–32 GB VRAM
and 64 GB RAM, with one analysis at a time. Actual fit must be measured.

## Analyze approved local sources and discover

```powershell
$env:ORIGINSPAN_DATABASE_URL = 'sqlite:///D:/OriginSpanState/state.sqlite'
$env:ORIGINSPAN_ALLOW_GITHUB = 'false'
originspan --state D:\OriginSpanState --project local --root D:\Sources register Sample --path D:\Sources\Sample
originspan --state D:\OriginSpanState --project local analyze APPLICATION_ID
originspan --state D:\OriginSpanState --project local discover APPLICATION_ID --analysis ANALYSIS_ID --profile laptop-qwen-v1
```

The `analyze` command creates source facts. `discover` interprets an already
completed analysis; it does not register, download, run or modify the application.
An omitted analysis ID resolves the latest completed snapshot once. Name
collisions are rejected; explicit IDs are recommended. The local MCP child uses
the selected state's SQLite catalog even if the shell contains a remote database
URL, and disables GitHub import. Optional embeddings load only approved local
files through the existing adapter.

Output lives under `STATE/offline-runs/RUN_ID/`: `report.md` and `run.json`.
The latter includes source scope, model/runtime fingerprints, stages, evidence
references, gaps and usage. AI interpretations are unapproved hypotheses. A valid
citation proves that supplied evidence exists, not that the business meaning is
correct. Extra fields and undeclared IDs are rejected. Context shortening,
unread pages and unsupported evidence remain visible.

Resume with exactly the same source snapshot, question, profile and inference
contract (system instructions and response schema):

```powershell
originspan --state D:\OriginSpanState --project local discover APPLICATION_ID --analysis ANALYSIS_ID --profile laptop-qwen-v1 --resume RUN_ID
```

Budgets apply across resume, not per retry. Exit code 2 means partial/interrupted
discovery. Review the saved report. No claim is accepted from a truncated or
invalid model response. Missing models, wrong hashes, runtime incompatibility
or unavailable memory fail locally; there is no hosted fallback.

## Offline boundary

Inference uses an owned llama.cpp process bound to `127.0.0.1`, with proxies and
redirects disabled and an ephemeral API key. Single-state OS locks prevent
concurrent CLI/batch inference; a process monitor terminates inference above the
tier's RSS limit. Model files and runtime are local, and Hub downloads/telemetry
are disabled for the MCP child. These application controls do not replace host
network policy: use an approved firewall/air-gapped environment and run the full
workflow with outbound access blocked before claiming disconnected certification.
Loopback communication must remain available. The release report must say
whether OS-level egress denial was actually exercised.

No raw reasoning trace is written to reports or logs. Usage counters contain only
what the runtime exposes. Context limits and short output do not establish
reasoning-token savings. Indexing, storage, local compute and human review still
have costs. The existing 79.6% result is response text bytes only.

Generation uses bounded SSE streaming with a total deadline. Incomplete streams
and canonical output-validation failures cannot become accepted interpretations.
The native compact grammar constrains structure, action choices and citations; the
application also enforces canonical string lengths. This avoids relying on a
provider's JSON-schema implementation as the sole validation boundary.

The model receives no shell, review, approval, upload or import capability.
Source text is untrusted data, not instructions. Existing source-disclosure and
application authorization remain enforced by MCP. Local reports contain approved
metadata and should be stored with the same access restrictions as the index.

This controller is an operator CLI over MCP, not a new chat interface. Copilot
can continue using the existing MCP server. Fully disconnected operation uses
the local controller/model without signing into an online agent.

## Online escalation stays manual

Review `run.json`, choose specific evidence references, then explicitly approve
the exact question/reference packet:

```powershell
originspan --state D:\OriginSpanState --project local escalation-export RUN_ID `
  --question "Approved unresolved question" --refs E000001 E000003 `
  --output D:\ReviewedPackets\packet.json --approve
```

This exports at most 12 selected metadata items within 30 KB. It never invokes
a provider. The approval applies only to this packet, not future disclosure.
Original source excerpts are excluded from model packages and escalation packets.

## Qualification

`evaluations/offline-suite.json` contains 20 calibration and 40 held-out tasks,
using the existing pinned public source revisions and synthetic ambiguity corpus.
It is an implementation-authored draft, deliberately marked `owner_reviewed:
false`. Review expectations against the exact source subsets before accepting
release claims. Keep the original task checksum and record any reviewed revision.

The batch runner expects pre-indexed authorized sources, not network imports:

```powershell
python scripts/evaluate_local_models.py --suite evaluations/offline-suite.json `
  --source-map source-map.json --profile office-qwen-v1 --model-state D:\OriginSpanState `
  --split calibration --output calibration-runs.json
originspan evaluate-offline --suite evaluations/offline-suite.json `
  --runs held-out-runs.json --reviews reviewed-scores.json
```

A source-map entry has `state`, `project`, `application`, `analysis`; source IDs
are `bank`, `petclinic`, `saleor`, `ambiguity`. Profiles are verified before use.
Use the review template; scores bind to exact task questions and run IDs. Do not
mix model profiles in a qualification result. Missing tasks/reviews never pass.

Answer gates: precision >=95%, required-finding recall >=90%, ambiguity handling
>=90%, accepted local completion >=80%, all citations valid and all security
judgments pass. Separately test scope isolation, prompt injection, cancellation,
restart/resume, no outbound access, memory exhaustion and the 10-minute reference
workstation banking-report target. Laptop smoke results are not office SLOs.

Fine-tuning is conditional on recurring held-out failures after retrieval and
prompt improvements. Use approved synthetic/reviewed data with repository-level
train/test separation. No training is performed or claimed by this release.

For a short, reproducible protocol probe (not a general accuracy benchmark):

```powershell
python scripts/evaluate_semantic_probes.py --suite evaluations/heldout-semantic-probes.json `
  --state D:\OriginSpanState --profile laptop-qwen-v1 --output probe-results.json
```

The expected labels, citation requirements and checksums are explicit. The score
requires exact labels, sufficient citations and unresolved status for ambiguity
or absent context in field-annotation mode. A correct
paraphrase can fail this protocol test; do not report it as semantic inaccuracy.

## Research references

- [llama.cpp local inference](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)
- [Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B)
- [Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B)
- [Qwen3-Coder-30B-A3B](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct)
- [Devstral Small 2 24B](https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512)
- [PEFT LoRA concepts](https://huggingface.co/docs/peft/main/en/conceptual_guides/lora)

These primary sources informed runtime/profile choices; they are not evidence
that any candidate wins the frozen discovery tasks.
