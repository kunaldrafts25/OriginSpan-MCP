from diamond_mcp.chat_output import payload
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from starlette.testclient import TestClient

from diamond_mcp.engine import Engine, Config, Actor
from diamond_mcp.web import create_web


def test_standalone_stdio_tools_resources_prompts(tmp_path):
    source = tmp_path / "sources"
    source.mkdir()
    (source / "domain.py").write_text('def locate(C_ID): return C_ID\n')
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
           "DIAMOND_STATE": str(tmp_path / "state"), "DIAMOND_TRUSTED_ROOTS": str(source)}
    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=["-m", "diamond_mcp", "serve"], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                inventory = await session.list_tools()
                names = {t.name for t in inventory.tools}
                assert {"search_findings", "get_semantic_context", "validate_suggestions", "record_suggestions",
                        "get_complete_discovery", "get_discovery_section"} <= names
                assert not any("approv" in name or "migrat" in name for name in names)
                assert (await session.list_prompts()).prompts
                async def call(tool_name, **kwargs):
                    r = await session.call_tool(tool_name, {"project_id": "local", **kwargs})
                    return payload(r)
                result = await call("register_application", name="Fixture", local_path=str(source))
                app = result["data"]["id"]
                job = (await call("start_analysis", application_id=app))["data"]
                for _ in range(400):
                    job = (await call("get_analysis", application_id=app, analysis_id=job["id"]))["data"]
                    if job["status"] in {"COMPLETED", "FAILED"}:
                        break
                    await asyncio.sleep(.05)
                assert job["status"] == "COMPLETED", job
                result = await call("search_findings", application_id=app, analysis_id=job["id"], query="C_ID")
                assert result["data"]["findings"]
                dossier = await call("get_complete_discovery",application_id=app,analysis_id=job['id'])
                assert dossier['status']=='OK'
                assert dossier['data']['c360_handoff']['status']=='DRAFT_REQUIRES_REVIEW'
                section = await call('get_discovery_section',application_id=app,analysis_id=job['id'],section='fields')
                assert section['status']=='OK'
                foreign = await session.call_tool('get_complete_discovery',{'project_id':'foreign','application_id':app,'analysis_id':job['id']})
                assert payload(foreign)['status']=='DENIED'
                resource = await session.read_resource(f"diamond://local/{app}/{job['id']}/report")
                assert "observed_facts" in str(resource)
                current = await session.read_resource(f"originspan://local/{app}/{job['id']}/report")
                assert "observed_facts" in str(current)
                assert result["data"]["resource"].startswith("originspan://")
                denied = await call("register_application", name="escape", local_path=str(source.parent))
                assert denied["status"] == "ERROR"
    asyncio.run(scenario())


def test_token_protected_viewer_and_escaped_html(tmp_path):
    engine = Engine(Config(tmp_path / "state"))
    token = "a-random-test-token-at-least-24-characters"
    with TestClient(create_web(engine, local_token=token)) as client:
        assert client.get("/").status_code == 200
        assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]
        assert client.get("/api/local/applications").status_code == 403
        h = {"Authorization": "Bearer " + token}
        assert client.get("/api/local/applications", headers=h).status_code == 200
        assert client.get("/api/foreign/applications", headers=h).status_code == 403
        assert client.post("/api/local/applications", headers=h, json={"name": "<script>bad()</script>"}).status_code == 200
        # Rendered text is assigned with textContent, never interpolated as HTML.
        page = client.get("/").text
        assert "innerHTML" not in page
