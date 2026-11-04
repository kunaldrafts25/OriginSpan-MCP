# OriginSpan 0.4 discovery workflow

OriginSpan supplies persistent, inspectable evidence to the customer's AI. The
goal is better discovery with the same model, not replacing or outperforming a
foundation model independently. No server LLM or additional model subscription
is required. Neural retrieval is still optional and is disabled in the free
Render demo. The model interprets unfamiliar terminology from fields, definitions,
code usage and relationships; field names are not forced into a fixed glossary.

## Changes

- `get_complete_discovery`: a bounded overview of documentation, APIs, architecture,
  declared fields and their code occurrences, ORM operations, SQL, transformations,
  coverage, and a draft C360 handoff.
- `get_discovery_section`: explicit pagination for purpose, APIs, fields, ORM,
  transformations, documentation and open questions. One user request can involve
  multiple tool calls. Overview size is not a completeness claim.
- `complete_application_discovery`: a reusable MCP prompt guiding that workflow.
- Multi-document Kubernetes YAML and ConfigMap/consumer/source-environment links.
  Conflicting destinations remain unresolved. Edges cite source, configuration,
  and consuming deployment evidence; they do not prove live traffic.
- Static SQLAlchemy Table/Column declarations, insert/select/update/delete and
  predicates; explicit JPA field mappings and supported single-entity JPQL.
- Python field-key occurrences, hashing, token operations, sanitization, numeric
  scaling and lookup enrichment. These are static patterns; scaling alone does
  not prove currency units. Same-name field usage is a candidate association.
- Exact DDL field lines, actionable graph-focus validation, and metadata-only
  search fallback when requested source excerpts are forbidden.
- Typed discovery indexes allow native section pagination. Large scans retain
  explicit budgets. Unsupported languages, runtime behavior, ORM features and
  unindexed artifacts remain gaps.

## Validation

See `validation-results/discovery-upgrade/banking.json` for the frozen banking
checks and `banking-dossier.json` for the actual overview. The original reviewed
39 routes, three tables and 23 DDL columns remain intact. The checks also cover
five frontend service connections, users/contacts ORM operations, all eight
supported JPQL statements, field usage, transformation evidence and YAML coverage.

The same source extraction does not establish host-answer precision or recall.
Repeat the Copilot comparison with a fresh chat, identical model, source revision,
instructions and context budget. Record unsupported claims, citation validity,
completed tasks, tool calls, response bytes and wall time. Separate indexing time
from query time. Provider tokens/costs are unknown unless the client exposes them.

## Deploy and reanalyze

Use the existing `Dockerfile.render` and the current Render environment. No new
database, token, OIDC provider or model is required for the sqlite-demo deployment.
After deploying this code, start a new Copilot chat so tool discovery sees 0.4.
Existing source snapshots must be analyzed again to obtain the new facts. Cached
extraction is versioned, so old facts cannot silently stand in for new extraction.
The free SQLite demo still resets after restarts/redeployments; register and
upload the approved source again when it resets. Verify the real `/readyz`, then
MCP initialization and a completed banking analysis before presenting.

## One user prompt

```text
Use originspan-render for project demo. Locate Bank of Anthos and its latest
completed analysis. Start with get_complete_discovery. Complete discovery of
application purpose, APIs, service/database architecture, all declared fields,
demographic-related meanings and usage, SQL/ORM lineage and transformations.
Follow the relevant get_discovery_section, SQL and coverage continuation pages.
Use definitions and code context to interpret unfamiliar names; leave ambiguous
meanings unresolved. Cite precise source locations. Separate source facts,
candidate relationships and AI interpretations. Produce an architecture diagram
and a reviewable C360 handoff, identifying missing target-schema information.
State extraction, retrieval and coverage limits. Do not read customer records,
execute application code, change files or approve mappings.
```

## C360 boundary

The handoff identifies the application/snapshot and points to source field
inventory, evidence and unresolved questions. It does not impose the synthetic
C360 schema, invent a customer target model, approve business mappings, or write
to a target system. Supply the approved C360 target schema to propose mappings;
keep proposals separate from observed facts and separately reviewed knowledge.
