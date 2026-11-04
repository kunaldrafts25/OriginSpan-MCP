"""Read-only, bounded multi-language discovery. Findings carry source evidence."""
from __future__ import annotations

from .compat import getenv
import ast
from bisect import bisect_left
import hashlib
import json
import os
from pathlib import Path
import re
from datetime import datetime, timezone
from typing import Any

from .sql_intelligence import analyze_sql

PROJECT = Path(__file__).resolve().parents[2]
SUFFIXES = {".html": "interface", ".htm": "interface", ".java": "java", ".kt": "kotlin", ".py": "python", ".sql": "sql",
            ".json": "configuration", ".yaml": "configuration", ".yml": "configuration",
            ".xml": "configuration", ".properties": "configuration", ".kts": "configuration", ".toml": "configuration"}
SKIP = {".git", ".venv", "node_modules", "build", "target", "__pycache__", ".runtime", ".gradle"}
GLOSSARY = {
    "customer_id": ("identity", "sensitive"), "customerid": ("identity", "sensitive"),
    "source_id": ("identity", "sensitive"), "first_name": ("identity", "sensitive"),
    "last_name": ("identity", "sensitive"), "full_name": ("identity", "sensitive"),
    "name": ("identity", "sensitive"), "birth_date": ("demographics", "sensitive"),
    "birth_dt": ("demographics", "sensitive"), "date_of_birth": ("demographics", "sensitive"), "dob": ("demographics", "sensitive"),
    "gender_code": ("demographics", "sensitive"), "nationality": ("demographics", "sensitive"),
    "marital_status": ("demographics", "sensitive"), "city": ("contact", "sensitive"),
    "postal_code": ("contact", "sensitive"), "address": ("contact", "sensitive"),
    "mobile": ("contact", "sensitive"), "email": ("contact", "sensitive"),
    "employer": ("employment", "sensitive"), "employment": ("employment", "sensitive"),
    "income_category": ("financial", "restricted"), "annual_income": ("financial", "restricted"),
    "membership_date": ("relationship", "sensitive"), "relationship": ("relationship", "sensitive"),
    "firstname": ("identity", "sensitive"), "lastname": ("identity", "sensitive"),
    "birthday": ("demographics", "sensitive"), "accountid": ("identity", "restricted"),
    "account_num": ("identity", "restricted"), "ssn": ("government_identifier", "restricted"),
    "passhash": ("authentication", "restricted"),
}


def allowed_root() -> Path:
    return Path(getenv("DIAMOND_REPOSITORY_ROOT", str(PROJECT / "sample-apps"))).resolve()


def safe_repository(root: Path | str, trusted_root: Path | None = None) -> Path:
    raw = Path(root)
    if raw.is_symlink() or raw.is_junction() or any(p.is_symlink() or p.is_junction() for p in raw.parents):
        raise ValueError("Symbolic-link repositories are not authorized")
    resolved = raw.resolve(strict=True)
    if not resolved.is_relative_to(trusted_root or allowed_root()) or not resolved.is_dir():
        raise ValueError("Repository is outside DIAMOND_REPOSITORY_ROOT")
    return resolved


def _revision(root: Path) -> str:
    # Read Git references directly. Discovery never starts a repository-controlled
    # process or loads Git configuration, hooks, helpers or fsmonitor commands.
    scope = allowed_root()
    for directory in (root, *root.parents):
        if not directory.is_relative_to(scope):
            break
        git = directory / ".git"
        if git.is_symlink() or git.is_junction() or git.is_file():
            return "worktree-reference-unresolved"
        head_path = git / "HEAD"
        if not head_path.is_file() or head_path.is_symlink() or head_path.stat().st_size > 1024:
            continue
        head = head_path.read_text(encoding="ascii").strip()
        if re.fullmatch(r"[0-9a-fA-F]{40,64}", head):
            return head
        if head.startswith("ref: "):
            ref = head[5:]
            target = (git / ref).resolve()
            if not target.is_relative_to(git.resolve()) or any(p.is_symlink() for p in (git / ref).parents if p.is_relative_to(git)):
                return "unresolved"
            if target.is_file() and target.stat().st_size <= 1024:
                revision = target.read_text(encoding="ascii").strip()
                if re.fullmatch(r"[0-9a-fA-F]{40,64}", revision):
                    return revision
            packed = git / "packed-refs"
            if packed.is_file() and not packed.is_symlink() and packed.stat().st_size <= 2_000_000:
                for line in packed.read_text(encoding="ascii").splitlines():
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == ref and re.fullmatch(r"[0-9a-fA-F]{40,64}", parts[0]):
                        return parts[0]
        return "uncommitted"
    return "uncommitted"


def _syntax_parser(language: str):
    from tree_sitter import Language, Parser
    if language == "java":
        import tree_sitter_java as grammar
    else:
        import tree_sitter_kotlin as grammar
    return Parser(Language(grammar.language()))


def scan_application(root: Path | str, *, trusted_root: Path | None = None, check_cancel=None, max_file_bytes=2_000_000) -> dict[str, Any]:
    root = safe_repository(root, trusted_root)
    revision = _revision(root)
    timestamp = datetime.now(timezone.utc).isoformat()
    result: dict[str, Any] = {"id": "", "revision": revision, "analyzed_at": timestamp,
        "repository": root.name, "files": [], "components": [], "nodes": [], "edges": [],
        "attributes": [], "rules": [], "sql": [], "limitations": [], "languages": []}
    fingerprint = hashlib.sha256()
    node_ids: set[str] = set()
    service_ports: dict[str, set[int]] = {}
    client_routes: list[dict] = []
    parsers = {}
    service_roots: set[str] = set()

    def service_for(path: str) -> str:
        candidates = [directory for directory in service_roots
                      if path.startswith(directory + "/")]
        return max(candidates, key=len) if candidates else path.split("/")[0]

    def evidence(path: str, line: int | None, extractor: str, status: str = "SOURCE_CONFIRMED"):
        return {"file": path, "line": line, "revision": revision, "extractor": extractor,
                "evidence_type": "static_source", "verification_status": status,
                "analyzed_at": timestamp, "ambiguities": []}

    def node(id_: str, type_: str, name: str, language: str, ev: dict, **extra):
        if id_ not in node_ids:
            result["nodes"].append({"id": id_, "type": type_, "name": name, "language": language,
                                    "evidence": ev, "service": service_for(ev["file"]), **extra})
            node_ids.add(id_)
        return id_

    def edge(source: str, target: str, kind: str, ev: dict, **extra):
        result["edges"].append({"source": source, "target": target, "type": kind, "evidence": ev, **extra})

    def sql_finding(analysis: dict, rel: str, ev: dict, suffix: str = ""):
        analysis["evidence"] = ev
        result["sql"].append(analysis)
        if analysis["status"] in {"partial", "unsupported"}:
            result["limitations"].append(f"SQL analysis {analysis['status']} in {rel}; inspect query limitations")
        statement_id = node(f"sql:{rel}{suffix}", "SQL_STATEMENT", Path(rel).name + suffix, "sql", ev)
        edge(f"file:{rel}", statement_id, "BELONGS_TO", ev)
        for table in analysis["tables"]:
            table_id = node(f"table:{table}", "TABLE", table, "sql", ev)
            edge(statement_id, table_id, "DEPENDS_ON", ev, ambiguity="Read/write semantics not yet classified")
        for column in analysis["columns"]:
            column_id = node(f"column:{rel}{suffix}:{column}", "COLUMN", column, "sql", ev)
            edge(statement_id, column_id, "READS", ev)
        result["rules"].extend({**rule, "rule_id": f"rule:{rel}{suffix}:{i}", "source_language": "sql", "evidence": ev}
                               for i, rule in enumerate(analysis["rules"]))

    # os.walk does not follow symlinks; also reject junctions/reparse points via resolution.
    paths: list[Path] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        if check_cancel:
            check_cancel()
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not (Path(current) / d).is_symlink()
                          and not (Path(current) / d).is_junction()
                          and (Path(current) / d).resolve().is_relative_to(root))
        for filename in sorted(files):
            path = Path(current) / filename
            if (path.suffix not in SUFFIXES and filename != "Dockerfile") or path.is_symlink() or filename.startswith(".env"):
                continue
            if not path.resolve().is_relative_to(root):
                continue
            if path.stat().st_size > max_file_bytes:
                result["limitations"].append(f"Skipped oversized file {path.relative_to(root).as_posix()}")
                continue
            paths.append(path)
            if len(paths) > 2000:
                raise ValueError("Repository exceeds 2000-file bounded scan limit")
    for path in paths:
        if path.name in {"Dockerfile", "pom.xml", "pyproject.toml", "build.gradle.kts"}:
            directory = path.parent.relative_to(root).as_posix()
            if directory != ".":
                service_roots.add(directory)
    for path in paths:
        rel = path.relative_to(root).as_posix()
        if check_cancel:
            check_cancel()
        content = path.read_bytes()
        fingerprint.update(rel.encode() + b"\0" + content)
        try:
            source = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            result["limitations"].append(f"Non UTF-8 file skipped: {rel}")
            continue
        language = SUFFIXES.get(path.suffix, "configuration")
        service = service_for(rel)
        for port_match in re.finditer(r'(?:server\.port\s*=\s*\$\{[^:}]+:|--port(?:\s+|"\s*,\s*"))(\d+)', source):
            service_ports.setdefault(service, set()).add(int(port_match.group(1)))
        file_id = f"file:{rel}"
        ev = evidence(rel, 1, "inventory")
        node(file_id, "MODULE", path.name, language, ev)
        result["files"].append({"path": rel, "language": language, "sha256": hashlib.sha256(content).hexdigest(),
                                "bytes": len(content), "evidence": ev})
        if language == "python":
            try:
                tree = ast.parse(source)
            except SyntaxError as exc:
                result["limitations"].append(f"Python syntax error {rel}:{exc.lineno}")
                continue
            for item in ast.walk(tree):
                line = getattr(item, "lineno", None)
                ref = evidence(rel, line, "python.ast")
                if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    id_ = node(f"{file_id}:{line}", "CLASS" if isinstance(item, ast.ClassDef) else "FUNCTION",
                               item.name, language, ref)
                    edge(id_, file_id, "BELONGS_TO", ref)
                    result["components"].append({"id": id_, "name": item.name, "language": language, "evidence": ref})
                    for decorator in item.decorator_list:
                        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                            if decorator.func.attr in {"get", "post", "put", "patch", "delete", "route"} and decorator.args:
                                if isinstance(decorator.args[0], ast.Constant) and isinstance(decorator.args[0].value, str):
                                    route = decorator.args[0].value
                                    methods = [decorator.func.attr.upper()]
                                    if decorator.func.attr == "route":
                                        methods = ["GET"]
                                        method_arg = next((k.value for k in decorator.keywords if k.arg == "methods"), None)
                                        if method_arg is not None:
                                            if not isinstance(method_arg, (ast.List, ast.Tuple)) or not all(
                                                isinstance(v, ast.Constant) and isinstance(v.value, str)
                                                for v in method_arg.elts):
                                                result["limitations"].append(f"Dynamic route methods unresolved in {rel}:{line}")
                                                continue
                                            methods = [v.value.upper() for v in method_arg.elts]
                                    for method in methods:
                                        endpoint = node(f"api:{rel}:{line}:{method}", "API_ENDPOINT", f"{method} {route}", language, ref, route=route, method=method)
                                        edge(file_id, endpoint, "EXPOSES", ref)
                                        edge(endpoint, id_, "CALLS", ref)
                elif isinstance(item, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in item.names] if isinstance(item, ast.Import) else [item.module or "relative"]
                    for name in names:
                        target = node(f"import:{name}", "PACKAGE", name, language, ref)
                        edge(file_id, target, "IMPORTS", ref)
                elif isinstance(item, ast.If):
                    expression = ast.get_source_segment(source, item.test) or ast.unparse(item.test)
                    result["rules"].append({"rule_id": f"rule:{rel}:{line}", "category": "condition_candidate",
                        "original_expression": expression, "dependent_fields": sorted({n.attr for n in ast.walk(item.test) if isinstance(n, ast.Attribute)}),
                        "source_language": language, "evidence": ref, "interpretation_status": "review_required", "approval_status": "pending"})
        elif language in {"java", "kotlin"}:
            default_ports = {int(m.group(1)) for m in re.finditer(r'https?://[A-Za-z0-9_.-]+:(\d+)', source)}
            for match in re.finditer(r'\.uri\(\s*"([^"]+)"', source):
                client_routes.append({"source": file_id, "service": service, "route": match.group(1),
                    "default_ports": default_ports,
                    "evidence": evidence(rel, source[:match.start()].count("\n") + 1, "rest-client-uri-literal", "CANDIDATE")})
            try:
                if language not in parsers:
                    parsers[language] = _syntax_parser(language)
                tree = parsers[language].parse(content)
                newline_offsets = [i for i, byte in enumerate(content) if byte == 10]
                if tree.root_node.has_error:
                    result["limitations"].append(f"Partial {language} syntax tree: {rel}")
                stack = [tree.root_node]
                while stack:
                    item = stack.pop()
                    stack.extend(reversed(item.children))
                    # Compute lines from byte offsets: repeatedly constructing native
                    # Point objects caused heap corruption in the Windows 0.26 binding.
                    ref = evidence(rel, bisect_left(newline_offsets, item.start_byte) + 1, f"tree-sitter-{language}")
                    if item.type in {"variable_declarator", "property_declaration", "formal_parameter"}:
                        identifier = item.child_by_field_name("name")
                        if identifier is None:
                            identifier = next((c for c in item.named_children if c.type in {"identifier", "simple_identifier", "variable_declaration"}), None)
                            if identifier is not None and identifier.type == "variable_declaration":
                                identifier = next((c for c in identifier.named_children if c.type in {"identifier", "simple_identifier"}), None)
                        if identifier is not None:
                            field_name = content[identifier.start_byte:identifier.end_byte].decode("utf-8")
                            field_id = node(f"field:{rel}:{item.start_byte}", "DECLARED_PROPERTY", field_name, language, ref)
                            edge(field_id, file_id, "BELONGS_TO", ref)
                    if item.type in {"class_declaration", "interface_declaration", "object_declaration", "method_declaration", "function_declaration"}:
                        name_node = item.child_by_field_name("name")
                        if name_node is None:
                            name_node = next((n for n in item.children if n.type in {"identifier", "simple_identifier", "type_identifier"}), None)
                        if name_node is None:
                            continue
                        name = content[name_node.start_byte:name_node.end_byte].decode("utf-8")
                        kind = "CLASS" if "class" in item.type or "interface" in item.type or "object" in item.type else "METHOD"
                        id_ = node(f"{file_id}:{item.start_byte}", kind, name, language, ref)
                        edge(id_, file_id, "BELONGS_TO", ref)
                        result["components"].append({"id": id_, "name": name, "language": language, "evidence": ref})
                    if item.type in {"if_statement", "if_expression"}:
                        condition = item.child_by_field_name("condition")
                        result["rules"].append({"rule_id": f"rule:{rel}:{item.start_byte}", "category": "condition_candidate",
                            "original_expression": content[(condition or item).start_byte:(condition or item).end_byte].decode("utf-8")[:1000],
                            "source_language": language, "evidence": ref, "interpretation_status": "review_required", "approval_status": "pending"})
                    if item.type in {"import_declaration", "import_header"}:
                        name = content[item.start_byte:item.end_byte].decode("utf-8").removeprefix("import ").strip().rstrip(";")
                        dependency = node("import:" + name, "PACKAGE", name, language, ref)
                        edge(file_id, dependency, "IMPORTS", ref)
                    if item.type in {"annotation", "marker_annotation"}:
                        # Keep the source bytes owned by this scan rather than asking
                        # the native binding to allocate another text representation.
                        text_ = content[item.start_byte:item.end_byte].decode("utf-8")
                        if re.match(r"@Query\s*\(", text_):
                            arguments = item.child_by_field_name("arguments")
                            query_node, native = None, False
                            for argument in arguments.named_children if arguments else []:
                                if argument.type == "element_value_pair":
                                    key = argument.child_by_field_name("key")
                                    value = argument.child_by_field_name("value")
                                    key_name = content[key.start_byte:key.end_byte].decode() if key else ""
                                    if key_name == "nativeQuery":
                                        native = value is not None and value.type == "true"
                                    elif key_name == "value":
                                        query_node = value
                                elif argument.type in {"string_literal", "binary_expression"}:
                                    query_node = argument

                            def constant_java_string(expression):
                                if expression is not None and expression.type == "string_literal":
                                    return json.loads(content[expression.start_byte:expression.end_byte].decode())
                                if expression is not None and expression.type == "binary_expression":
                                    operator = expression.child_by_field_name("operator")
                                    if operator is not None and content[operator.start_byte:operator.end_byte] == b"+":
                                        return constant_java_string(expression.child_by_field_name("left")) + constant_java_string(expression.child_by_field_name("right"))
                                raise ValueError("Dynamic Java query expression")
                            try:
                                query = constant_java_string(query_node)
                            except (ValueError, TypeError):
                                result["limitations"].append(f"Dynamic or unsupported Java string expression in @Query {rel}:{ref['line']}")
                                query = ""
                            if query and native:
                                # JDBC positional bind markers are normalized only for
                                # parsing, never executed or presented as original text.
                                parsed_query = re.sub(r"\?(\d+)", r":p\1", query)
                                analysis = analyze_sql(parsed_query)
                                analysis.update(query_language="SQL", original_expression=query,
                                                parameter_normalization="JDBC ?N -> named :pN")
                                sql_finding(analysis, rel, ref, f":{item.start_byte}")
                            elif query:
                                result.setdefault("non_sql_queries", []).append({"query_language":"JPQL", "expression":query,
                                    "status":"NOT_ANALYZED", "evidence":ref,
                                    "reason":"JPQL entity/property names are not physical SQL tables/columns"})
                        match = re.search(r'@(Get|Post|Put|Patch|Delete|Request)Mapping\s*\(\s*(?:value\s*=\s*)?"([^"]+)"', text_)
                        if match:
                            endpoint = node(f"api:{rel}:{item.start_byte}", "API_ENDPOINT", f"{match[1].upper()} {match[2]}", language, ref, route=match[2])
                            edge(file_id, endpoint, "EXPOSES", ref)
                            edge(endpoint, file_id, "BELONGS_TO", ref)
                result["limitations"].append(f"{language} syntax evidence only; compiler type/call resolution is not implemented")
            except ImportError:
                result["limitations"].append(f"{language} grammar unavailable; no syntax findings claimed for {rel}")
        elif language == "sql":
            analysis = analyze_sql(source)
            sql_finding(analysis, rel, evidence(rel, 1, "sqlglot"))
        # Names are candidates only. No values are read or used to infer characteristics.
        seen: set[str] = set()
        for match in re.finditer(r"\b[A-Za-z_][A-Za-z_0-9]*\b", source):
            name = match.group()
            normalized = name.lower()
            if normalized in GLOSSARY and normalized not in seen:
                seen.add(normalized)
                category, sensitivity = GLOSSARY[normalized]
                result["attributes"].append({"id": f"attribute:{rel}:{normalized}", "name": name,
                    "category": category, "sensitivity": sensitivity, "status": "DETERMINISTIC_MATCH",
                    "description": "Identifier matches synthetic glossary; business meaning requires review",
                    "reviewer": None, "evidence": evidence(rel, source[:match.start()].count("\n") + 1, "identifier-glossary", "CANDIDATE")})
        # URLs in comments/licenses are not application dependencies. Only the
        # syntax-backed client call extraction above contributes contract edges.
    normalize_route = lambda route: re.sub(r'\{[^}]+\}', "{}", route)
    for reference in client_routes:
        matches = [target for target in result["nodes"] if target["type"] == "API_ENDPOINT"
                   and target["service"] != reference["service"]
                   and normalize_route(target.get("route", "")) == normalize_route(reference["route"])
                   and reference["default_ports"] & service_ports.get(target["service"], set())]
        if len(matches) == 1:
            edge(reference["source"], matches[0]["id"], "DEPENDS_ON", reference["evidence"],
                 resolution="CANDIDATE_CONTRACT_MATCH", configuration="Default configured ports and normalized URI templates match; runtime overrides unverified")
        else:
            result["limitations"].append(f"Unresolved or ambiguous REST client route {reference['route']} in {reference['source']}")
    for reference in result.get("http_references", []):
        matched = False
        for target in result["nodes"]:
            if target["type"] == "API_ENDPOINT" and target.get("route") == reference["route"]:
                edge(reference["source"], target["id"], "DEPENDS_ON", reference["evidence"],
                     resolution="CANDIDATE_CONTRACT_MATCH")
                matched = True
        if not matched:
            result["limitations"].append(f"Unresolved HTTP reference {reference['route']} in {reference['source']}")
    result["content_sha256"] = fingerprint.hexdigest()
    # Commit IDs alone do not identify uncommitted fixtures. Attach exact scan content.
    for collection in [result["nodes"], result["edges"], result["components"], result["attributes"], result["rules"], result["files"]]:
        for finding in collection:
            finding["evidence"]["content_sha256"] = fingerprint.hexdigest()
    result["id"] = fingerprint.hexdigest()[:16]
    result["languages"] = sorted({f["language"] for f in result["files"] if f["language"] != "configuration"})
    result["limitations"] = sorted(set(result["limitations"]))
    result["limitations"].append("Name classification uses identifier candidates, not approved metadata. Dynamic dependencies remain unresolved.")
    return result


def trace_flow(graph: dict[str, Any], source: str, target: str) -> dict[str, Any]:
    from collections import deque
    edges = graph["edges"]
    queue = deque([(source, [source])])
    seen = {source}
    while queue:
        current, path = queue.popleft()
        if current == target:
            return {"path": path, "status": "FOUND", "note": "Edge evidence determines certainty; candidate edges are not verified runtime flows"}
        for edge in edges:
            if edge["source"] == current and edge["target"] not in seen:
                seen.add(edge["target"])
                queue.append((edge["target"], path + [edge["target"]]))
    return {"path": [], "status": "UNRESOLVED"}
