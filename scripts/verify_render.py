"""Exercise the Render image locally with real PostgreSQL, TLS and Keycloak.

This creates only diamond-render-validation containers. It does not call Render
or create paid resources. Reuses the local CA/identity fixture generator.
"""
from diamond_mcp.chat_output import payload
import asyncio
import io
import json
from pathlib import Path
import ssl
import time
import zipfile

import httpx
import yaml
import deployment_rehearsal as fixture

ROOT = Path(__file__).resolve().parents[1]
fixture.WORK = ROOT / ".runtime/render-validation"
fixture.BASE = "https://localhost:9445"
WORK, BASE = fixture.WORK, fixture.BASE
SEMANTIC = False


def prepare():
    fixture.prepare()
    config = yaml.safe_load((WORK / "compose.yaml").read_text())
    config["name"] = "diamond-render-validation"
    services = config["services"]
    services.pop("worker")
    services.pop("migrate")
    api = services["api"]
    api["image"] = "originspan-mcp:render-semantic" if SEMANTIC else "originspan-mcp:render"
    api["init"] = False  # Exercise the image's supervisor as PID 1, as deployed.
    api["command"] = []
    api["restart"] = "no"  # Observe supervisor failure rather than hiding it.
    api["depends_on"] = {"database":{"condition":"service_healthy"}}
    api["mem_limit"] = "4g" if SEMANTIC else "2g"
    # Bootstrap needs these three capabilities, then permanently drops uid/gid.
    api["cap_add"] = ["CHOWN", "SETUID", "SETGID"]
    api["environment"].update(PORT="10000", DIAMOND_STATE="/var/data/diamond", DIAMOND_NEURAL_ENABLED="true" if SEMANTIC else "false", DIAMOND_PGVECTOR="false")
    api["environment"]["DIAMOND_DATABASE_URL"] = api["environment"]["DIAMOND_DATABASE_URL"].replace("postgresql+psycopg:", "postgresql:")
    api["volumes"] = ["state:/var/data", str(WORK / "tls/ca.pem").replace("\\", "/") + ":/tls/ca.pem:ro"]
    api["healthcheck"]["test"][-1] = "import urllib.request; urllib.request.urlopen('http://127.0.0.1:10000/readyz',timeout=2)"
    services["edge"]["ports"] = ["127.0.0.1:9445:8443"]
    # Static identity config must advertise this fixture's external port.
    proxy = (WORK / "nginx.conf").read_text().replace("api:8765", "api:10000").replace("Port 9443", "Port 9445")
    (WORK / "nginx.conf").write_text(proxy)
    services["monitor"]["ports"] = ["127.0.0.1:9097:9090"]
    (WORK / "compose.yaml").write_text(yaml.safe_dump(config, sort_keys=False))


async def run():
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    fixture.compose("up", "-d")
    tls = ssl.create_default_context(cafile=str(WORK / "tls/ca.pem"))
    env = dict(line.split("=", 1) for line in (WORK / "deployment.env").read_text().splitlines() if "=" in line)
    with httpx.Client(verify=tls, trust_env=False, timeout=10) as client:
        for _ in range(120):
            try:
                r = client.get(BASE + "/realms/diamond/.well-known/openid-configuration")
                if r.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
        response = client.post(BASE + "/realms/diamond/protocol/openid-connect/token", data={
            "grant_type":"client_credentials", "client_id":"diamond-integration", "client_secret":env["REHEARSAL_CLIENT_SECRET"]})
        response.raise_for_status()
        token = response.json()["access_token"]
        async with httpx2.AsyncClient(verify=tls, trust_env=False, headers={"Authorization":"Bearer " + token}) as http:
            async with streamable_http_client(BASE + "/mcp", http_client=http) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.server_info.name == "OriginSpan Discovery"
                    async def call(tool_name, **kwargs):
                        r = await session.call_tool(tool_name, {"project_id":"integration", **kwargs})
                        result = payload(r)
                        assert result["status"] == "OK", result.get("code")
                        return result["data"]
                    app = (await call("register_application", name="Render container acceptance"))["id"]
                    content = io.BytesIO()
                    with zipfile.ZipFile(content, "w") as archive:
                        archive.writestr("dictionary.md", "Z91 denotes the day on which a person was born.")
                        archive.writestr("schema.sql", "CREATE TABLE party (party_key INT, z91 DATE);")
                    uploaded = await http.post(BASE + "/api/integration/upload", params={"application":app}, content=content.getvalue())
                    uploaded.raise_for_status()
                    job = (await call("start_analysis", application_id=app))["id"]
                    for _ in range(240):
                        result = await call("get_analysis", application_id=app, analysis_id=job)
                        if result["status"] in {"COMPLETED", "PARTIAL", "FAILED"}:
                            break
                        await asyncio.sleep(1)
                    assert result["status"] == "COMPLETED", result["status"]
                    for _ in range(120):
                        found = await call("search_findings", application_id=app, analysis_id=job,
                            query="date of birth definition" if SEMANTIC else "born", mode="semantic" if SEMANTIC else "lexical")
                        if found["findings"]:
                            break
                        await asyncio.sleep(1)
                    assert any(f["file"] == "dictionary.md" for f in found["findings"])
                    if SEMANTIC:
                        assert any(p["channel"] == "neural" for f in found["findings"] for p in f["retrieval"])
                    else:
                        unavailable = await call("search_findings", application_id=app, analysis_id=job, query="birth date", mode="semantic")
                        assert not unavailable["neural"]["available"] and not unavailable["findings"]
                        assert any("unavailable" in item.lower() for item in unavailable["limitations"])
                    assert (await http.get(BASE + "/api/other/applications")).status_code == 403
        cid = fixture.compose("ps", "-q", "api").decode().strip()
        if not SEMANTIC:
            fixture.command("docker", "exec", cid, "python", "-c",
                "import importlib.util; assert importlib.util.find_spec('torch') is None; "
                "assert importlib.util.find_spec('sentence_transformers') is None")
        fixture.command("docker", "exec", cid, "python", "-c",
            "from pathlib import Path; ids=['1']+Path('/proc/1/task/1/children').read_text().split(); "
            "statuses=[dict(line.split(':',1) for line in Path('/proc',p,'status').read_text().splitlines() if ':' in line) for p in ids]; "
            "assert all(s['Uid'].split()==['10001']*4 and int(s['CapEff'].strip(),16)==0 for s in statuses)")
        fixture.compose("restart", "api")
        for _ in range(90):
            try:
                if client.get(BASE + "/readyz").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
        persisted = client.get(BASE + "/api/integration/search", params={"application":app, "analysis":job,"q":"born"},
                               headers={"Authorization":"Bearer " + token})
        persisted.raise_for_status()
        assert persisted.json()["findings"]
        # Kill only this container's worker; its supervisor must fail the whole
        # service, preventing a healthy API from hiding a dead queue consumer.
        fixture.command("docker", "exec", "--user", "10001", cid, "python", "-c",
            "from pathlib import Path; import os,signal; children=Path('/proc/1/task/1/children').read_text().split(); "
            "worker=next(int(p) for p in children if b'worker' in Path('/proc',p,'cmdline').read_bytes()); os.kill(worker,signal.SIGKILL)")
        for _ in range(45):
            state = fixture.command("docker", "inspect", "--format", "{{.State.Status}}", cid).decode().strip()
            if state == "exited":
                break
            await asyncio.sleep(1)
        assert state == "exited"
        exit_code = fixture.command("docker", "inspect", "--format", "{{.State.ExitCode}}", cid).decode().strip()
        assert exit_code == "1"
        report = dict(status="PASSED", image="originspan-mcp:render-semantic" if SEMANTIC else "originspan-mcp:render",
            memory_limit="4 GiB" if SEMANTIC else "2 GiB", architecture="single service, API plus one durable worker",
            checks=["Render-style postgres URL normalization", "runtime migration", "PORT=10000", "TLS/OIDC and project isolation",
                    "real pinned local neural indexing and semantic retrieval" if SEMANTIC else "model-free retrieval and explicit neural-unavailable status", "non-root supervisor and children",
                    "disk/database persistence across restart", "worker crash fails the container"],
            limitations=["local container rehearsal, not a live Render deployment", "Render Blueprint API validation not run",
                         "small fixture under configured memory limit; no broad workload capacity claim"])
        name = "originspan-render-semantic-container.json" if SEMANTIC else "originspan-render-container.json"
        (ROOT / "docs/validation-results/production" / name).write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    import sys
    SEMANTIC = "--semantic" in sys.argv[2:]
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "prepare":
        prepare()
    elif action == "run":
        asyncio.run(run())
    elif action == "stop":
        fixture.compose("stop")
    else:
        raise SystemExit("Usage: verify_render.py prepare|run|stop [--semantic]")
