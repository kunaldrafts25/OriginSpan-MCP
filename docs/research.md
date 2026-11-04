# Research and reuse decisions

Research recorded on 1 November 2026 from primary repositories, official docs,
release metadata and licenses. Documented capabilities are not benchmark results.
Executed comparisons are recorded separately in validation artifacts.

| Project | Observed version / license | Relevant decision |
|---|---|---|
| [Serena](https://github.com/oraios/serena) | 2.1.0; application GPL-3.0-or-later | Do not copy or bundle the application. SolidLSP has a separate MIT grant; direct integration remains optional work. |
| [CodeGraphContext](https://github.com/CodeGraphContext/CodeGraphContext) | 0.5.7; MIT | Reference for persistent syntax/compiler graph workflows; no mandatory graph database. |
| [Codanna](https://github.com/bartolli/codanna) | 0.17.1; Apache-2.0 | Executed local semantic-context baseline; binary archive checked against official SHA256. |
| [semantic-code-mcp](https://github.com/smallthinkingmachines/semantic-code-mcp) | 0.4.1; MIT | Hybrid retrieval and reranking reference; no bundled copy. |
| [Claude Context](https://github.com/zilliztech/claude-context) | MIT | Vector retrieval reference; no mandatory Milvus service or embedding API. |
| [ast-grep MCP](https://github.com/ast-grep/ast-grep-mcp) | MIT | Targeted syntax extraction reference; pattern matches do not establish business intent. |
| [DataHub MCP](https://github.com/acryldata/mcp-server-datahub) | 0.8.0; Apache-2.0 | Approved catalog exports join the evidence corpus; no required catalog deployment. |
| [OpenMetadata](https://github.com/open-metadata/OpenMetadata) | observed 2.0.7-release; Apache-2.0 | Business context/glossary reference; explicit normalized metadata import. |
| [DBHub](https://github.com/bytebase/dbhub) | 1.5.0; MIT | Future database capability reference; live queries excluded. |
| [Google MCP Toolbox](https://github.com/googleapis/mcp-toolbox) | Apache-2.0 | Narrow explicit source capabilities; no general command/SQL execution tool. |
| [SCIP](https://github.com/scip-code/scip) | Apache-2.0 | Bounded protobuf occurrence import; no compiler process launched. |

The [Serena license](https://github.com/oraios/serena/blob/main/LICENSE) separates
GPL application code from MIT SolidLSP. They must not be described as one MIT
component. No GPL application code is incorporated here.

LSP resolves programming references; embeddings retrieve related content; an LLM
interprets business meaning. DIAMOND keeps these claims distinct. Parsing and
nearest-neighbor similarity are not business validation.

The selected [multilingual-e5-small model](https://huggingface.co/intfloat/multilingual-e5-small)
is MIT-licensed and pinned at revision
`614241f622f53c4eeff9890bdc4f31cfecc418b3`. It is an initial multilingual baseline,
not a claim of code-search superiority. Queries/passages use the model's prescribed
prefixes; weights load locally without remote code or API fallback.

The official [Python MCP SDK](https://py.sdk.modelcontextprotocol.io/) is pinned to
2.3.0. DIAMOND uses `MCPServer`, official transports/auth machinery, reusable
prompts and ordinary host tool calls. The
[current sampling specification](https://modelcontextprotocol.io/specification/2026-07-28/client/sampling)
deprecates sampling; it is not a DIAMOND dependency. The SDK's
[older-client compatibility path](https://py.sdk.modelcontextprotocol.io/run/legacy-clients/)
is tested with explicit 2025-11-25 HTTP initialization and tool listing.

Research led to adapter boundaries, local inference, scoped retrieval, graph
context and review provenance. It did not justify copying every server or making
an unsupported universal ranking claim.
