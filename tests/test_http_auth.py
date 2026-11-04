from diamond_mcp.chat_output import payload
"""Signed JWT auth and real HTTP MCP transport. No mock accepts unsigned tokens."""
import asyncio
import json
import os
import socket
import threading
import time

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
import httpx2
import uvicorn

from diamond_mcp.auth import OIDCVerifier
from diamond_mcp.engine import Engine, Config, Actor
from diamond_mcp.server import create_server
from diamond_mcp.web import create_web


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def launch(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            return server, thread
        if not thread.is_alive():
            raise RuntimeError("Server failed to start")
        time.sleep(.01)
    raise RuntimeError("Server startup timed out")


def test_oidc_signed_tokens_http_transport_and_project_isolation(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="integration", alg="RS256", use="sig")
    auth_port, port = free_port(), free_port()
    issuer, resource = f"http://127.0.0.1:{auth_port}", f"http://127.0.0.1:{port}/mcp"
    async def jwks(request):
        return JSONResponse({"keys": [jwk]})
    auth_server, auth_thread = launch(Starlette(routes=[Route("/jwks", jwks)]), auth_port)
    config = Config(tmp_path / "state", shared=True)
    engine = Engine(config)
    verifier = OIDCVerifier(issuer, resource, issuer + "/jwks")
    mcp = create_server(engine, verifier=verifier)
    server, thread = launch(create_web(engine, mcp, verifier), port)
    def token(**changes):
        claims = dict(sub="alice", iss=issuer, aud=resource, iat=int(time.time()), exp=int(time.time()) + 120,
                      scope="diamond:read diamond:analyze diamond:propose", diamond_projects=["alpha"])
        claims.update(changes)
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "integration"})
    async def scenario():
        async with httpx2.AsyncClient() as client:
            response = await client.post(resource, headers={"Accept": "application/json, text/event-stream"}, json={})
            assert response.status_code == 401
            for bad in [token(aud="wrong-resource"), token(exp=int(time.time())-10), token(iss="https://foreign.invalid")]:
                response = await client.post(resource, headers={"Authorization": "Bearer " + bad,
                    "Accept": "application/json, text/event-stream"}, json={})
                assert response.status_code == 401
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + token()}) as client:
            legacy_headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
            legacy = await client.post(resource, headers=legacy_headers, json={"jsonrpc":"2.0", "id":1,
                "method":"initialize", "params":{"protocolVersion":"2025-11-25", "capabilities":{},
                    "clientInfo":{"name":"diamond-legacy-check", "version":"1"}}})
            assert legacy.status_code == 200, legacy.text
            assert legacy.json()["result"]["protocolVersion"] == "2025-11-25"
            legacy_tools = await client.post(resource, headers=legacy_headers, json={"jsonrpc":"2.0", "id":2, "method":"tools/list", "params":{}})
            assert any(t["name"] == "search_findings" for t in legacy_tools.json()["result"]["tools"])
            async with streamable_http_client(resource, http_client=client) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.server_info.name == "OriginSpan Discovery"
                    tools = await session.list_tools()
                    assert any(t.name == "search_findings" for t in tools.tools)
                    result = await session.call_tool("register_application", {"project_id": "alpha", "name": "Private app"})
                    assert payload(result)["status"] == "OK"
                    result = await session.call_tool("list_applications", {"project_id": "beta"})
                    assert payload(result)["status"] == "DENIED"
        async with httpx2.AsyncClient(headers={"Authorization":"Bearer " + token(scope="originspan:read originspan:analyze", originspan_projects=["gamma"])}) as client:
            async with streamable_http_client(resource, http_client=client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("register_application", {"project_id":"gamma", "name":"OriginSpan application"})
                    assert payload(result)["status"] == "OK"
                    result = await session.call_tool("list_applications", {"project_id":"alpha"})
                    assert payload(result)["status"] == "DENIED"
    try:
        asyncio.run(scenario())
    finally:
        server.should_exit = auth_server.should_exit = True
        thread.join(5)
        auth_thread.join(5)
