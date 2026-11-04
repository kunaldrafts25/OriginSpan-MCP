# Offline discovery feasibility

OriginSpan completed discovery of the pinned public Bank of Anthos source while
the operating system denied external networking. The model weights were unchanged.
This demonstrates local workflow feasibility, with business-quality qualification
still open. Results below are executed checks, not estimates.

| Measured item | Final run |
| --- | --- |
| Source | 321 files; 4,076 evidence items |
| Sections completed | Six, with explicit coverage gaps |
| Inventory | 67 field records; selected semantic evidence is summarized |
| Field meanings | 12 source-anchored, unapproved local-model annotations |
| Local model | Qwen3.5-2B Q4_K_M, llama.cpp b11960 |
| Controller after model startup | 120.0 seconds |
| Cold test including indexing, model provisioning/verification and startup | 231.4 seconds |
| Indexing | 23.8 seconds |
| Input / output tokens | 4,141 / 784 |
| Model calls | 3 |
| Memory | Model peak 2233.4 MiB RSS; entire container capped at 3 GiB |
| Regression | 142 passed, one external PostgreSQL integration test skipped |

See the [actual report](bank-report.md), [raw proof](bank-proof.json) and
[complete measured record](executed.json).

## What improved

- Compact inputs retain unfamiliar metadata and remove internal hashes, duplicate
  source locations and retrieval bookkeeping from the model prompt.
- Architecture and SQL use recorded facts, including separate value lineage and
  join/filter/order dependencies. Missing approved target schemas remain explicit.
- Local generation uses bounded SSE streaming, actual usage counters and a total
  deadline. Incomplete responses are rejected; raw reasoning is not retained.
- Citation choices are limited to supplied references. Application validation
  also enforces canonical structure, lengths and tool-action permissions.
- Field discovery prioritizes declarations and preserves identical names on
  different entities. Follow-up context cannot replace fields with generic
  application documentation. Freeform field claims must name source fields;
  typed annotations must reference valid field IDs. These are consistency checks,
  not proof that a proposed business meaning is correct.
- Selected fields receive validated annotations with source IDs, short proposed
  meanings, unreviewed status and citations. The renderer takes names/entities
  from the corpus. Omitted annotations become unresolved; the model cannot
  approve a mapping. Native compact grammars fix field IDs and source citations
  while leaving the proposed business meaning to the local model.
- Explicit uncertainty labels cannot remain tagged as hypotheses. Distinct
  explicit definitions with the same field name and compatible entity scope
  trigger unresolved review, with the competing evidence retained. Compatible
  definitions can differ in wording; this conservative guard is not a general
  natural-language contradiction detector. It does not merge different entities.

The original completed banking run reported 14,231 input and 2,162 output tokens
over six model calls. The final workflow used **70.9% fewer input
tokens and 63.74% fewer output tokens** on the same pinned source
and model weights. It retains all six sections by keeping parser facts directly.
This is a local pipeline comparison, not a measured Copilot cost comparison.
Unavailable reasoning-token counts remain null. No hidden-token saving is claimed.

An earlier streamed run completed its controller in 113.421 seconds using 3,670
input and 475 output tokens. Later iterations changed field context, validation
and representative SQL selection. Both results are retained; the fastest earlier
run is not presented as the final result or a production SLA. The old Windows
baseline and Linux container runs also have different runtime environments.

## What the semantic probes establish

The final calibration check passed 10/10
and the second frozen-set recheck passed 10/10.
See [calibration responses](calibration.json) and [all recheck responses](probe-recheck.json),
including failed cases. The production field-annotation interface is exercised.
The criterion requires an exact expected label, supporting citations and an
unresolved status for missing/conflicting evidence. Correct paraphrases can fail this protocol
criterion, so it must not be equated with general semantic accuracy.

A separate, explicitly post-hoc formatting check normalizes only case,
spaces, hyphens and underscores. With citation/status requirements unchanged,
it passes 10/10
calibration cases and 10/10
recheck cases. It does not equate synonyms or change expected meanings. Raw
strict scores remain above; this secondary check is not independent validation.

These are maintainer-authored synthetic cases, not independent domain-owner
review. The second set was initially unexecuted, then inspected and rechecked
during development. It is no longer an untouched release qualification set.
The separate 60-task draft still needs owner review and complete model evaluation.

The current probe mode constrains the meaning to the supplied candidate labels
plus unresolved/ambiguous outcomes. These are pipeline decisions: both local
inference and host consistency guards contribute. They are not model-only
accuracy measurements. This mode was added after free-phrase answers exposed
formatting ambiguity; [earlier free-phrase calibration](calibration-unconstrained.json)
and [earlier free-phrase rechecks](recheck-unconstrained.json) remain available.
The original frozen fixture checksums and expected meanings were not edited.

The final uncertainty/definition guards were replayed against all twelve saved
bank annotations. Their meanings, statuses and citations remained identical.
This replay did not run new inference or create a new latency measurement.

## Limits and the next weight decision

This run used structural/lexical retrieval and local model interpretation;
optional neural embeddings were disabled. The inventory does not imply that every
field has a reviewed business meaning. There are still uncollected ORM/document
pages, source-analysis limitations and unapproved model hypotheses. No live
customer records, application code execution or target-system writes were used.

Model weights were not trained or fine-tuned. The current small model remains
unqualified: safe abstentions and occasional weak interpretations require review.
The next model step is a larger office-workstation candidate evaluated against
owner-approved tasks, latency and memory budgets. Fine-tune only recurring errors
using approved training examples with repository-level train/test separation.

No superiority over Copilot, office deployment certification, or terabyte-scale
performance is established by this POC.

## Reproduce

Use the [offline setup guide](../../offline-discovery.md) to provision licensed,
checksum-verified local model/runtime files. The reasoning controller needs no
online agent or model API key. On the prepared demonstration machine:

```powershell
& 'D:\diamond ai\originspan-offline-validation\run-offline-demo.ps1'
```

`scripts/validate_offline_bank.py` is the public-source container check. Its
conventions are a read-only checkout mounted at `/repo`, an output directory at
`/results`, approved assets under `.runtime/offline-discovery`, and Docker
`--network none --cpus 4 --memory 3g --memory-swap 3g --read-only` with a writable
512 MiB `/tmp`. The verification image also needs `libgomp1`. Provision dependencies
before disconnecting; no packages or weights are downloaded during discovery.

Engineering references: [pinned llama.cpp server protocol and timings](https://github.com/ggml-org/llama.cpp/blob/b11960/tools/server/README.md),
[grammar performance/validation guidance](https://github.com/ggml-org/llama.cpp/blob/b11960/grammars/README.md),
and the [Qwen3.5-2B model card](https://huggingface.co/Qwen/Qwen3.5-2B).
