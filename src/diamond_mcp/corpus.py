"""Extract facts and field candidates without a fixed business glossary.

Code literals are never treated as customer records or business definitions.
Approved contract/dictionary descriptions are metadata, not parser-proved meaning.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re

import yaml
from .discovery_facts import yaml_documents

from .sources import redact, SECRET_NAME

EXTRACTOR = "diamond-corpus-1"


def tokens(value):
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return re.findall(r"[^\W_]+", value.lower(), re.UNICODE)


def normalize(value):
    return "".join(tokens(value))


def build_corpus(root: Path, structural: dict, cancel=lambda: None):
    items = []
    gaps = list(structural["limitations"])
    relationships = []
    seen = set()
    node_refs = {}
    source_hashes = {}

    def add(name, kind, file, line=1, description="", *, declaration="observed_fact", end_line=None,
            original_id=None, extra=None):
        if not name or SECRET_NAME.search(name) or len(name) > 300:
            return None
        # Stable within content snapshot; IDs cannot accidentally cite a different source.
        location = f"{file}:{line}:{kind}:{name}:{(extra or {}).get('entity','')}"
        id_ = "ev-" + hashlib.sha256(location.encode()).hexdigest()[:24]
        if id_ in seen:
            return id_
        seen.add(id_)
        description = redact(description)[:6000]
        if file not in source_hashes:
            source_hashes[file] = hashlib.sha256((root/file).read_bytes()).hexdigest()
        item = dict(id=id_, name=redact(name), kind=kind, file=file, line=line,
            end_line=end_line or line, description=description, declaration=declaration,
            extractor=EXTRACTOR, extractor_version="1", source_sha256=source_hashes[file],
            search_text=" ".join([name, " ".join(tokens(name)), file, kind, description]),
            **(extra or {}))
        items.append(item)
        if original_id:
            node_refs[original_id] = id_
        return id_

    for node in structural["nodes"]:
        ev = node.get("evidence", {})
        file = ev.get("file")
        if file and (root / file).is_file():
            add(node["name"], node["type"].lower(), file, ev.get("line") or 1,
                original_id=node["id"], extra={"service": node.get("service"), "source_node": node["id"]})
    for edge in structural["edges"]:
        if edge["source"] in node_refs and edge["target"] in node_refs:
            relationships.append(dict(source=node_refs[edge["source"]], target=node_refs[edge["target"]],
                                      type=edge["type"], status=edge.get("evidence", {}).get("verification_status", "CANDIDATE")))
    for query in structural["sql"]:
        ev = query["evidence"]
        for field in query.get("schema_fields", []):
            field_line = ev.get("line") or 1
            schema_source = (root / ev["file"]).read_text(encoding="utf-8-sig")
            table_match = re.search(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["`]?'+re.escape(field["table"])+r'["`]?\s*\(', schema_source, re.I)
            if table_match:
                section = schema_source[table_match.end():]
                next_table = re.search(r'CREATE\s+TABLE\b', section, re.I)
                if next_table: section=section[:next_table.start()]
                field_match = re.search(r'(?m)^[ \t]*["`]?'+re.escape(field["name"])+r'["`]?\s+\w', section, re.I)
                if field_match:
                    field_line = schema_source.count("\n", 0, table_match.end()+field_match.start())+1
                    while field_line <= len(schema_source.splitlines()) and not schema_source.splitlines()[field_line-1].strip():
                        field_line += 1
            add(field["name"], "declared_schema_field", ev["file"], field_line,
                f"Table {field['table']}; data type {field.get('data_type')}", extra={"entity": field["table"],"data_type":field.get("data_type")})

    paths = sorted(path for path in root.rglob("*") if path.is_file())
    compiler_symbols = {}
    for path in paths:
        cancel()
        rel = path.relative_to(root).as_posix()
        if path.suffix == ".scip":
            from .scip import read_index
            try:
                occurrences = read_index(path.read_bytes())
                for occurrence in occurrences:
                    location = occurrence["file"]
                    if not (root / location).is_file():
                        gaps.append(f"{rel}: SCIP document {location} is absent from approved snapshot")
                        continue
                    symbol = occurrence["symbol"]
                    id_ = add(symbol[-200:], "compiler_definition" if occurrence["definition"] else "compiler_reference",
                        location, occurrence["line"], occurrence["documentation"], extra={"compiler_symbol": symbol, "index_file": rel})
                    if id_:
                        compiler_symbols.setdefault(symbol, []).append((id_, occurrence["definition"]))
                gaps.append(f"{rel}: supplied SCIP occurrences; indexer provenance and freshness require review")
            except (ValueError, UnicodeError) as exc:
                gaps.append(f"{rel}: SCIP metadata not analyzed: {exc}")
            continue
        source = path.read_text(encoding="utf-8-sig")
        if path.suffix == ".py":
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    doc = ast.get_docstring(node) or ""
                    symbol_id = add(node.name, "symbol_context", rel, node.lineno, doc,
                        end_line=min(node.end_lineno or node.lineno, node.lineno + 80))
                    if symbol_id:
                        identifiers = sorted({n.id for n in ast.walk(node) if isinstance(n, ast.Name)} |
                                             {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)})
                        next(i for i in items if i["id"] == symbol_id)["search_text"] += " " + " ".join(identifiers)
                if isinstance(node, ast.arg):
                    add(node.arg, "parameter", rel, node.lineno)
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, (ast.Name, ast.Attribute)):
                            name = target.id if isinstance(target, ast.Name) else target.attr
                            value = node.value
                            kind, desc = "property_candidate", "Assignment declaration; business meaning unresolved"
                            if isinstance(value, ast.Call):
                                callee = ast.unparse(value.func)
                                if callee.endswith(("Column", "mapped_column")) or callee.endswith("Field"):
                                    kind, desc = "orm_field", "Explicit ORM declaration " + callee
                                    if value.args and isinstance(value.args[0], ast.Constant) and isinstance(value.args[0].value, str):
                                        desc += "; mapped column " + value.args[0].value
                            add(name, kind, rel, node.lineno, desc)
                if isinstance(node, ast.Dict):
                    for key in node.keys:
                        if isinstance(key, ast.Constant) and isinstance(key.value, str) and re.fullmatch(r"[A-Za-z_][\w.-]{0,100}", key.value):
                            add(key.value, "literal_key_candidate", rel, key.lineno,
                                "Observed code key; not a declared schema field", declaration="candidate")
                if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    add(node.slice.value, "literal_key_candidate", rel, node.lineno,
                        "Observed code lookup; not a declared schema field", declaration="candidate")
        elif path.suffix in {".java", ".kt"}:
            # Symbol-aware chunks include adjacent documentation and identifiers,
            # not executable repository plugins or literal record values.
            lines = source.splitlines()
            for node in [n for n in structural["nodes"] if n.get("evidence", {}).get("file") == rel
                         and n["type"] in {"CLASS", "METHOD", "DECLARED_PROPERTY"}]:
                line = node["evidence"].get("line") or 1
                context = "\n".join(lines[max(0, line - 10):line + 15])
                comments = " ".join(re.findall(r"(?m)^\s*(?://|\*|/\*+)(.*)$", context))
                id_ = add(node["name"], "symbol_context", rel, line, comments)
                if id_:
                    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', context)
                    next(i for i in items if i["id"] == id_)["search_text"] += " " + " ".join(tokens(text))
            # Explicit mappings only; unresolved naming strategies stay unresolved.
            pattern = r'@Column\s*\(\s*name\s*=\s*"([\w.]+)"[^)]*\)\s*(?:(?:private|public|protected|final|static)\s+)*(?:[\w<>?]+)\s+(\w+)'
            for match in re.finditer(pattern, source):
                add(match[2], "orm_field", rel, source.count("\n", 0, match.start()) + 1,
                    f"Explicit JPA column mapping: {match[1]}")
            gaps.append(f"{rel}: implicit ORM naming and runtime entity resolution are not compiler verified")
        elif path.suffix in {".json", ".yaml", ".yml"}:
            try:
                documents = [json.loads(source)] if path.suffix == ".json" else yaml_documents(source)
            except (ValueError, yaml.YAMLError):
                gaps.append(f"{rel}: metadata parse failed")
                continue
            for data in documents:
                if not isinstance(data, dict):
                    gaps.append(f"{rel}: record arrays are excluded; import schema metadata instead")
                    continue
                if any(k in data for k in ("openapi", "asyncapi", "$schema", "properties", "$defs", "definitions")):
                    visited = set()
                    def schema_walk(value, prefix="", depth=0):
                        if not isinstance(value, dict):
                            return
                        if id(value) in visited:
                            return
                        if depth > 50 or len(visited) > 20000:
                            gaps.append(f"{rel}: schema nesting/item limit reached")
                            return
                        visited.add(id(value))
                        properties = value.get("properties", {})
                        if isinstance(properties, dict):
                            for name, spec in properties.items():
                                if not isinstance(spec, dict):
                                    continue
                                add(name, "contract_field", rel, description=str(spec.get("description", "")),
                                    extra={"entity": prefix, "data_type": spec.get("type"), "reference": spec.get("$ref")})
                        for key, child in value.items():
                            if key in {"example", "examples", "default", "enum"}:
                                continue
                            if isinstance(child, dict):
                                schema_walk(child, prefix + "/" + key, depth + 1)
                    schema_walk(data)
                elif data.get("format") == "diamond-catalog-1":
                    # Explicit schema prevents accidentally ingesting exported records.
                    for asset in data.get("assets", [])[:2000]:
                        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
                            continue
                        add(asset["name"], "catalog_asset", rel, description=str(asset.get("description", "")),
                            extra={"owner": str(asset.get("owner", ""))[:200], "terms": asset.get("terms", [])[:30]})
                        for field in asset.get("fields", [])[:2000]:
                            if isinstance(field, dict) and isinstance(field.get("name"), str):
                                add(field["name"], "catalog_field", rel, description=str(field.get("description", "")),
                                    extra={"entity": asset["name"]})
                    gaps.append(f"{rel}: catalog declarations are supplied metadata, not runtime-verified facts")
                elif data.get("format") in {"diamond-scip-1", "diamond-lsp-1"}:
                    for symbol in data.get("symbols", [])[:10000]:
                        if isinstance(symbol, dict) and isinstance(symbol.get("name"), str):
                            add(symbol["name"], "compiler_symbol", rel, description=str(symbol.get("documentation", "")),
                                extra={"compiler_symbol": symbol.get("symbol"), "supplied_location": symbol.get("location")})
                    gaps.append(f"{rel}: supplied compiler/LSP metadata; no language server or build was executed")
                else:
                    gaps.append(f"{rel}: generic configuration inventoried; values are not business definitions")
        elif path.suffix in {".md", ".txt"}:
            # Semantic chunks preserve document definitions, not arbitrary record rows.
            lines = source.splitlines()
            for start in range(0, len(lines), 30):
                chunk = "\n".join(lines[start:start + 35])
                if chunk.strip():
                    title = next((line.strip(" #\t") for line in lines[start:start + 35] if line.strip()), path.stem)
                    add(title[:200], "dictionary_context", rel, start + 1, chunk, end_line=min(start + 35, len(lines)))
        elif path.suffix not in {".sql", ".xml", ".toml", ".properties", ".kts", ".html", ".htm"} and path.name != "Dockerfile":
            gaps.append(f"{rel}: unsupported extraction format")

    for symbol, occurrences in compiler_symbols.items():
        definitions = [id_ for id_, definition in occurrences if definition]
        for id_, definition in occurrences:
            if not definition:
                for target in definitions[:10]:
                    relationships.append(dict(source=id_, target=target, type="COMPILER_REFERENCE", status="SUPPLIED_INDEX"))
    # Co-located evidence is useful context, explicitly not a runtime relationship.
    by_file = {}
    for item in items:
        by_file.setdefault(item["file"], []).append(item["id"])
    for ids in by_file.values():
        anchor = ids[0]
        for id_ in ids[1:]:
            relationships.append(dict(source=anchor, target=id_, type="CO_LOCATED", status="SOURCE_LOCATION"))
    return items, relationships, sorted({redact(gap) for gap in gaps})
