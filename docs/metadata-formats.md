# Approved metadata imports

Place metadata next to source in an approved folder or ZIP. A snapshot may contain
metadata alone. All artifacts are subject to the same per-file, total-size and
authorization limits. Generic JSON configuration is inventoried but not treated
as a data dictionary. Arrays of record values are not indexed.

OpenAPI, AsyncAPI and JSON Schema imports traverse declared `properties`, retain
types/descriptions and unresolved `$ref` strings, and skip examples/defaults/enums.
Remote references are not fetched. Inline recursive YAML aliases are bounded.
Definitions in Markdown/text are chunked with line provenance; customers must
approve them as metadata rather than customer-record exports.

For DataHub/OpenMetadata or other catalogs, export/normalize approved metadata to:

```json
{
  "format":"diamond-catalog-1",
  "assets":[{
    "name":"agreement",
    "description":"Commercial agreements",
    "owner":"Legal operations",
    "terms":["contract"],
    "fields":[{"name":"X17","description":"Renewal deadline"}]
  }]
}
```

This first adapter retains assets, descriptions, owner, terms and fields. Native
vendor APIs, catalog policy evaluation and arbitrary vendor lineage-export
formats are not implemented. Normalize supported metadata explicitly; do not
point DIAMOND at a production database merely to discover catalog fields.

Binary `.scip` files support document paths, symbol definitions/references,
documentation and both legacy packed and typed occurrence ranges. The bounded
reader skips unknown protobuf fields and does not implement every SCIP message.
Referenced documents must be present in the approved snapshot. It does not run
the compiler, trust the index as fresh, or infer reference semantics absent from
the supplied index.

Normalized compiler/LSP JSON can also be imported:

```json
{
  "format":"diamond-lsp-1",
  "symbols":[{
    "name":"Agreement.renew",
    "symbol":"approved-language-server-symbol-id",
    "documentation":"Renews an agreement",
    "location":{"file":"agreement.py","line":12}
  }]
}
```

`diamond-scip-1` accepts the same normalized symbol shape. These locations are
retained as supplied metadata and are not equivalent to parsed source citations.
Direct SolidLSP execution is deferred; approved integrations can produce this
format without making DIAMOND install servers or run repository scripts.
