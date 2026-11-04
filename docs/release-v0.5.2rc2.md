# OriginSpan 0.5.2rc2 pilot release

OriginSpan exposes persistent source evidence through MCP and adds optional local-model discovery over an already completed analysis. The Windows pilot bundle installs the application and pinned dependencies without a package index. It includes public-sample evidence and installation instructions; model assets are provisioned separately.

## Public distribution repository

This release is maintained in `kunaldrafts25/OriginSpan-MCP`, separate from the original deployment repository. It adds package metadata and a checksum-verified PyPI publishing workflow. PyPI publication requires the owner to configure a pending trusted publisher; no registry publication is claimed before that connection.

## Included

- Standalone MCP, approved source/schema/metadata discovery and readable evidence-backed reports.
- Optional managed loopback llama.cpp reasoning with no online provider fallback.
- Typed unapproved field interpretations, bounded context, source-reference validation, explicit uncertainty, checkpoint/resume and recorded usage.
- Windows x64/Python 3.13 installer, hash-pinned dependency wheelhouse, file manifest and installation receipt.
- Approved public Bank of Anthos sample and original license; pinned model/runtime reference; technical reproduction and prior validation records.
- Clean-install verification script exercising isolated installation, real sample indexing and actual MCP stdio. Its executed result is attached separately when available.

## Evidence and scope

The prior local feasibility run covered 321 files and 4,076 evidence items, proposed 12 unapproved field meanings, and completed six sections with gaps. Its controller took 120.035 seconds after model startup inside a 3 GiB container with external networking blocked. Neural embeddings were disabled. Model weights were unchanged.

The earlier-to-final local pipeline comparison reported 70.9% fewer model input tokens and 63.74% fewer output tokens. This is not a measured Copilot comparison, a hidden-reasoning-token saving, an independent business accuracy result or a production SLA. The bundle's evidence entry point is `docs/evidence/README.md`; the repository record is `docs/validation-results/offline-feasibility/README.md`.

## Release validation

The prerelease regression suite passed **149 tests**, with one external PostgreSQL integration test skipped. The installer integrity tests reject tampered bytes and paths outside the package. The separately attached fresh-install report records the executed packaging check; the historical model feasibility report is not a substitute for fresh installation validation.

## Known limitations

- Pilot prerelease; no universal market-win, terabyte qualification or office-model certification.
- Windows dependency bundle is for x64/Python 3.13. macOS and Linux require separately qualified runtime/dependency assets.
- Python, model weights and llama.cpp binaries are not included. No automatic model download or fine-tuning.
- Current controller owns a single-slot loopback runtime; shared remote model hosting needs an authenticated adapter and additional qualification.
- Business meanings and target mappings remain unapproved; live customer-record profiling and target-system writes are outside scope.
- Summary context is bounded: ORM and documentation continuations remain visible. Model suggestions are not parser facts.
- External PostgreSQL integration and actual office identity/deployment behavior need their own tests.

## Installation and feedback

Follow `PILOT-QUICKSTART.md` in the extracted release. Start with the public sample and current small-model baseline. Use an approved source slice for a domain-owner-reviewed pilot. Retain the installation receipt and provide sanitized reproduction details for issues.
