"""Run inside a disposable production container, never modifying its image."""
import json
import os
from pathlib import Path
import subprocess
import sys

result = Path("/results/dependency-audit.json")
subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "--target", "/tmp/auditor", "pip-audit"])
env = {**os.environ, "PYTHONPATH":"/tmp/auditor"}
code = subprocess.run([sys.executable, "-m", "pip_audit", "--path", "/usr/local/lib/python3.13/site-packages",
    "--format", "json", "--output", str(result)], env=env).returncode
if result.exists():
    data = json.loads(result.read_text())
    vulnerable = [{"name":d["name"], "version":d.get("version"), "advisories":[
        {"id":v["id"], "fix_versions":v.get("fix_versions", [])} for v in d.get("vulns", [])]}
        for d in data["dependencies"] if d.get("vulns")]
    print(json.dumps({"exit_code":code, "vulnerable_dependencies":vulnerable,
        "limitations":"Python advisory scan only; not an OS/image or penetration test"}))
raise SystemExit(code)
