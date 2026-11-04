# OriginSpan MCP

Source-backed application discovery for MCP clients, with optional offline local-model reasoning.

OriginSpan indexes approved code, SQL, schemas, contracts and metadata into a versioned evidence corpus. AI clients retrieve application context, architecture, fields and lineage through MCP. An optional local controller produces cited discovery reports using separately provisioned model assets.

**Status: pilot prerelease.** Business interpretations require review. No universal Copilot superiority, exhaustive discovery or enterprise-scale qualification is claimed.

## What it provides

- Source and configuration relationships with Mermaid architecture views.
- Field inventory, unfamiliar vocabulary context and unapproved semantic interpretations.
- Embedded SQL inventory, value lineage and separate selection dependencies.
- Persistent analyses, bounded retrieval, source citations and explicit coverage gaps.
- Local stdio MCP, plus separately qualified shared HTTPS deployment paths.
- Optional managed local llama.cpp inference with pinned assets and no online fallback.

The MCP evidence server itself does not require a reasoning model. An AI client may supply interpretation, or the local controller may use approved local model weights. Copilot can consume this MCP; it is not a replacement language model.

## Installation

Use the Windows x64 / Python 3.13 offline bundle from [GitHub Releases](https://github.com/kunaldrafts25/OriginSpan-MCP/releases). It includes dependency wheels, an installer, checksums and verification instructions. Python, model weights and llama.cpp binaries are separate prerequisites.

**PyPI publication is pending its account connection.** Once version 0.5.2rc2 is published:

```console
python -m pip install originspan-mcp==0.5.2rc2
originspan --help
```

The Python package declares Python 3.12 or newer. The bundled Windows installer specifically targets x64/Python 3.13; macOS and other accelerated runtime combinations require separate qualification.

## First discovery

Register an approved source root, analyze the application, then select that completed analysis through MCP or the local discovery controller. Application registration establishes which source the engine can access; a remote server cannot read a laptop filesystem from an arbitrary path.

Follow the [pilot quickstart](https://github.com/kunaldrafts25/OriginSpan-MCP/blob/main/docs/PILOT-QUICKSTART.md) and [local model guide](https://github.com/kunaldrafts25/OriginSpan-MCP/blob/main/docs/offline-discovery.md).

## Measured feasibility

Earlier local validation indexed 321 public sample files and 4,076 evidence items. A local 2B model completed six report sections with gaps under a 3 GiB container limit and externally blocked networking. The controller took 120.035 seconds after model startup; all twelve selected field meanings remained unapproved.

The improved local pipeline used 70.9% fewer reported input tokens than its earlier implementation. This is not a Copilot billing comparison or a measured hidden-reasoning saving. See the [executed evidence and limitations](https://github.com/kunaldrafts25/OriginSpan-MCP/tree/main/docs/validation-results/offline-feasibility).

## Boundaries

No live customer-record profiling, automatic source modification, arbitrary application-code execution or target-system writes are needed. Citation validity establishes reference existence, not business correctness. Source/metadata inputs remain untrusted evidence, never instructions.

## Project and license

[Source and issues](https://github.com/kunaldrafts25/OriginSpan-MCP) · [Releases](https://github.com/kunaldrafts25/OriginSpan-MCP/releases) · [Apache-2.0 license](https://github.com/kunaldrafts25/OriginSpan-MCP/blob/main/LICENSE)

Model/runtime assets and optional dependencies retain their own licenses. See the supplied third-party notices and exact asset provenance before redistribution.
