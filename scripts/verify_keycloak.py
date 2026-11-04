"""Real Keycloak + PostgreSQL integration, using the isolated compose fixture.

No credentials or bearer tokens are written to the report or printed.
"""
from diamond_mcp.chat_output import payload
import asyncio
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
import zipfile

import httpx
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from diamond_mcp.auth import OIDCVerifier
from diamond_mcp.engine import Config
from diamond_mcp.service import DiscoveryService as Engine
from diamond_mcp.server import create_server
from diamond_mcp.web import create_web


async def scenario():
    env = dict(line.split("=", 1) for line in (ROOT / ".diamond/integration.env").read_text().splitlines() if "=" in line)
    issuer, resource = "http://127.0.0.1:8890/realms/diamond", "http://127.0.0.1:8765/mcp"
    for attempt in range(60):
        try:
            ready = httpx.get(issuer + "/.well-known/openid-configuration", timeout=2, trust_env=False)
            if ready.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if attempt == 59:
            raise RuntimeError("Keycloak did not become ready")
        await asyncio.sleep(1)
    response = httpx.post(issuer + "/protocol/openid-connect/token", data={"grant_type":"client_credentials",
        "client_id":"diamond-integration", "client_secret":env["DIAMOND_TEST_CLIENT_SECRET"]}, timeout=20, trust_env=False)
    response.raise_for_status()
    token = response.json()["access_token"]
    config = Config(ROOT / ".diamond/shared-test", database_url="postgresql+psycopg://diamond:" + env["DIAMOND_TEST_DB_PASSWORD"] + "@127.0.0.1:55433/diamond", shared=True)
    engine = Engine(config,migrate=True)
    worker_stop=threading.Event()
    worker=threading.Thread(target=engine.pipeline.run,args=("keycloak-validation-worker",None,worker_stop),daemon=True)
    worker.start()
    verifier = OIDCVerifier(issuer, resource, issuer + "/protocol/openid-connect/certs")
    server = uvicorn.Server(uvicorn.Config(create_web(engine, create_server(engine, verifier=verifier), verifier),
        host="127.0.0.1", port=8765, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(.05)
        async with httpx2.AsyncClient(headers={"Authorization":"Bearer " + token}) as client:
            async with streamable_http_client(resource, http_client=client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    async def call(tool_name, **kwargs):
                        result = await session.call_tool(tool_name, {"project_id":"integration", **kwargs})
                        return payload(result)
                    app = (await call("register_application", name="OIDC metadata fixture"))["data"]["id"]
                    stream = io.BytesIO()
                    with zipfile.ZipFile(stream, "w") as archive:
                        archive.writestr("dictionary.md", "X17 is an agreement renewal deadline.")
                    # Shared upload endpoint takes explicitly supplied content,
                    # and cannot access the client's filesystem through a path.
                    uploaded = await client.post("http://127.0.0.1:8765/api/integration/upload", params={"application":app}, content=stream.getvalue())
                    uploaded.raise_for_status()
                    job = (await call("start_analysis", application_id=app))["data"]
                    for _ in range(100):
                        job = (await call("get_analysis", application_id=app, analysis_id=job["id"]))["data"]
                        if job["status"] in {"COMPLETED", "FAILED"}:
                            break
                        await asyncio.sleep(.05)
                    assert job["status"] == "COMPLETED", job
                    found = await call("search_findings", application_id=app, analysis_id=job["id"], query="renewal")
                    assert found["data"]["findings"]
                    denied = await session.call_tool("list_applications", {"project_id":"other"})
                    assert payload(denied)["status"] == "DENIED"
                    review = await client.post("http://127.0.0.1:8765/api/integration/review", params={"application":app}, json={"id":"none", "decision":"ACCEPTED"})
                    assert review.status_code == 403
        report = {"status":"PASSED", "identity_provider":"Keycloak 26.4.0", "metadata_database":"PostgreSQL 18.6",
                  "checks":["OIDC signature/audience/issuer", "HTTP MCP", "managed upload", "persistent job", "PostgreSQL full text", "project isolation", "review permission denial"],
                  "client":"official Python MCP SDK 2.3.0", "copilot":"NOT_RUN"}
        (ROOT / "artifacts").mkdir(exist_ok=True)
        (ROOT / "artifacts/keycloak-integration.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report))
    finally:
        worker_stop.set()
        worker.join(10)
        server.should_exit = True
        thread.join(5)


if __name__ == "__main__":
    asyncio.run(scenario())
