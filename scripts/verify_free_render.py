"""Small free-tier-shaped demo: 512 MiB, ephemeral files, PostgreSQL, token auth.

Local Docker only; creates no Render resources. Never prints generated secrets.
"""
from diamond_mcp.chat_output import payload
import asyncio
import io
import json
from pathlib import Path
import secrets
import ssl
import zipfile

import httpx
import yaml
import deployment_rehearsal as fixture

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".runtime/free-render-validation"
BASE = "https://localhost:9446"
fixture.WORK, fixture.BASE = WORK, BASE
SQLITE_DEMO = False


def prepare():
    fixture.prepare()
    env = WORK / "deployment.env"
    if "ORIGINSPAN_DEMO_TOKEN=" not in env.read_text():
        with env.open("a") as stream:
            stream.write("ORIGINSPAN_DEMO_TOKEN=" + secrets.token_urlsafe(32) + "\n")
    config = yaml.safe_load((WORK / "compose.yaml").read_text())
    config["name"] = "originspan-free-demo-validation"
    services = config["services"]
    for name in ["worker", "migrate", "keycloak", "monitor"]:
        services.pop(name)
    api = services["api"]
    api.update(image="originspan-mcp:render", init=False, command=[], restart="no", mem_limit="512m", memswap_limit="512m")
    api["depends_on"] = {"database":{"condition":"service_healthy"}}
    api["cap_add"] = ["CHOWN", "SETUID", "SETGID"]
    api["volumes"] = [str(WORK / "tls/ca.pem").replace("\\", "/") + ":/tls/ca.pem:ro"]
    api["environment"] = {key:value for key,value in api["environment"].items() if "OIDC" not in key}
    api["environment"].update(ORIGINSPAN_AUTH_MODE="demo-token", ORIGINSPAN_DEMO_TOKEN="${ORIGINSPAN_DEMO_TOKEN}",
        ORIGINSPAN_RESOURCE_URL=BASE + "/mcp", ORIGINSPAN_PROJECT="demo", ORIGINSPAN_STATE="/tmp/originspan",
        ORIGINSPAN_MAX_ACTIVE_ANALYSES="1", ORIGINSPAN_NEURAL_ENABLED="false", ORIGINSPAN_MIN_FREE_BYTES="33554432", PORT="10000")
    if SQLITE_DEMO:
        services.pop("database")
        api["depends_on"] = {}
        api["environment"].update(ORIGINSPAN_STORAGE_MODE="sqlite-demo",
            ORIGINSPAN_DATABASE_URL="postgresql://invalid.invalid/not-used", ORIGINSPAN_PGVECTOR="false")
    api["healthcheck"]["test"][-1] = "import urllib.request; urllib.request.urlopen('http://127.0.0.1:10000/readyz',timeout=2)"
    services["edge"]["ports"] = ["127.0.0.1:9446:8443"]
    services["edge"]["depends_on"] = {"api":{"condition":"service_healthy"}}
    proxy = (WORK / "nginx.conf").read_text().replace("api:8765", "api:10000")
    (WORK / "nginx.conf").write_text(proxy)
    (WORK / "compose.yaml").write_text(yaml.safe_dump(config, sort_keys=False))


async def run():
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    fixture.compose("up", "-d")
    env = dict(line.split("=",1) for line in (WORK / "deployment.env").read_text().splitlines() if "=" in line)
    tls = ssl.create_default_context(cafile=str(WORK / "tls/ca.pem"))
    async with httpx2.AsyncClient(verify=tls, trust_env=False) as anonymous:
        assert (await anonymous.post(BASE + "/mcp", json={})).status_code == 401
    async with httpx2.AsyncClient(verify=tls, trust_env=False, headers={"Authorization":"Bearer " + env["ORIGINSPAN_DEMO_TOKEN"]}) as http:
        async with streamable_http_client(BASE + "/mcp", http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                async def call(tool, **kwargs):
                    r = await session.call_tool(tool, {"project_id":"demo", **kwargs})
                    result = payload(r)
                    assert result["status"] == "OK", result.get("code")
                    return result["data"]
                app = (await call("register_application", name="Free demo sample"))["id"]
                content = io.BytesIO()
                with zipfile.ZipFile(content,"w") as archive:
                    archive.writestr("schema.sql", "CREATE TABLE party (party_id INT, birthday DATE);")
                    archive.writestr("dictionary.md", "birthday means the date of birth of a person.")
                uploaded = await http.post(BASE + "/api/demo/upload", params={"application":app}, content=content.getvalue())
                uploaded.raise_for_status()
                job = (await call("start_analysis", application_id=app))["id"]
                for _ in range(120):
                    result = await call("get_analysis", application_id=app, analysis_id=job)
                    if result["status"] in {"COMPLETED", "FAILED", "PARTIAL"}:
                        break
                    await asyncio.sleep(.5)
                assert result["status"] == "COMPLETED", result["status"]
                found = await call("search_findings", application_id=app, analysis_id=job, query="birthday")
                assert found["findings"]
                denied = await session.call_tool("list_applications", {"project_id":"other"})
                assert payload(denied)["status"] == "DENIED"
        assert (await http.post(BASE + "/api/demo/review", params={"application":app}, json={"id":"none", "decision":"ACCEPTED"})).status_code == 403
        if SQLITE_DEMO:
            fixture.compose("restart", "api")
            for _ in range(60):
                try:
                    ready = await http.get(BASE + "/readyz")
                    if ready.status_code == 200 and ready.json()["ephemeral"]:
                        break
                except Exception:
                    pass
                await asyncio.sleep(1)
            apps = await http.get(BASE + "/api/demo/applications")
            apps.raise_for_status()
            assert apps.json() == [], "Ephemeral demo must start fresh after its tmpfs is reset"
    report = dict(status="PASSED", memory_limit="512 MiB, swap disabled", auth="explicit single-project demo token; no OIDC provider",
        storage="ephemeral SQLite; no PostgreSQL service" if SQLITE_DEMO else "PostgreSQL",
        checks=["startup and migration", "HTTPS MCP initialize", "anonymous denial", "source ZIP upload", "durable analysis completion",
                "evidence retrieval", "cross-project denial", "review denied"],
        limitations=["local container test, not live Render", "tiny synthetic metadata fixture only", "ephemeral source/blob files; no restart durability claim"])
    name = "render-free-sqlite-demo.json" if SQLITE_DEMO else "render-free-demo.json"
    (ROOT / "docs/validation-results/production" / name).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    import sys
    SQLITE_DEMO = "--sqlite" in sys.argv[2:]
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "prepare": prepare()
    elif action == "run": asyncio.run(run())
    elif action == "stop": fixture.compose("stop")
    else: raise SystemExit("Usage: verify_free_render.py prepare|run|stop")
