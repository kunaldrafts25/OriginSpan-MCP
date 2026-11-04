# Third-party notices

## Optional offline reasoning

The wheel does not bundle llama.cpp executables or reasoning-model weights.
Operator-provisioned llama.cpp is MIT-licensed; retain its exact release notices
and notices for bundled runtime libraries. Qwen3.5-2B, Qwen3.8-27B,
Qwen3-Coder-30B-A3B-Instruct and Devstral-Small-2-24B-Instruct-2512 are candidate
models with Apache-2.0 upstream licensing. Review the exact chosen conversion,
revision, license and redistribution conditions. A third-party GGUF conversion
is not an official upstream artifact. The local profile records operator-supplied
license metadata; hashing a model does not establish license compliance.

## Core dependencies

OriginSpan MCP is distributed under Apache-2.0 (see LICENSE). Dependency packages
retain their own licenses and notices; installing a dependency does not transfer
its copyright. No competing discovery server is copied into this package.

Core runtime dependencies include the official MCP SDK (MIT), SQLAlchemy (MIT),
SQLGlot (MIT), Tree-sitter and Java/Kotlin grammars (their package licenses),
HTTPX (BSD-3-Clause), PyJWT (MIT), PyYAML (MIT), Uvicorn (BSD-3-Clause) and optional
Psycopg (LGPL-3.0-or-later with its distribution conditions). Consult the exact
installed distributions for complete notices and transitive dependencies.

Optional neural dependencies include Sentence Transformers, Transformers,
PyTorch, Hugging Face Hub and their transitive packages under their respective
licenses. The explicitly downloaded multilingual-e5-small model is MIT-licensed;
its model card and license files remain with the approved model directory.

The SCIP importer follows the public SCIP protobuf schema (Apache-2.0); it is a
small independently written bounded wire reader, not a bundled generated indexer.

Public evaluation inputs are separate from runtime source: Bank of Anthos and
Spring Petclinic (Apache-2.0), and the selected Saleor files (BSD-3-Clause). Fetch
scripts preserve selected metadata only and record immutable source revisions.
Original upstream notices must accompany redistribution of those source files;
the runtime wheel does not bundle these downloaded fixtures.

Codanna (Apache-2.0) is an opt-in comparison executable, retained in ignored test
storage with available release notices. It is not a runtime dependency. Serena's
application is GPL-3.0-or-later and is not included. Its separately MIT-licensed
SolidLSP is not currently bundled either.


## Bundled public Bank of Anthos demo

The Render image includes a metadata/source snapshot of GoogleCloudPlatform/bank-of-anthos
at db35fea9fd090150e2398106aadb475576f80d94, licensed Apache-2.0. Original
copyright notices remain in the source files. The complete upstream license is
in demo/BANK_OF_ANTHOS_LICENSE and inside demo/bank-of-anthos.zip.
Only this approved public sample is restored in explicit sqlite-demo/token mode.
