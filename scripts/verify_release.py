"""Check built-wheel/source parity and accidental disclosure of rehearsal secrets."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
wheel = ROOT / "dist/originspan_mcp-0.3.0-py3-none-any.whl"
with zipfile.ZipFile(wheel) as archive:
    source = {p.relative_to(ROOT / "src").as_posix():p
        for package in ["diamond_mcp", "originspan"] for p in (ROOT / "src" / package).rglob("*.py")}
    assert set(source) == {n for n in archive.namelist() if n.startswith(("diamond_mcp/", "originspan/")) and n.endswith(".py")}
    for name, path in source.items():
        assert archive.read(name) == path.read_bytes(), "Wheel source mismatch: " + name
    assert archive.read("diamond_mcp/viewer/index.html") == (ROOT / "src/diamond_mcp/viewer/index.html").read_bytes()
    metadata = archive.read("originspan_mcp-0.3.0.dist-info/METADATA").decode()
    assert "PyJWT[crypto]==2.15.1" in metadata
secrets = []
for file in [ROOT / ".runtime/readiness/deployment.env", ROOT / ".runtime/render-validation/deployment.env",
             ROOT / ".runtime/production.env", ROOT / ".diamond/integration.env"]:
    if file.exists():
        secrets.extend(line.split("=", 1)[1].encode() for line in file.read_text().splitlines()
                       if "=" in line and len(line.split("=", 1)[1]) >= 24)
leaks = []
for folder in ["src", "scripts", "infrastructure", "docs", ".github", ".vscode"]:
    for path in (ROOT / folder).rglob("*"):
        if path.is_file() and path.suffix not in {".pyc", ".png"}:
            content = path.read_bytes()
            if any(secret in content for secret in secrets):
                leaks.append(str(path.relative_to(ROOT)))
assert not leaks, "Private fixture credentials found in release inputs: " + ", ".join(leaks)
report = dict(status="PASSED", wheel=wheel.name, sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
    source_modules=len(source), checks=["wheel/source parity", "viewer asset parity", "patched JWT metadata", "fixture secret exclusion"],
    limitation="fixture-specific secret scan; not a comprehensive sensitive-content detector")
(ROOT / "docs/validation-results/production/release-artifact.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
