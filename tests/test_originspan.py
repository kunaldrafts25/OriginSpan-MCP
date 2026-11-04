from pathlib import Path
import subprocess
import sys

import pytest
from diamond_mcp.auth import actor_from_claims
from diamond_mcp.compat import getenv, required, default_state
from diamond_mcp.hosted import configure
from diamond_mcp.sources import SKIP


def test_new_settings_take_precedence_without_losing_legacy_settings(monkeypatch):
    monkeypatch.setenv("DIAMOND_PROJECT", "legacy")
    monkeypatch.delenv("ORIGINSPAN_PROJECT", raising=False)
    assert getenv("ORIGINSPAN_PROJECT") == "legacy"
    monkeypatch.setenv("ORIGINSPAN_PROJECT", "current")
    assert getenv("DIAMOND_PROJECT") == getenv("ORIGINSPAN_PROJECT") == "current"
    assert required("DIAMOND_PROJECT") == "current"


def test_existing_state_is_reused_without_moving_or_rewriting_it(tmp_path):
    assert default_state(tmp_path) == tmp_path / ".originspan"
    legacy = tmp_path / ".diamond"
    legacy.mkdir()
    (legacy / "saved-analysis.txt").write_text("unchanged")
    assert default_state(tmp_path) == legacy
    (tmp_path / ".originspan").mkdir()
    assert default_state(tmp_path) == tmp_path / ".originspan"
    assert (legacy / "saved-analysis.txt").read_text() == "unchanged"
    assert {".originspan", ".diamond"} <= SKIP


@pytest.mark.parametrize("prefix", ["diamond", "originspan"])
def test_legacy_and_current_identity_claims(prefix):
    a = actor_from_claims({"sub":"reviewer", prefix + "_projects":["p"],
        prefix + "_applications":["a"], "scope":prefix + ":read " + prefix + ":propose"})
    a.require("p", "read", "a")
    with pytest.raises(PermissionError):
        a.require("p", "review", "a")


def test_claims_are_not_merged_across_namespaces():
    actor = actor_from_claims({"sub":"a", "originspan_projects":["new"], "diamond_projects":["old"], "scope":"originspan:read"})
    actor.require("new", "read")
    with pytest.raises(PermissionError):
        actor.require("old", "read")


def test_old_hosted_environment_is_normalized_to_current_settings(tmp_path):
    env = {"DIAMOND_DATABASE_URL":"postgresql://u:password@database/metadata",
           "DIAMOND_STATE":str(tmp_path), "DIAMOND_OIDC_ISSUER":"https://id.example",
           "DIAMOND_OIDC_JWKS":"https://id.example/keys", "RENDER_EXTERNAL_URL":"https://app.onrender.com"}
    configure(env)
    assert env["ORIGINSPAN_DATABASE_URL"].startswith("postgresql+psycopg://")
    assert env["ORIGINSPAN_STATE"] == str(tmp_path)
    assert env["ORIGINSPAN_EXTERNAL_WORKERS"] == "true"


def test_public_module_and_viewer_use_originspan():
    import originspan
    import tomllib
    expected = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())["project"]["version"]
    assert originspan.__version__ == expected
    result = subprocess.run([sys.executable, "-m", "originspan", "--help"], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and "usage: originspan" in result.stdout
    root = Path(__file__).resolve().parents[1]
    page = (root / "src/diamond_mcp/viewer/index.html").read_text(encoding="utf-8")
    assert "<title>OriginSpan Discovery</title>" in page and "<h1>OriginSpan Discovery</h1>" in page
