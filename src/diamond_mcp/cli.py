"""Standalone entry point: `originspan --help`. No synthetic application required."""
from __future__ import annotations

from .compat import getenv, required
import argparse
import json
import os
from pathlib import Path
import sys

from .engine import Actor, Config
from .service import DiscoveryService as Engine


def main():
    parser = argparse.ArgumentParser(prog="originspan", description="OriginSpan discovery MCP and offline analysis")
    parser.add_argument("--state", type=Path, help="State directory (or ORIGINSPAN_STATE; legacy DIAMOND_STATE supported)")
    parser.add_argument("--root", type=Path, action="append", help="Trusted local source root; repeatable")
    parser.add_argument("--project", default=getenv("DIAMOND_PROJECT", "local"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Initialize/version the production catalog (run before shared servers)")
    worker = commands.add_parser("worker", help="Run an independent durable discovery worker")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--analysis")
    worker.add_argument("--name", default=None)
    serve = commands.add_parser("serve", help="Run stdio or authenticated shared HTTP MCP")
    serve.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    commands.add_parser("viewer", help="Run optional token-protected loopback viewer").add_argument("--port", type=int, default=8766)
    reg = commands.add_parser("register")
    reg.add_argument("name")
    reg.add_argument("--path", type=Path)
    commands.add_parser("applications")
    analyze = commands.add_parser("analyze")
    analyze.add_argument("application")
    analyze.add_argument("--recover", action="store_true", help="Mark stopped jobs interrupted before starting")
    search = commands.add_parser("search")
    search.add_argument("application")
    search.add_argument("analysis")
    search.add_argument("query")
    search.add_argument("--mode", choices=["exact", "lexical", "semantic", "hybrid"], default="hybrid")
    review = commands.add_parser("review")
    review.add_argument("application")
    review.add_argument("suggestion")
    review.add_argument("decision", choices=["ACCEPTED", "REJECTED"])
    review.add_argument("--reviewer", required=True)
    review.add_argument("--correction")
    disclose = commands.add_parser("disclosure")
    disclose.add_argument("application")
    disclose.add_argument("--excerpts", choices=["on", "off"], required=True)
    imp = commands.add_parser("import-zip")
    imp.add_argument("application")
    imp.add_argument("archive", type=Path)
    gh = commands.add_parser("import-github")
    gh.add_argument("application")
    gh.add_argument("repository")
    gh.add_argument("commit")
    up = commands.add_parser("upload", help="Upload approved snapshot to shared server; token read from environment")
    up.add_argument("url")
    up.add_argument("application")
    up.add_argument("archive", type=Path)
    download = commands.add_parser("model-download", help="Explicitly download pinned local embedding model")
    download.add_argument("destination")
    from .offline.cli import add_commands
    add_commands(commands)
    args = parser.parse_args()
    try:
        if args.command in {"models", "discover", "escalation-export", "evaluate-offline"}:
            from .offline.cli import execute
            execute(args)
            return
        if args.command == "model-download":
            from .embeddings import download_model
            download_model(args.destination)
            return
        if args.command == "upload":
            import httpx
            from urllib.parse import urlparse
            url = urlparse(args.url)
            if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost"}):
                raise ValueError("Upload requires HTTPS or loopback HTTP")
            if args.archive.stat().st_size > 50_000_000:
                raise ValueError("Archive exceeds 50 MB")
            response = httpx.post(args.url.rstrip("/") + f"/api/{args.project}/upload",
                params={"application": args.application}, content=args.archive.read_bytes(),
                headers={"Authorization": "Bearer " + required("DIAMOND_ACCESS_TOKEN")}, timeout=90, follow_redirects=False)
            response.raise_for_status()
            print(response.text)
            return
        shared = (args.command == "serve" and args.transport == "http") or args.command == "worker" and (
            bool(getenv("DIAMOND_DATABASE_URL","").startswith("postgresql")) or getenv("ORIGINSPAN_STORAGE_MODE") == "sqlite-demo")
        config = Config.load(shared)
        if args.state:
            config.state = args.state.resolve()
        if args.root:
            config.roots = tuple(p.resolve() for p in args.root)
        engine = Engine(config, migrate=True if args.command == "migrate" else None)
        if args.command == "migrate":
            print(json.dumps({"status":"OK", "catalog_schema":engine.catalog.VERSION}))
            return
        if args.command == "worker":
            import uuid
            import signal
            import threading
            stopping=threading.Event()
            signal.signal(signal.SIGTERM,lambda *_:stopping.set())
            name = args.name or "worker-"+uuid.uuid4().hex
            if args.once:
                engine.pipeline.step(name,args.analysis)
            else:
                try:
                    engine.pipeline.run(name,args.analysis,stopping)
                except KeyboardInterrupt:
                    pass
            return
        actor = Actor("local-operator", frozenset({args.project}), frozenset({"read", "analyze", "propose", "review"}))
        if args.command == "serve":
            from .server import create_server
            if shared:
                sqlite_demo = (getenv("ORIGINSPAN_STORAGE_MODE") == "sqlite-demo"
                    and getenv("ORIGINSPAN_AUTH_MODE") == "demo-token"
                    and bool(config.database_url and config.database_url.startswith("sqlite:///")))
                if not sqlite_demo and (not config.database_url or not config.database_url.startswith("postgresql")):
                    raise ValueError("Shared deployments require a PostgreSQL metadata URL")
                from .auth import configured_verifier
                from .web import create_web
                import uvicorn
                verifier = configured_verifier()
                mcp = create_server(engine, verifier=verifier)
                uvicorn.run(create_web(engine, mcp, verifier), host=args.host, port=args.port, access_log=False)
            else:
                # Project is explicit; MCP identity never carries review permission.
                create_server(engine, Actor("local-ai-client", frozenset({args.project}))).run(transport="stdio")
            return
        if args.command == "viewer":
            from .web import create_web
            import uvicorn
            uvicorn.run(create_web(engine, local_token=required("DIAMOND_VIEWER_TOKEN"), local_project=args.project), host="127.0.0.1", port=args.port, access_log=False)
            return
        if args.command == "register":
            result = engine.register(actor, args.project, args.name, args.path)
        elif args.command == "applications":
            result = engine.applications(actor, args.project)
        elif args.command == "analyze":
            if args.recover:
                engine.recover_jobs(actor, args.project, args.application)
            result = engine.start(actor, args.project, args.application, background=False)
        elif args.command == "search":
            result = engine.search(actor, args.project, args.application, args.analysis, args.query, args.mode)
        elif args.command == "review":
            actor = Actor(args.reviewer, actor.projects, actor.scopes)
            result = engine.review(actor, args.project, args.application, args.suggestion, args.decision, args.correction)
        elif args.command == "disclosure":
            result = engine.set_disclosure(actor, args.project, args.application, args.excerpts == "on")
        elif args.command == "import-zip":
            if args.archive.stat().st_size > 50_000_000:
                raise ValueError("Archive exceeds 50 MB")
            result = engine.import_archive(actor, args.project, args.application, args.archive.read_bytes())
        elif args.command == "import-github":
            result = engine.import_github(actor, args.project, args.application, args.repository, args.commit)
        print(json.dumps(result, indent=2))
        if isinstance(result, dict) and result.get("status") in {"FAILED", "CANCELLED"}:
            raise SystemExit(1)
    except (ValueError, PermissionError, KeyError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
