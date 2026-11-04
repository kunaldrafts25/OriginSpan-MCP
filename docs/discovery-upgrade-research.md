# Evidence-first discovery upgrade

The November 2 Bank of Anthos comparison showed that the MCP-assisted answer was
less detailed than workspace inspection. Preserve those missing findings as
regression tasks before changing extraction. Do not claim a model or market win.

## Research and decisions

- Serena: https://github.com/oraios/serena — symbol context and references supplied
  to the host LLM. Keep optional compiler integrations; do not execute client builds.
- Codanna: https://github.com/bartolli/codanna — correlated context reduces repeated
  search/read loops. Provide a bounded dossier and targeted section pagination.
- CodeGraphContext: https://codegraphcontext.github.io/concepts/architecture/ —
  explicit code relationships complement search. Persist configuration references,
  field occurrences and ORM operations as inspectable source facts.
- SQLGlot: https://sqlglot.com/sqlglot/lineage.html — schema/source context is needed
  for column resolution. Resolve supported JPQL only with explicit, unambiguous JPA
  mappings; unresolved entity/query features must remain visible.

These are documented designs, not executed competitor benchmarks. No competitor
code is copied. Extraction remains model-free; neural retrieval remains optional.
The host AI can interpret unfamiliar concepts from all declared fields and cited
documentation. Hint categories are retrieval aids, never automatic classifications.

## Frozen banking regression expectations

1. Multi-document Kubernetes manifests parse without single-document errors.
2. Frontend environment references resolve through service-api-config to userservice,
   contacts, balancereader, transactionhistory and ledgerwriter with file/line evidence.
3. users/contacts SQLAlchemy select, insert and filters appear as ORM operations.
4. Transaction entity maps transactionId/fromAccountNum to TRANSACTION_ID/FROM_ACCT.
5. Explicitly mapped, supported JPQL queries resolve to physical column dependencies.
6. Declared demographic-adjacent fields have precise DDL lines and consuming code
   occurrences, with names/identifiers separate from birth/location candidates.
7. Decimal currency to integer cents, password hashing, JWT construction and
   contact-label lookups are discoverable as static operations, not runtime proofs.
8. One complete-discovery tool returns architecture, purpose documentation, fields,
   query/ORM context, transformations, gaps, and a nonapproved C360 handoff.
9. Budget limits and continuations are explicit; source excerpts disabled does not
   disable metadata discovery. Missing graph focus gives an actionable error.
10. No application imports/execution, no record profiling, and no cross-app access.

## Evaluation boundary

Run the same frozen source and model with and without the upgraded MCP. Measure
task coverage, citation accuracy, unsupported claims, elapsed time and returned
bytes. Include cold analysis separately. Existing transcripts do not provide
reliable latency, token or cost measurements. Passing structural regressions is
necessary; it is not an executed comparison with a premium Copilot model.

The alternative POC is a discovery report/viewer over this same engine; a report
does not need a separate chatbot, fine-tuning, or a replacement for Copilot.
