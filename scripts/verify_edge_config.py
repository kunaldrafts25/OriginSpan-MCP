"""Validate the shipped production TLS overlay using local rehearsal certificates."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
work = ROOT / ".runtime/readiness"
env = {**os.environ, "DIAMOND_PUBLIC_HOST":"localhost", "DIAMOND_TLS_DIR":str(work / "tls"),
    "DIAMOND_SOURCE_DIR":str(work / "sources"), "DIAMOND_RESOURCE_URL":"https://localhost:9443/mcp",
    "DIAMOND_OIDC_ISSUER":"https://localhost:9443/realms/diamond",
    "DIAMOND_OIDC_JWKS":"https://localhost:9443/realms/diamond/protocol/openid-connect/certs"}


def check(args):
    r = subprocess.run(args, env=env, capture_output=True)
    if r.returncode:
        (work / "edge-error.log").write_bytes(r.stderr)
        raise RuntimeError("TLS overlay validation failed; see ignored edge-error.log")


check(["docker", "compose", "--env-file", str(work / "deployment.env"),
    "-f", str(ROOT / "infrastructure/compose.production.yaml"),
    "-f", str(ROOT / "infrastructure/compose.edge.yaml"), "config", "--quiet"])
check(["docker", "run", "--rm", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
    "--tmpfs", "/tmp:size=33554432,mode=1777", "-e", "DIAMOND_PUBLIC_HOST=localhost",
    "-e", "NGINX_ENVSUBST_TEMPLATE_DIR=/templates", "-e", "NGINX_ENVSUBST_OUTPUT_DIR=/tmp",
    "-e", "NGINX_ENVSUBST_FILTER=^DIAMOND_PUBLIC_HOST$",
    "-v", str(ROOT / "infrastructure/nginx-production.conf.template") + ":/templates/nginx.conf.template:ro",
    "-v", str(work / "tls/cert.pem") + ":/tls/fullchain.pem:ro",
    "-v", str(work / "tls/key.pem") + ":/tls/privkey.pem:ro",
    "nginxinc/nginx-unprivileged:1.30.5-alpine", "nginx", "-t", "-c", "/tmp/nginx.conf"])
report = dict(status="PASSED", checks=["production plus edge Compose validation",
    "environment template substitution", "non-root/read-only proxy configuration and certificate loading"],
    limitation="uses local rehearsal certificate; customer certificate/DNS acceptance remains required")
(ROOT / "docs/validation-results/production/edge-config.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
