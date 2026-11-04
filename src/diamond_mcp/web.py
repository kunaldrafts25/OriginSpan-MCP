"""Optional viewer and upload endpoints, using the same authorization checks as MCP."""
from __future__ import annotations

from .compat import getenv
import hmac
import json
import os
from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route
from starlette.concurrency import run_in_threadpool

from .auth import actor_from_claims
from .engine import Actor
from .sql_intelligence import analyze_sql


def create_web(engine, mcp=None, verifier=None, local_token=None, local_project=None):
    from starlette.applications import Starlette
    shared = engine.config.shared
    if not shared and (not local_token or len(local_token) < 24):
        raise ValueError("Set DIAMOND_VIEWER_TOKEN to a random token of at least 24 characters")

    async def identity(request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise PermissionError("Bearer token required")
        token = header[7:]
        if shared:
            access = await verifier.verify_token(token)
            if not access:
                raise PermissionError("Invalid identity token")
            return actor_from_claims(access.claims)
        if not hmac.compare_digest(token, local_token):
            raise PermissionError("Invalid viewer token")
        return Actor("local-human-reviewer", frozenset({local_project or getenv("DIAMOND_PROJECT", "local")}),
                     frozenset({"read", "analyze", "propose", "review"}))

    async def api(request: Request):
        try:
            actor = await identity(request)
            project = request.path_params["project"]
            action = request.path_params.get("action", "applications")
            app = request.query_params.get("application", "")
            analysis = request.query_params.get("analysis", "")
            if request.method == "POST":
                if action == "upload":
                    engine.app(actor, project, app, "analyze")
                    chunks, length = [], 0
                    async for chunk in request.stream():
                        length += len(chunk)
                        if length > 50_000_000:
                            return JSONResponse({"error": "Archive exceeds 50 MB"}, status_code=413)
                        chunks.append(chunk)
                    result = engine.import_archive(actor, project, app, b"".join(chunks))
                else:
                    chunks, length = [], 0
                    async for chunk in request.stream():
                        length += len(chunk)
                        if length > 300_000:
                            return JSONResponse({"error": "Body exceeds limit"}, status_code=413)
                        chunks.append(chunk)
                    body = json.loads(b"".join(chunks))
                    if action == "applications":
                        result = engine.register(actor, project, body["name"], body.get("path"))
                    elif action == "analyze":
                        result = engine.start(actor, project, app)
                    elif action == "review":
                        result = engine.review(actor, project, app, body["id"], body["decision"], body.get("correction"))
                    elif action == "lineage":
                        result = engine.sql_lineage(actor, project, app, analysis, body["sql"], body.get("dialect", "postgres"))
                    else:
                        raise ValueError("Unsupported operation")
            elif action == "applications":
                result = engine.applications(actor, project)
            elif action == "analyses":
                engine.app(actor, project, app)
                result = engine.list_analyses(actor,project,app)["analyses"] if hasattr(engine,"list_analyses") else engine.store.list(project, app, "analysis")
            elif action == "architecture":
                result = engine.architecture(actor,project,app,analysis,
                    level=request.query_params.get("level","services"),service=request.query_params.get("service"))
            elif action == "coverage":
                result = engine.coverage(actor,project,app,analysis,offset=int(request.query_params.get("offset",0)))
            elif action == "sql-inventory":
                result = engine.sql_inventory(actor,project,app,analysis,offset=int(request.query_params.get("offset",0)))
            elif action == "graph":
                result = engine.graph(actor, project, app, analysis, request.query_params.get("focus"),
                                      limit=200, offset=int(request.query_params.get("offset", 0)))
            elif action == "search":
                result = engine.search(actor, project, app, analysis, request.query_params.get("q", ""),
                                       excerpts=request.query_params.get("excerpts") == "true")
            elif action == "suggestions":
                result = engine.suggestions(actor, project, app, analysis)
            elif action == "report":
                result = engine.report(actor, project, app, analysis)
            else:
                raise ValueError("Unsupported operation")
            engine.store.log(project, app, actor.subject, "viewer_" + action)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except PermissionError:
            return JSONResponse({"error": "Access denied"}, status_code=403)
        except (ValueError, KeyError, TypeError) as exc:
            return JSONResponse({"error": str(exc)[:500]}, status_code=400)

    async def index(request):
        return HTMLResponse((Path(__file__).parent / "viewer" / "index.html").read_text(encoding="utf-8"),
            headers={"Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
                     "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    async def health(request):
        return JSONResponse({"live":True})

    def catalog_ping():
        from sqlalchemy import text
        with engine.store.engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    async def ready(request):
        try:
            await run_in_threadpool(catalog_ping)
            return JSONResponse({"ready":True,"catalog":"production" if hasattr(engine,"catalog") else "legacy",
                "ephemeral":getenv("ORIGINSPAN_STORAGE_MODE") == "sqlite-demo"})
        except Exception:
            return JSONResponse({"ready":False},status_code=503)

    async def telemetry(request):
        try:
            actor=await identity(request)
            if "operate" not in actor.scopes:
                raise PermissionError()
            from .observability import prometheus
            try:
                await run_in_threadpool(catalog_ping)
                available = 1
            except Exception:
                available = 0
            return Response(prometheus() + "# TYPE diamond_catalog_available gauge\n"
                + f"diamond_catalog_available {available}\n", media_type="text/plain")
        except PermissionError:
            return JSONResponse({"error":"Access denied"},status_code=403)

    routes = [Route("/", index), Route("/healthz",health), Route("/readyz",ready), Route("/ops/metrics",telemetry), Route("/api/{project}/{action}", api, methods=["GET", "POST"])]
    if mcp:
        from urllib.parse import urlparse
        from mcp.server.transport_security import TransportSecuritySettings
        resource=urlparse(verifier.audience)
        transport=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=[resource.netloc],allowed_origins=[resource.scheme+"://"+resource.netloc])
        app = mcp.streamable_http_app(stateless_http=True, json_response=True,transport_security=transport)
        app.router.routes.extend(routes)
        return app
    return Starlette(routes=routes)
