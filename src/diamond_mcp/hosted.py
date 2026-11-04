"""Single-instance Render runtime: migrate, supervise API and durable worker.

Both children use the same persistent disk. No customer source is executed.
An unexpected child exit fails the service so the platform can restart it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url


def configure(environ):
    """Validate/normalize before opening state or binding a public socket."""
    from .compat import canonical_environment
    canonical_environment(environ)
    mode = environ.get("ORIGINSPAN_AUTH_MODE", "oidc")
    if mode not in {"oidc", "demo-token"}:
        raise ValueError("ORIGINSPAN_AUTH_MODE must be oidc or demo-token")
    storage = environ.get("ORIGINSPAN_STORAGE_MODE", "postgres")
    if storage not in {"postgres", "sqlite-demo"}:
        raise ValueError("ORIGINSPAN_STORAGE_MODE must be postgres or sqlite-demo")
    if storage == "sqlite-demo":
        if mode != "demo-token":
            raise ValueError("sqlite-demo storage requires explicit demo-token authentication")
        if environ.get("ORIGINSPAN_PGVECTOR", "false").lower() == "true":
            raise ValueError("sqlite-demo does not support pgvector; set ORIGINSPAN_PGVECTOR=false")
    else:
        raw = environ.get("ORIGINSPAN_DATABASE_URL") or environ.get("DATABASE_URL")
        if not raw:
            raise ValueError("Set ORIGINSPAN_DATABASE_URL to the Render database URL")
        url = make_url(raw.strip())
        if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
            raise ValueError("Hosted discovery requires PostgreSQL")
        environ["ORIGINSPAN_DATABASE_URL"] = url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    identity_keys = ("ORIGINSPAN_OIDC_ISSUER", "ORIGINSPAN_OIDC_JWKS") if mode == "oidc" else ()
    if mode == "demo-token":
        from .auth import validate_demo_settings
        validate_demo_settings(environ.get("ORIGINSPAN_DEMO_TOKEN"), environ.get("ORIGINSPAN_PROJECT", "demo"))
    for key in identity_keys:
        if not environ.get(key):
            raise ValueError("Missing " + key)
    resource = environ.get("ORIGINSPAN_RESOURCE_URL")
    if not resource:
        external = environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
        parsed = urlsplit(external)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("Set ORIGINSPAN_RESOURCE_URL or a valid HTTPS RENDER_EXTERNAL_URL")
        resource = external + "/mcp"
    for value in (resource, *(environ[key] for key in identity_keys)):
        parsed = urlsplit(value)
        if not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Identity/resource URLs must not contain credentials, query or fragment")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}):
            raise ValueError("Hosted identity/resource endpoints require HTTPS")
    if urlsplit(resource).path != "/mcp":
        raise ValueError("ORIGINSPAN_RESOURCE_URL must end with /mcp")
    environ["ORIGINSPAN_RESOURCE_URL"] = resource
    port = int(environ.get("PORT", "10000"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    default = "/var/data/originspan"
    if not Path(default).exists() and Path("/var/data/diamond").exists():
        default = "/var/data/diamond"
    state = Path(environ.get("ORIGINSPAN_STATE", default))
    if not state.is_absolute() or state.is_symlink():
        raise ValueError("ORIGINSPAN_STATE must be an absolute, non-symlink directory")
    environ["ORIGINSPAN_STATE"] = str(state)
    if storage == "sqlite-demo":
        # Explicit demo selection, never a fallback after PostgreSQL failure.
        # Catalog, snapshots and blobs share the same ephemeral lifecycle.
        environ["ORIGINSPAN_DATABASE_URL"] = "sqlite:///" + (state / "state.sqlite").as_posix()
    environ["ORIGINSPAN_EXTERNAL_WORKERS"] = "true"
    environ.setdefault("ORIGINSPAN_MAX_ACTIVE_ANALYSES", "2")
    environ.setdefault("ORIGINSPAN_MODEL_THREADS", "1")
    environ.setdefault("ORIGINSPAN_MIN_FREE_BYTES", "268435456")
    environ.setdefault("ORIGINSPAN_LOG_JSON", "true")
    return port, state


def prepare_state(state):
    state.mkdir(parents=True, exist_ok=True)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        # Bootstrap only the state directory. Never recursively chown uploads.
        os.chown(state, 10001, 10001, follow_symlinks=False)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    probe = state / (".write-probe-" + str(os.getpid()))
    with probe.open("x") as stream:
        stream.write("writable")
    probe.unlink()


def emit(event, **fields):
    print(json.dumps({"event":event, **fields}), file=sys.stderr, flush=True)


def startup_diagnostic(exc, stage):
    """Classify failures without logging driver strings, URLs, SQL or secrets."""
    from sqlalchemy.exc import DBAPIError
    result = {"stage":stage, "code":"STARTUP_FAILED",
              "hint":"Check database, authentication settings, PORT and filesystem permissions"}
    if isinstance(exc, DBAPIError):
        original = exc.orig
        message = str(original).lower()
        sqlstate = getattr(original, "sqlstate", None)
        code, hint = "DATABASE_OPERATION_FAILED", "Check database availability and the complete Internal Database URL"
        if sqlstate in {"28P01", "28000"} or any(s in message for s in ["password authentication failed", "no password supplied", "authentication failed"]):
            code, hint = "DATABASE_AUTH_FAILED", "Copy the current full Internal Database URL again; verify the database user/password"
        elif sqlstate == "3D000" or "database" in message and "does not exist" in message:
            code, hint = "DATABASE_NOT_FOUND", "The database name in ORIGINSPAN_DATABASE_URL does not exist; copy the full URL from Render"
        elif any(s in message for s in ["could not translate host", "name or service not known", "failed to resolve", "getaddrinfo", "name does not resolve", "temporary failure in name resolution"]):
            code, hint = "DATABASE_DNS_FAILED", "Check the database hostname and that both services share the same Render account and region"
        elif "connection refused" in message:
            code, hint = "DATABASE_CONNECTION_REFUSED", "Check database status, hostname and port in the Internal Database URL"
        elif any(s in message for s in ["timeout", "timed out"]):
            code, hint = "DATABASE_CONNECTION_TIMEOUT", "Check database availability and network access between the service and database"
        elif "no pg_hba.conf entry" in message or "ip address is not allowed" in message:
            code, hint = "DATABASE_NETWORK_ACCESS_DENIED", "Check the database external-access rules for this web service"
        elif any(s in message for s in ["ssl", "tls", "certificate", "encryption"]):
            code, hint = "DATABASE_TLS_FAILED", "TLS negotiation failed; inspect tls_reason and database external-access settings"
            reasons = [
                ("invalid sslmode", "INVALID_SSLMODE_OPTION"),
                ("no sni", "SNI_REQUIRED"),
                ("certificate verify failed", "CERTIFICATE_VERIFY_FAILED"),
                ("wrong version number", "WRONG_TLS_NEGOTIATION"),
                ("unsupported protocol", "TLS_PROTOCOL_UNSUPPORTED"),
                ("eof", "PEER_CLOSED_TLS_CONNECTION"),
                ("closed unexpectedly", "PEER_CLOSED_TLS_CONNECTION"),
                ("does not support ssl", "SERVER_REJECTS_TLS")]
            result["tls_reason"] = next((reason for pattern, reason in reasons if pattern in message), "UNCLASSIFIED_TLS_ERROR")
        elif sqlstate == "42501" or "permission denied" in message or "must be owner" in message:
            code, hint = "DATABASE_PERMISSION_DENIED", "The database role must own or be permitted to create the OriginSpan catalog tables/indexes"
        result.update(code=code, hint=hint)
        if isinstance(sqlstate, str) and len(sqlstate) == 5 and sqlstate.isascii() and sqlstate.isalnum():
            result["sqlstate"] = sqlstate
    elif stage == "state_directory" and isinstance(exc, OSError):
        result.update(code="STATE_DIRECTORY_UNWRITABLE", hint="For the free demo set ORIGINSPAN_STATE=/tmp/originspan")
    elif stage == "configuration":
        result.update(code="CONFIGURATION_INVALID", hint="Check database URL, auth mode/demo token, resource URL and PORT")
    return result


def stop_children(children, grace=25):
    for child in children:
        if child.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(child.pid, signal.SIGTERM)
                else:
                    child.terminate()
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + grace
    while any(child.poll() is None for child in children) and time.monotonic() < deadline:
        time.sleep(.1)
    for child in children:
        if os.name == "posix":
            # Also reap parser descendants if their parent has already exited.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def supervise(commands, stopping, popen=subprocess.Popen):
    children = []
    try:
        for name, command in commands:
            children.append(popen(command, start_new_session=os.name == "posix"))
            emit("hosted_child_started", component=name)
        while not stopping.wait(.25):
            for (name, _), child in zip(commands, children):
                if child.poll() is not None:
                    emit("hosted_child_exited", component=name, exit_code=child.returncode)
                    return 1
        return 0
    finally:
        stop_children(children)


def main():
    stage = "configuration"
    try:
        if len(sys.argv) > 1:
            raise ValueError("Hosted runtime takes no command arguments")
        port, state = configure(os.environ)
        stage = "state_directory"
        prepare_state(state)
        stage = "public_demo_restore"
        from .demo_seed import maybe_restore
        restored=maybe_restore(os.environ,state)
        if restored:
            emit("hosted_public_demo",**restored)
        from .engine import Config
        from .service import DiscoveryService
        # Render pre-deploy commands cannot access the persistent disk. Migrate
        # on this single instance before any HTTP or worker child is launched.
        stage = "database_catalog"
        engine = DiscoveryService(Config.load(shared=True), migrate=True)
        engine.store.engine.dispose()
        emit("hosted_catalog_ready", schema=engine.catalog.VERSION,
             storage=os.environ.get("ORIGINSPAN_STORAGE_MODE", "postgres"),
             ephemeral=os.environ.get("ORIGINSPAN_STORAGE_MODE") == "sqlite-demo")
        stopping = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_:stopping.set())
        signal.signal(signal.SIGINT, lambda *_:stopping.set())
        base = [sys.executable, "-I", "-m", "diamond_mcp"]
        stage = "service_processes"
        return supervise([
            ("worker", base + ["worker", "--name", "originspan-worker"]),
            ("api", base + ["serve", "--transport", "http", "--host", "0.0.0.0", "--port", str(port)])
        ], stopping)
    except Exception as exc:
        # Driver/config errors can contain secrets; expose only their class.
        emit("hosted_start_failed", error_type=type(exc).__name__, **startup_diagnostic(exc, stage))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
