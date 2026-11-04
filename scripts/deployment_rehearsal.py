"""Isolated local TLS/OIDC/container/restore rehearsal. Never prints secrets.

Uses the production Compose definition with generated local-only identity/certs.
Run --prepare, build the production image, then run --run. Retains volumes and
ignored credentials for inspection. --stop stops only this rehearsal project.
"""
from __future__ import annotations
from diamond_mcp.chat_output import payload

import asyncio
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import time
import zipfile

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".runtime/readiness"
BASE = "https://localhost:9443"


def command(*args, input=None):
    result = subprocess.run(args, input=input, capture_output=True)
    if result.returncode:
        # Docker may echo interpolated settings. Keep diagnostics private.
        (WORK / "last-error.log").write_bytes(result.stderr)
        raise RuntimeError("Deployment command failed; inspect ignored .runtime/readiness/last-error.log")
    return result.stdout


def compose(*args):
    return command("docker", "compose", "--env-file", str(WORK / "deployment.env"),
                   "-f", str(WORK / "compose.yaml"), *args)


def prepare():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    WORK.mkdir(parents=True, exist_ok=True)
    secret_file = WORK / "deployment.env"
    if not secret_file.exists():
        secret_file.write_text("\n".join(f"{key}={secrets.token_urlsafe(32)}" for key in [
            "DIAMOND_DB_PASSWORD", "DIAMOND_DB_ADMIN_PASSWORD", "REHEARSAL_CLIENT_SECRET",
            "REHEARSAL_OPERATOR_SECRET", "REHEARSAL_ADMIN_PASSWORD"]) + "\n")
    tls = WORK / "tls"
    tls.mkdir(exist_ok=True)
    needs_certificate = not (tls / "cert.pem").exists()
    if not needs_certificate:
        try:
            x509.load_pem_x509_certificate((tls / "cert.pem").read_bytes()).extensions.get_extension_for_class(x509.AuthorityKeyIdentifier)
        except x509.ExtensionNotFound:
            needs_certificate = True
    if needs_certificate:
        now = datetime.now(timezone.utc)
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "DIAMOND local rehearsal CA")])
        ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
              .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=30))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
              .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
              .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
              .sign(ca_key, hashes.SHA256()))
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        cert = (x509.CertificateBuilder().subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])).issuer_name(ca_name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=14))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.DNSName("edge")]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
        (tls / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        (tls / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (tls / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        # The CA signing key is not retained. Certificates are local test assets.
    source = WORK / "sources"
    source.mkdir(exist_ok=True)
    realm = json.loads((ROOT / "infrastructure/keycloak-realm.json").read_text())
    client = realm["clients"][0]
    client["secret"] = "${REHEARSAL_CLIENT_SECRET}"
    client["protocolMappers"][0]["config"]["included.custom.audience"] = BASE + "/mcp"
    operator = json.loads(json.dumps(client))
    operator.update(clientId="diamond-monitor", secret="${REHEARSAL_OPERATOR_SECRET}",
                    defaultClientScopes=["diamond:read", "diamond:operate"])
    realm["clients"].append(operator)
    realm["clientScopes"].append({"name":"diamond:operate", "protocol":"openid-connect",
                                 "attributes":{"include.in.token.scope":"true"}})
    (WORK / "realm.json").write_text(json.dumps(realm))
    (WORK / "nginx.conf").write_text('''pid /tmp/nginx.pid;
events {}
http {
  resolver 127.0.0.11 valid=5s ipv6=off;
  client_body_temp_path /tmp/client_temp;
  proxy_temp_path /tmp/proxy_temp;
  access_log off;
  server {
    listen 8443 ssl;
    server_name localhost edge;
    ssl_certificate /tls/cert.pem;
    ssl_certificate_key /tls/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 50m;
    location /realms/ {
      set $identity_backend keycloak:8080;
      proxy_pass http://$identity_backend;
      proxy_set_header Host $http_host;
      proxy_set_header X-Forwarded-Proto https;
      proxy_set_header X-Forwarded-Port 9443;
    }
    location / {
      set $discovery_backend api:8765;
      proxy_pass http://$discovery_backend;
      proxy_set_header Host $http_host;
      proxy_set_header Authorization $http_authorization;
      proxy_set_header X-Forwarded-Proto https;
      proxy_buffering off;
      proxy_read_timeout 300s;
      add_header Strict-Transport-Security "max-age=86400" always;
    }
  }
}
''')
    (WORK / "monitor.token").touch(exist_ok=True)
    (WORK / "prometheus.yaml").write_text('''global:
  scrape_interval: 2s
  evaluation_interval: 2s
rule_files: [/config/alerts.yaml]
scrape_configs:
  - job_name: diamond
    scheme: https
    metrics_path: /ops/metrics
    authorization:
      credentials_file: /config/monitor.token
    tls_config:
      ca_file: /tls/ca.pem
      server_name: edge
    static_configs:
      - targets: ['edge:8443']
''')
    (WORK / "alerts.yaml").write_text('''groups:
  - name: diamond
    rules:
      - alert: DiamondUnavailable
        expr: up{job="diamond"} == 0 or diamond_catalog_available == 0
        for: 2s
        labels: {severity: critical}
        annotations: {summary: "DIAMOND API or catalog is unavailable"}
''')
    config = yaml.safe_load((ROOT / "infrastructure/compose.production.yaml").read_text())
    config.pop("x-runtime", None)
    config["name"] = "diamond-readiness"
    services = config["services"]
    services["database"]["volumes"][1] = str(ROOT / "infrastructure/bootstrap-production.sh").replace("\\", "/") + ":/docker-entrypoint-initdb.d/01-app.sh:ro"
    services["api"]["ports"] = []
    for name in ["api", "worker", "migrate"]:
        item = services[name]
        item["environment"] = dict(item["environment"],
            DIAMOND_OIDC_ISSUER=BASE + "/realms/diamond",
            DIAMOND_OIDC_JWKS="https://edge:8443/realms/diamond/protocol/openid-connect/certs",
            DIAMOND_RESOURCE_URL=BASE + "/mcp", SSL_CERT_FILE="/tls/ca.pem",
            DIAMOND_NEURAL_ENABLED="false")
        item["volumes"] = ["state:/state", f"{source.as_posix()}:/sources:ro", f"{tls.as_posix()}/ca.pem:/tls/ca.pem:ro"]
    services["keycloak"] = dict(image="quay.io/keycloak/keycloak:26.4.0",
        command=["start-dev", "--import-realm"],
        environment={"KC_BOOTSTRAP_ADMIN_USERNAME":"admin", "KC_BOOTSTRAP_ADMIN_PASSWORD":"${REHEARSAL_ADMIN_PASSWORD}",
            "REHEARSAL_CLIENT_SECRET":"${REHEARSAL_CLIENT_SECRET}", "REHEARSAL_OPERATOR_SECRET":"${REHEARSAL_OPERATOR_SECRET}",
            "KC_HOSTNAME":BASE, "KC_PROXY_HEADERS":"xforwarded"},
        volumes=[f"{WORK.as_posix()}/realm.json:/opt/keycloak/data/import/diamond-realm.json:ro"])
    services["edge"] = dict(image="nginxinc/nginx-unprivileged:1.30.5-alpine",
        ports=["127.0.0.1:9443:8443"],
        volumes=[f"{WORK.as_posix()}/nginx.conf:/etc/nginx/nginx.conf:ro", f"{tls.as_posix()}:/tls:ro"],
        depends_on={"api":{"condition":"service_healthy"}, "keycloak":{"condition":"service_started"}})
    services["monitor"] = dict(image="prom/prometheus:v3.15.0",
        command=["--config.file=/config/prometheus.yaml", "--storage.tsdb.retention.time=1d"],
        ports=["127.0.0.1:9096:9090"], volumes=[f"{WORK.as_posix()}/{name}:/config/{name}:ro" for name in ["prometheus.yaml", "alerts.yaml", "monitor.token"]]
            + [f"{tls.as_posix()}/ca.pem:/tls/ca.pem:ro"],
        depends_on=["edge"])
    (WORK / "compose.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    print("Prepared isolated rehearsal; secrets and local certificates remain in ignored .runtime/readiness")


async def run():
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    checks = []
    def check(message):
        checks.extend([message])
        print(message, flush=True)
    attempt = secrets.token_hex(4)
    restore_database = "diamond_restore_" + attempt
    env = dict(line.split("=", 1) for line in (WORK / "deployment.env").read_text().splitlines() if "=" in line)
    tls = ssl.create_default_context(cafile=str(WORK / "tls/ca.pem"))
    compose("up", "-d", "--scale", "worker=2")
    worker_ids = compose("ps", "-q", "worker").decode().split()
    restarts = {cid:command("docker", "inspect", "--format", "{{.RestartCount}}", cid) for cid in worker_ids}
    with httpx.Client(verify=tls, trust_env=False, timeout=8) as client:
        for _ in range(120):
            try:
                response = client.get(BASE + "/realms/diamond/.well-known/openid-configuration")
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
        else:
            raise RuntimeError("TLS/identity readiness timed out")
        check("TLS certificate and hostname validation")
        def token(client_id, secret):
            r = client.post(BASE + "/realms/diamond/protocol/openid-connect/token",
                data={"grant_type":"client_credentials", "client_id":client_id, "client_secret":secret})
            r.raise_for_status()
            return r.json()["access_token"]
        access = token("diamond-integration", env["REHEARSAL_CLIENT_SECRET"])
        operator = token("diamond-monitor", env["REHEARSAL_OPERATOR_SECRET"])
        (WORK / "monitor.token").write_text(operator)
        for _ in range(45):
            if client.get(BASE + "/readyz").status_code == 200:
                break
            await asyncio.sleep(1)
        else:
            raise RuntimeError("API not ready through TLS edge")
        assert client.get(BASE + "/ops/metrics").status_code == 403
        assert client.get(BASE + "/ops/metrics", headers={"Authorization":"Bearer " + access}).status_code == 403
        assert client.get(BASE + "/ops/metrics", headers={"Authorization":"Bearer " + operator}).status_code == 200
        check("separate monitoring scope; anonymous and reader denial")
        async with httpx2.AsyncClient(verify=tls, trust_env=False, headers={"Authorization":"Bearer " + access}) as http:
            async with streamable_http_client(BASE + "/mcp", http_client=http) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    async def call(tool_name, **kwargs):
                        r = await session.call_tool(tool_name, {"project_id":"integration", **kwargs})
                        data = payload(r)
                        assert data["status"] == "OK", data.get("code")
                        return data["data"]
                    app = (await call("register_application", name="TLS deployment rehearsal"))["id"]
                    content = io.BytesIO()
                    with zipfile.ZipFile(content, "w") as archive:
                        archive.writestr("schema.sql", "CREATE TABLE party (party_key INT PRIMARY KEY, birth_date DATE);")
                        archive.writestr("dictionary.md", "Z91 means the date a person was born. This is schema metadata only.")
                    upload = await http.post(BASE + "/api/integration/upload", params={"application":app}, content=content.getvalue())
                    upload.raise_for_status()
                    job = (await call("start_analysis", application_id=app))["id"]
                    for _ in range(180):
                        result = await call("get_analysis", application_id=app, analysis_id=job)
                        if result["status"] in {"COMPLETED", "PARTIAL", "FAILED"}:
                            break
                        await asyncio.sleep(.5)
                    assert result["status"] == "COMPLETED"
                    found = await call("search_findings", application_id=app, analysis_id=job, query="born", mode="lexical")
                    assert found["findings"]
                    denied = await session.call_tool("list_applications", {"project_id":"other"})
                    assert payload(denied)["status"] == "DENIED"
                    assert (await http.post(BASE + "/api/integration/review", params={"application":app},
                        json={"id":"none", "decision":"ACCEPTED"})).status_code == 403
                    check("official MCP SDK over TLS; Keycloak; production app role; independent workers; project isolation")
                    compose("restart", "api")
        for _ in range(45):
            try:
                if client.get(BASE + "/readyz").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
        query = {"application":app, "analysis":job, "q":"born"}
        headers = {"Authorization":"Bearer " + access}
        persisted = client.get(BASE + "/api/integration/search", params=query, headers=headers)
        persisted.raise_for_status()
        assert persisted.json()["findings"]
        check("API restart preserves published evidence")
        # Scrape/alert test: force an actual catalog outage; restore it in finally.
        compose("stop", "database")
        try:
            assert client.get(BASE + "/readyz").status_code == 503
            with httpx.Client(trust_env=False) as monitor:
                for _ in range(25):
                    response = monitor.get("http://127.0.0.1:9096/api/v1/alerts").json()
                    if any(a["state"] == "firing" for a in response["data"]["alerts"]):
                        check("Prometheus outage alert fired during catalog failure")
                        break
                    await asyncio.sleep(1)
                else:
                    raise RuntimeError("Monitoring did not detect catalog outage")
        finally:
            compose("start", "database")
        for _ in range(60):
            if client.get(BASE + "/readyz").status_code == 200:
                break
            await asyncio.sleep(1)
        else:
            raise RuntimeError("Catalog did not recover")
        resumed = client.post(BASE + "/api/integration/analyze", params={"application":app}, headers=headers, json={})
        resumed.raise_for_status()
        resumed_id = resumed.json()["id"]
        for _ in range(120):
            analyses = client.get(BASE + "/api/integration/analyses", params={"application":app}, headers=headers)
            analyses.raise_for_status()
            if any(a["id"] == resumed_id and a["status"] == "COMPLETED" for a in analyses.json()):
                break
            await asyncio.sleep(1)
        else:
            raise RuntimeError("Workers did not consume new work after catalog recovery")
        assert all(command("docker", "inspect", "--format", "{{.RestartCount}}", cid) == restarts[cid] for cid in worker_ids)
        check("same worker containers consumed new analysis after catalog recovery without restarting")
        # Pause all writers for a consistent database+blob checkpoint.
        compose("stop", "api", "worker")
        try:
            compose("exec", "-T", "database", "pg_dump", "-U", "postgres", "-d", "diamond", "-Fc", "-f", "/tmp/readiness.dump")
            compose("exec", "-T", "database", "createdb", "-U", "postgres", "-O", "diamond_app", restore_database)
            # Restore as owner/admin and preserve the dumped application role
            # ownership. GRANT alone does not permit required index operations.
            compose("exec", "-T", "database", "pg_restore", "-U", "postgres", "--exit-on-error", "-d", restore_database, "/tmp/readiness.dump")
            db = compose("ps", "-q", "database").decode().strip()
            command("docker", "cp", db + ":/tmp/readiness.dump", str(WORK / ("catalog-" + attempt + ".dump")))
            # Copy the entire state volume, not only catalog rows.
            compose("run", "--rm", "--no-deps", "--user", "0", "--entrypoint", "python", "-v", str(WORK) + ":/backup", "worker", "-c",
                "import tarfile; t=tarfile.open('/backup/state.tar.gz','w:gz'); t.add('/state',arcname='state'); t.close()")
            restore = WORK / ("restored-" + attempt)
            restore.mkdir(exist_ok=True)
            compose("run", "--rm", "--no-deps", "--user", "0", "--entrypoint", "python", "-v", str(WORK) + ":/backup", "worker", "-c",
                f"import tarfile; t=tarfile.open('/backup/state.tar.gz'); t.extractall('/backup/{restore.name}',filter='data'); t.close()")
            code = ("from pathlib import Path; from diamond_mcp.engine import Config,Actor; "
                "from diamond_mcp.service import DiscoveryService; import os; "
                f"url=os.environ['DIAMOND_DATABASE_URL'].rsplit('/',1)[0]+'/{restore_database}'; "
                "e=DiscoveryService(Config(Path('/state'), database_url=url,shared=True)); "
                f"r=e.search(Actor('restore-check',frozenset({{'integration'}})),'integration',{app!r},{job!r},'born','lexical'); "
                "assert r['findings']; "
                "import hashlib; blobs=list(Path('/state/blobs').glob('*/*/*')); assert blobs; "
                "assert all(hashlib.sha256(p.read_bytes()).hexdigest()==p.name for p in blobs); "
                "print('Restored catalog retrieval and blob checksums passed')")
            compose("run", "--rm", "--no-deps", "--entrypoint", "python", "-v", str(restore / "state") + ":/state", "worker", "-I", "-c", code)
            check("quiesced pg_dump plus state backup restored into separate database/path; retrieval and blob hashes verified")
        finally:
            compose("start", "api", "worker")
        report = dict(status="PASSED", executed_at=datetime.now(timezone.utc).isoformat(), checks=checks,
            identity="local test Keycloak; generated trusted local CA", transport="HTTPS Streamable HTTP",
            image="originspan-mcp:0.3.0", workers=2, model="disabled in base container; real neural profile validated separately",
            limitations=["customer IdP/DNS/certificates not configured", "signed-in Copilot acceptance not run", "1 TB workload not run",
                         "Prometheus alert evaluated; customer notification routing not configured"])
        output = ROOT / "docs/validation-results/production/deployment-rehearsal.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    # Accept simple verbs and their -- aliases.
    action = os.sys.argv[1] if len(os.sys.argv) == 2 else ""
    if action in {"prepare", "--prepare"}:
        prepare()
    elif action in {"run", "--run"}:
        asyncio.run(run())
    elif action in {"stop", "--stop"}:
        compose("stop")
    else:
        raise SystemExit("Usage: deployment_rehearsal.py prepare|run|stop")
