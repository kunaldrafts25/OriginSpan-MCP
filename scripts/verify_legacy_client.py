"""Exercise an installed older MCP SDK against the TLS rehearsal, no secrets logged."""
import asyncio
import importlib.metadata
import json
from pathlib import Path
import ssl

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).resolve().parents[1]


async def main():
    work = ROOT / ".runtime/readiness"
    env = dict(line.split("=", 1) for line in (work / "deployment.env").read_text().splitlines() if "=" in line)
    tls = ssl.create_default_context(cafile=str(work / "tls/ca.pem"))
    with httpx.Client(verify=tls, trust_env=False) as client:
        response = client.post("https://localhost:9443/realms/diamond/protocol/openid-connect/token",
            data={"grant_type":"client_credentials", "client_id":"diamond-integration", "client_secret":env["REHEARSAL_CLIENT_SECRET"]})
        response.raise_for_status()
        token = response.json()["access_token"]
    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth, verify=tls, trust_env=False)
    async with streamablehttp_client("https://localhost:9443/mcp", headers={"Authorization":"Bearer " + token},
            httpx_client_factory=factory) as (read, write, _):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            tools = await session.list_tools()
            assert any(t.name == "get_semantic_context" for t in tools.tools)
            result = await session.call_tool("list_applications", {"project_id":"integration"})
            assert (getattr(result,"structuredContent",None) or json.loads(result.content[0].text))["status"] == "OK"
    report = dict(status="PASSED", client="Python MCP SDK " + importlib.metadata.version("mcp"),
        protocol=info.protocolVersion, checks=["TLS/OIDC", "initialize", "list tools", "authorized tool call"],
        limitation="SDK compatibility; signed-in Copilot acceptance is a separate check")
    (ROOT / "docs/validation-results/production/legacy-client.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    asyncio.run(main())
