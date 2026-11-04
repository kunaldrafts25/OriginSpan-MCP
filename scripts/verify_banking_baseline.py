"""Verify exact route and DDL ground truth from a completed public evaluation."""
from pathlib import Path
import json
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
latest = max((ROOT / ".diamond/eval").iterdir(), key=lambda p: p.stat().st_mtime)
connection = sqlite3.connect(latest / "state.sqlite")
rows = connection.execute("SELECT payload FROM discovery_objects WHERE kind=?", ("structural",)).fetchall()
structures = [json.loads(row[0]) for row in rows]
structure = next(s for s in structures if any(f["path"].startswith("src/accounts/") for f in s["files"]))
ground = json.loads((ROOT / "evaluations/banking-ground-truth.json").read_text())
routes = {n["evidence"]["file"] + " :: " + n["name"] for n in structure["nodes"] if n["type"] == "API_ENDPOINT"}
expected_routes = {file + " :: " + route for file, rs in ground["declared_routes"].items() for route in rs}
fields = {(f["table"].casefold(), f["name"].casefold()) for q in structure["sql"] for f in q["schema_fields"]}
expected_fields = {(table.casefold(), field.casefold()) for table, names in ground["ddl_fields"].items() for field in names}
tables = {t.casefold() for q in structure["sql"] for t in q["tables"]}
result = dict(endpoints=len(routes), tables=len(tables), columns=len(fields),
    missing_routes=sorted(expected_routes-routes), unexpected_routes=sorted(routes-expected_routes),
    missing_columns=sorted(expected_fields-fields), unexpected_columns=sorted(fields-expected_fields),
    tables_match=tables == set(ground["physical_tables"]))
result["passed"] = not any(result[k] for k in ["missing_routes", "unexpected_routes", "missing_columns", "unexpected_columns"]) and result["tables_match"]
(ROOT / "artifacts/banking-baseline.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
raise SystemExit(0 if result["passed"] else 1)
