import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest
import yaml
from sqlalchemy.engine import make_url
from diamond_mcp.hosted import configure, supervise


def settings(tmp_path):
    return {"ORIGINSPAN_DATABASE_URL":"postgresql://user:p%40ss@database:5432/diamond?sslmode=require",
            "ORIGINSPAN_STATE":str(tmp_path / "state"), "PORT":"10000",
            "RENDER_EXTERNAL_URL":"https://example.onrender.com",
            "ORIGINSPAN_OIDC_ISSUER":"https://identity.example/realm",
            "ORIGINSPAN_OIDC_JWKS":"https://identity.example/keys"}


def test_render_configuration_preserves_password_and_ssl(tmp_path):
    env = settings(tmp_path)
    port, state = configure(env)
    assert port == 10000 and state == tmp_path / "state"
    url = make_url(env["ORIGINSPAN_DATABASE_URL"])
    assert url.drivername == "postgresql+psycopg" and url.password == "p@ss"
    assert url.query["sslmode"] == "require"
    assert env["ORIGINSPAN_RESOURCE_URL"] == "https://example.onrender.com/mcp"
    assert env["ORIGINSPAN_EXTERNAL_WORKERS"] == "true"
    env["ORIGINSPAN_RESOURCE_URL"] = "https://custom.example/mcp"
    configure(env)
    assert env["ORIGINSPAN_RESOURCE_URL"] == "https://custom.example/mcp"


@pytest.mark.parametrize("key,value", [
    ("ORIGINSPAN_DATABASE_URL", "sqlite:///fallback.sqlite"),
    ("ORIGINSPAN_OIDC_ISSUER", ""), ("ORIGINSPAN_OIDC_JWKS", "http://identity.example/keys"),
    ("ORIGINSPAN_RESOURCE_URL", "https://example.onrender.com/wrong"),
    ("ORIGINSPAN_RESOURCE_URL", "https://user:secret@example.com/mcp"),
    ("PORT", "0"), ("PORT", "65536"), ("ORIGINSPAN_STATE", "relative/state")])
def test_hosted_fails_closed_on_invalid_configuration(tmp_path, key, value):
    env = settings(tmp_path)
    env[key] = value
    with pytest.raises((ValueError, KeyError)):
        configure(env)


def test_supervisor_reaps_survivor_when_one_child_exits():
    children = []
    def spawn(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        children.append(process)
        return process
    commands = [("api", [sys.executable, "-c", "raise SystemExit(7)"]),
                ("worker", [sys.executable, "-c", "import time; time.sleep(60)"])]
    assert supervise(commands, threading.Event(), spawn) == 1
    assert all(child.poll() is not None for child in children)


def test_supervisor_graceful_stop_reaps_both_children():
    stop = threading.Event()
    children = []
    def spawn(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        children.append(process)
        if len(children) == 2:
            stop.set()
        return process
    commands = [(name, [sys.executable, "-c", "import time; time.sleep(60)"]) for name in ["api", "worker"]]
    assert supervise(commands, stop, spawn) == 0
    assert all(child.poll() is not None for child in children)


def test_render_blueprint_uses_one_shared_disk_and_authentication():
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "render.yaml").read_text())
    assert len(config["services"]) == 1
    service = config["services"][0]
    assert service["runtime"] == "docker" and service["plan"] != "free"
    assert service["healthCheckPath"] == "/readyz"
    assert service["disk"]["mountPath"] == "/var/data"
    assert "preDeployCommand" not in service and "dockerCommand" not in service
    env = {entry["key"]:entry for entry in service["envVars"]}
    assert env["ORIGINSPAN_OIDC_ISSUER"]["sync"] is False
    assert env["ORIGINSPAN_OIDC_JWKS"]["sync"] is False
    assert "fromDatabase" in env["ORIGINSPAN_DATABASE_URL"]
    assert env["ORIGINSPAN_NEURAL_ENABLED"]["value"] == "false"
    image = (root / "Dockerfile.render").read_text()
    assert "model-download" not in image and "torch==" not in image
    assert "ORIGINSPAN_STATE=" not in image  # Do not mask a legacy deployment's explicit state setting.
    assert config["databases"][0]["ipAllowList"] == []


def test_startup_errors_do_not_disclose_database_credentials(tmp_path):
    env = {**os.environ, **settings(tmp_path), "ORIGINSPAN_DATABASE_URL":"scheme-with-secret-password://bad"}
    result = subprocess.run([sys.executable, "-m", "diamond_mcp.hosted"], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    assert "secret-password" not in result.stderr
    assert json.loads(result.stderr)["event"] == "hosted_start_failed"
