"""Compact semantic context; keep the original evidence in the saved run."""
from __future__ import annotations

import copy

VERSION = 'model-input-4'
BOOKKEEPING = frozenset({
    'id', 'file', 'line', 'end_line', 'evidence', 'mapping_evidence',
    'extractor', 'extractor_version', 'source_sha256', 'source_hash', 'snapshot',
    'snapshot_id', 'application_id', 'analysis_id', 'project_id', 'query_id',
    'retrieval', 'rrf_score', 'created_at', 'updated_at',
})
SENSITIVE = frozenset({'excerpt', 'source_value', 'options', 'default', 'password', 'token'})


def clean(value):
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()
                if key not in BOOKKEEPING | SENSITIVE and item is not None and item != '' and item != [] and item != {}}
    if isinstance(value, list):
        return [clean(item) for item in value]
    return value


def digest_evidence(evidence):
    # Do not apply a customer-field glossary, drop unfamiliar semantic keys, or
    # turn extracted metadata into business facts. Remove only bookkeeping.
    return [dict(ref=row['ref'], source=f"{row['file']}:{row.get('line', 1)}",
                 facts=clean(row.get('metadata', {}))) for row in evidence]


def cited_schema(schema, refs):
    # Large bounded string repetitions can make native GBNF sampling very slow.
    # Structure, item counts and citation enums remain grammar-constrained;
    # validate_interpretation enforces all canonical string bounds afterward.
    def grammar(value):
        if isinstance(value, dict):
            return {k: grammar(v) for k,v in value.items() if k not in {'minLength','maxLength'}}
        if isinstance(value, list):return [grammar(v) for v in value]
        return value
    value = grammar(copy.deepcopy(schema))
    if 'claims' not in value.get('properties',{}):return value
    if not refs:
        value['properties']['claims']['maxItems'] = 0
        return value
    value['properties']['claims']['items']['properties']['citations']['items'] = {
        'type': 'string', 'enum': sorted(set(refs))}
    return value
