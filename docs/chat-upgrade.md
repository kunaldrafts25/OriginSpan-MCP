# OriginSpan 0.5 — discovery designed for AI chat

The second Copilot comparison exposed a conversation problem: the 0.4 run made
44 MCP calls, referenced temporary output files 13 times, and ran five PowerShell
JSON-conversion commands. Purpose and documentation repeated the same inventory;
normal-file coverage pages dominated the workflow. The final answer omitted the
diagram and treated caller-supplied SQL analysis as application evidence.

Those are observations from one supplied transcript, not comparative latency or
token measurements. Do not publish the private transcripts as benchmark fixtures.

## Conversation contract

- Model-facing `content` is Markdown: prose, diagrams, tables and short citations.
  Native `structured_content` retains machine-readable evidence for SDK clients.
  There is no frontend required for MCP use.
- `get_session_context` supplies only the identity's authorized project names.
  `discover_application(project_id, application_name)` selects an unambiguous
  authorized application and its latest completed analysis. The report includes
  explicit application/snapshot IDs in structured data.
- Start with the overview. Request only relevant topics: architecture, fields,
  SQL, flows, handoff or coverage. More detail uses explicit offsets. Do not page
  through every deployment document or normal file just to explain an application.
- Tool titles, source labels and tables use readable language. Opaque identifiers
  remain available for drill-down; they are not intended as final-answer prose.
- Source facts, supported interpretations and unresolved business questions are
  separate. Review applies to business mappings, not reapproving an obvious
  declaration or source label.
- The report text is bounded, retains source citations, and flags display limits.
  Representative flows are explicitly identified as samples rather than complete
  flow coverage. Unsupported artifacts are excluded, not mislabeled parser failures.

Copilot controls its own tool cards and final-message rendering. An MCP cannot
remove those cards or force every client to render Mermaid. OriginSpan supplies
readable text and instructions; final host-model answer quality must still be
tested in Copilot.

## Evidence fixes

- HTML input/select/textarea declarations and labels are indexed without retaining
  input defaults or option values. Explicit label-for-ID binding is distinguished
  from a candidate label-for-name association in imperfect HTML.
- Form fields missing a same-name schema declaration are flagged for investigation.
  That does not by itself prove the application discards those fields.
- Static assignments and explicit string-formatting/concatenation preserve
  field-to-variable context, including name construction. Arithmetic is not
  automatically classified as string concatenation.
- Overview transformations prefer application code over tests, fixtures and load
  generators. Full source facts remain available in the focused flows topic.
- `get_sql_lineage(query_id=...)` binds lineage to an observed source query.
  The query ID can be an unambiguous scoped 12-character prefix. Supplying `sql`
  instead explicitly produces hypothetical query analysis: an AST cannot prove
  that a caller-supplied SQL statement occurs in the application.

## Programmatic compatibility

Use the SDK's native structured result, with a legacy fallback:

```python
data = result.structured_content
if data is None:
    data = json.loads(result.content[0].text)
```

Older v1 clients use the camelCase property `structuredContent`. A JSON-text-only
deployment can set `ORIGINSPAN_MCP_OUTPUT=json`; chat is the default. The viewer's
HTTP endpoints and direct analysis-engine interfaces keep their structured APIs.

The channel separation follows the official Python SDK documentation:
https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/servers/structured-output.md
The MCP tool specification describes structured output and recommends serialized
JSON as a backward-compatibility text convention; this release intentionally
offers a documented JSON mode while defaulting chat content to readable Markdown:
https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/tools.mdx

## Validation and limits

See `validation-results/chat-upgrade/banking-chat.json` for same-analysis wire-text
comparison and `banking-chat.md` for the actual human-readable report. The overview
SDK path uses two calls (authorized context and application report). That is not a
promise that Copilot will make exactly two calls for a detailed investigation.

The banking source baseline preserves 39 declared routes, three tables and 23 DDL
columns. Source now includes HTML metadata, so its coverage differs from 0.4.
The constrained-container report verifies the default Render image at 512 MiB.
These checks do not establish a premium-model answer-quality win, terabyte
capacity, or universal discovery completeness. No individual records are profiled.

## One user prompt

```text
Use OriginSpan to discover Bank of Anthos in project demo. Start with
discover_application and produce an organized chat report: application purpose,
architecture diagram, APIs, demographic-related fields and usage, SQL/ORM lineage,
transformations, coverage gaps and a draft C360 handoff. Expand focused topics
where detail is missing. Cite sources, distinguish facts from interpretations,
and use source query references for lineage. Do not dump raw tool data or repeat
all documentation/coverage pages. Do not modify files or approve mappings.
```

Deploy with the existing Render configuration; no new model or authentication
setup is needed. Reimport/reanalyze the approved snapshot to obtain HTML and new
flow metadata. Free SQLite storage remains ephemeral. Start a new Copilot session
after deployment; if its tool inventory is stale, restart the MCP connection.
