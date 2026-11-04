"""Validate render.yaml against Render's current official public JSON schema."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import httpx
import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
url = "https://render.com/schema/render.yaml.json"
response = httpx.get(url, timeout=30, follow_redirects=True)
response.raise_for_status()
schema = response.json()
validator = jsonschema.validators.validator_for(schema)
validator.check_schema(schema)
errors = sorted(validator(schema).iter_errors(yaml.safe_load((ROOT / "render.yaml").read_text())), key=lambda e:str(e.path))
report = dict(status="FAILED" if errors else "PASSED", schema=url,
    schema_sha256=hashlib.sha256(response.content).hexdigest(), checked_at=datetime.now(timezone.utc).isoformat(),
    errors=[{"path":"/".join(str(p) for p in e.path), "message":e.message} for e in errors],
    limitation="Official JSON schema validation; live Render account/API validation is separate")
destination = ROOT / "docs/validation-results/production/render-schema.json"
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
raise SystemExit(bool(errors))
