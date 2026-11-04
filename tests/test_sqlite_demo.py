import secrets
import pytest
from sqlalchemy.engine import make_url
from diamond_mcp.hosted import configure


def settings(tmp_path):
    return {"ORIGINSPAN_AUTH_MODE":"demo-token", "ORIGINSPAN_DEMO_TOKEN":secrets.token_urlsafe(32),
        "ORIGINSPAN_STORAGE_MODE":"sqlite-demo", "ORIGINSPAN_STATE":str(tmp_path / "demo"),
        "RENDER_EXTERNAL_URL":"https://demo.onrender.com"}


def test_explicit_sqlite_demo_needs_no_postgres_and_ignores_old_database_url(tmp_path):
    env = settings(tmp_path)
    env["ORIGINSPAN_DATABASE_URL"] = "postgresql://invalid-do-not-contact/missing"
    configure(env)
    url = make_url(env["ORIGINSPAN_DATABASE_URL"])
    assert url.drivername == "sqlite"
    assert url.database == (tmp_path / "demo/state.sqlite").as_posix()
    assert env["ORIGINSPAN_EXTERNAL_WORKERS"] == "true"
    assert not (tmp_path / "demo").exists()  # Configuration never creates files.


def test_postgres_is_still_required_unless_explicit_demo_mode(tmp_path):
    env = settings(tmp_path)
    env.pop("ORIGINSPAN_STORAGE_MODE")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        configure(env)


@pytest.mark.parametrize("changes", [
    {"ORIGINSPAN_AUTH_MODE":"oidc"}, {"ORIGINSPAN_STORAGE_MODE":"unknown"},
    {"ORIGINSPAN_PGVECTOR":"true"}, {"ORIGINSPAN_DEMO_TOKEN":""}])
def test_demo_storage_cannot_disable_auth_or_silently_downgrade_features(tmp_path, changes):
    env = settings(tmp_path)
    env.update(changes)
    with pytest.raises(ValueError):
        configure(env)
