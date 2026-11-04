from diamond_mcp.chat_output import payload
import asyncio
import json
import secrets

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from diamond_mcp.auth import DemoTokenVerifier, actor_from_claims, configured_verifier
from diamond_mcp.engine import Engine, Config
from diamond_mcp.hosted import configure
from diamond_mcp.server import create_server
from diamond_mcp.web import create_web
from test_http_auth import launch, free_port


@pytest.mark.parametrize("token", ["", "short", "x"*31, "x"*257, "space "*8])
def test_demo_requires_explicit_strong_secret(token):
    with pytest.raises(ValueError):
        DemoTokenVerifier(token=token, audience="https://example.com/mcp")


def test_demo_startup_needs_no_oidc_but_is_explicit(tmp_path, monkeypatch):
    env = {"ORIGINSPAN_DATABASE_URL":"postgresql://u:p@host/db", "ORIGINSPAN_AUTH_MODE":"demo-token",
           "ORIGINSPAN_DEMO_TOKEN":secrets.token_urlsafe(32), "ORIGINSPAN_STATE":str(tmp_path),
           "RENDER_EXTERNAL_URL":"https://demo.onrender.com"}
    configure(env)
    assert env["ORIGINSPAN_RESOURCE_URL"] == "https://demo.onrender.com/mcp"
    env["ORIGINSPAN_AUTH_MODE"] = "oidc"
    with pytest.raises(ValueError):
        configure(env)
    monkeypatch.setenv("ORIGINSPAN_AUTH_MODE", "unknown")
    with pytest.raises(ValueError):
        configured_verifier()


def test_demo_token_never_grants_review_or_operate():
    secret = secrets.token_urlsafe(32)
    verifier = DemoTokenVerifier(secret, "https://example.com/mcp", "demo")
    assert asyncio.run(verifier.verify_token("wrong")) is None
    access = asyncio.run(verifier.verify_token(secret))
    actor = actor_from_claims(access.claims)
    actor.require("demo", "read")
    actor.require("demo", "analyze")
    for scope in ["review", "operate"]:
        with pytest.raises(PermissionError):
            actor.require("demo", scope)
    with pytest.raises(PermissionError):
        actor.require("another", "read")


def test_real_demo_http_mcp_and_viewer_are_protected(tmp_path):
    port = free_port()
    resource = f"http://127.0.0.1:{port}/mcp"
    secret = secrets.token_urlsafe(32)
    verifier = DemoTokenVerifier(secret, resource, "demo")
    engine = Engine(Config(tmp_path / "state", shared=True))
    app = create_web(engine, create_server(engine, verifier=verifier), verifier)
    server, thread = launch(app, port)
    base = f"http://127.0.0.1:{port}"
    async def scenario():
        async with httpx2.AsyncClient() as http:
            for headers in [{}, {"Authorization":"Bearer wrong"}]:
                result = await http.post(resource, headers=headers, json={})
                assert result.status_code == 401
                assert "resource_metadata" not in result.headers.get("www-authenticate", "")
                assert (await http.get(base + "/api/demo/applications", headers=headers)).status_code == 403
            assert (await http.get(base + "/.well-known/oauth-protected-resource/mcp")).status_code == 404
        async with httpx2.AsyncClient(headers={"Authorization":"Bearer " + secret}) as http:
            async with streamable_http_client(resource, http_client=http) as (read, write):
                async with ClientSession(read, write) as session:
                    info = await session.initialize()
                    assert info.server_info.name == "OriginSpan Discovery"
                    result = await session.call_tool("register_application", {"project_id":"demo", "name":"Demo"})
                    assert payload(result)["status"] == "OK"
                    result = await session.call_tool("list_applications", {"project_id":"other"})
                    assert payload(result)["status"] == "DENIED"
            assert (await http.get(base + "/api/demo/applications")).status_code == 200
            assert (await http.get(base + "/ops/metrics")).status_code == 403
            assert (await http.post(base + "/api/demo/review", params={"application":"none"}, json={"id":"none", "decision":"ACCEPTED"})).status_code == 403
    try:
        asyncio.run(scenario())
    finally:
        server.should_exit = True
        thread.join(5)
