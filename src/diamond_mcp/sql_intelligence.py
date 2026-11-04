"""Deterministic SQL AST inventory and conservative, scope-aware projection lineage."""
from __future__ import annotations

from typing import Any
import logging

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError, ErrorLevel
from sqlglot.lineage import lineage
from sqlglot.optimizer.qualify import qualify

# SQLGlot fallback warnings can contain full source statements. Diagnostics go
# through bounded result limitations instead of dumping source to stderr/logs.
logging.getLogger("sqlglot").setLevel(logging.ERROR)


def analyze_sql(sql: str, dialect: str = "postgres", schema: dict | None = None) -> dict[str, Any]:
    if len(sql.encode()) > 2_000_000:
        raise ValueError("SQL exceeds the 2 MB analysis limit")
    result: dict[str, Any] = {
        "dialect": dialect, "parser": "sqlglot", "status": "parsed",
        "tables": [], "columns": [], "outputs": [], "lineage": [],
        "conditions": [], "rules": [], "ctes": [], "schema_fields": [], "limitations": [],
        "predicate_dependencies": [], "value_lineage": [],
        "write_targets": [], "parameters": [], "statement_kinds": [],
        "metadata_statements": [],
    }
    try:
        statements = sqlglot.parse(sql, read=dialect, error_level=ErrorLevel.RAISE)
    except (SqlglotError, ValueError) as exc:
        return {**result, "status": "unsupported", "limitations": [str(exc)[:1500]]}
    tables: set[str] = set()
    columns: set[str] = set()
    for index, statement in enumerate(statements):
        if statement is None:
            continue
        result["statement_kinds"].append(statement.key.upper())
        result["parameters"].extend(sorted({p.sql(dialect=dialect) for p in statement.find_all(exp.Placeholder)}))
        if isinstance(statement, exp.Command):
            result["status"] = "partial"
            result["limitations"].append("An unsupported command was not semantically analyzed")
            continue
        result["metadata_statements"].append(statement.copy().transform(
            lambda n: exp.Placeholder() if isinstance(n,exp.Literal) and not isinstance(n.parent,exp.DataTypeParam) else n).sql(dialect=dialect))
        ctes = {cte.alias_or_name for cte in statement.find_all(exp.CTE)}
        result["ctes"].extend(sorted(ctes))
        # A CTE reference is not a physical table. Qualified tables remain physical.
        tables.update(".".join(p for p in [table.catalog, table.db, table.name] if p)
                      for table in statement.find_all(exp.Table)
                      if table.name not in ctes or table.db)
        for column in statement.find_all(exp.Column):
            columns.add(column.sql(dialect=dialect))
        for schema_node in statement.find_all(exp.Schema):
            if isinstance(schema_node.this, exp.Table):
                for definition in schema_node.expressions:
                    if isinstance(definition, exp.ColumnDef):
                        result["schema_fields"].append({"table": schema_node.this.name, "name": definition.name,
                            "data_type": definition.kind.sql(dialect=dialect) if definition.kind else None})
        for predicate_type in (exp.Where, exp.Having, exp.Join, exp.If):
            for condition in statement.find_all(predicate_type):
                expression = condition.args.get("on") if isinstance(condition, exp.Join) else condition.this
                if expression is None:
                    continue
                entry = {"kind": condition.key.upper(), "expression": expression.sql(dialect=dialect),
                         "original_expression": expression.sql(dialect=dialect), "expression_format": "AST_RENDERED",
                         "dependent_fields": sorted({c.sql(dialect=dialect) for c in expression.find_all(exp.Column)}),
                         "evidence_status": "AST_CONFIRMED", "interpretation_status": "review_required",
                         "approval_status": "pending", "statement": index}
                result["conditions"].append(entry)
                result["rules"].append(entry)
        query = statement if isinstance(statement, exp.Query) else statement.expression if isinstance(statement,(exp.Insert,exp.Create)) and isinstance(statement.expression,exp.Query) else None
        target_columns=[]
        if isinstance(statement,(exp.Insert,exp.Create,exp.Update,exp.Delete,exp.Merge)):
            target=statement.this.this if isinstance(statement.this,exp.Schema) else statement.this
            if isinstance(target,exp.Table):
                result["write_targets"].append(target.sql(dialect=dialect))
            if isinstance(statement.this,exp.Schema):
                target_columns=[c.name for c in statement.this.expressions]
        if isinstance(statement,exp.Merge):
            result["limitations"].append("MERGE writes identified; branch-specific value lineage requires further resolution")
        queries=[]
        if query is not None:
            queries.append((query,target_columns))
        elif isinstance(statement,exp.Update):
            for assignment in statement.expressions:
                if not isinstance(assignment,exp.EQ) or not isinstance(assignment.this,exp.Column):
                    continue
                projection=exp.select(assignment.expression.copy().as_(assignment.this.name)).from_(statement.this.copy())
                from_clause=statement.args.get("from_")
                if from_clause is not None:
                    projection=projection.join(from_clause.this.copy(),join_type="CROSS")
                if statement.args.get("where"):
                    projection.set("where",statement.args["where"].copy())
                queries.append((projection,[assignment.this.name]))
        for query,target_names in queries:
            if schema:
                try:
                    query=qualify(query.copy(),schema=schema,dialect=dialect,validate_qualify_columns=True,quote_identifiers=False,identify=False)
                except (SqlglotError,ValueError) as exc:
                    result["limitations"].append("Schema qualification incomplete: "+str(exc)[:300])
            for position,output in enumerate(query.named_selects):
                target_name=target_names[position] if position<len(target_names) else output
                result["outputs"].append(target_name)
                if output == "*":
                    result["limitations"].append("Wildcard requires approved schema metadata for exact expansion")
                    continue
                try:
                    node = lineage(output, query, schema=schema or {}, dialect=dialect)
                    leaves = sorted({
                        ".".join(p for p in [n.expression.catalog, n.expression.db, n.expression.name, sqlglot.parse_one(n.name,into=exp.Column,dialect=dialect).name] if p)
                        if isinstance(n.expression, exp.Table) else n.name
                        for n in node.walk() if not n.downstream
                    })
                    unresolved = any(isinstance(n.expression, exp.Placeholder) for n in node.walk()) or any(
                        not isinstance(n.expression, (exp.Table, exp.Literal))
                        for n in node.walk() if not n.downstream
                    )
                    result["lineage"].append({"output": target_name, "sources": leaves,
                                              "status": "UNRESOLVED" if unresolved else "AST_CONFIRMED",
                                              "statement": index, "transformation":node.expression.sql(dialect=dialect)[:2000]})
                    if unresolved:
                        result["limitations"].append(f"Projection {output} includes unresolved or aggregate row-dependency lineage")
                except (SqlglotError, ValueError, KeyError) as exc:
                    result["lineage"].append({"output": target_name, "sources": [], "status": "UNRESOLVED",
                                              "reason": str(exc)[:500], "statement": index})
    result["value_lineage"] = result["lineage"]
    result["predicate_dependencies"] = [{"kind": c["kind"], "expression": c["expression"],
        "fields": c["dependent_fields"], "statement": c["statement"]} for c in result["conditions"]
        if c["kind"] in {"WHERE", "HAVING", "JOIN"}]
    result["tables"] = sorted(tables)
    result["columns"] = sorted(columns)
    if any(c for c in result["columns"] if "." not in c):
        result["limitations"].append("Unqualified columns may require schema metadata; no physical source is guessed")
    result["limitations"].append("Static SQL only; runtime-generated SQL and stored-procedure semantics are not resolved")
    return result
