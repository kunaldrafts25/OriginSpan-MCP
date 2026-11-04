import io
import json
from pathlib import Path
import zipfile

import pytest

from diamond_mcp.engine import Actor, Config, Engine
from diamond_mcp.sources import extract_zip
from diamond_mcp.sql_intelligence import analyze_sql


AI = Actor("ai", frozenset({"p"}))
HUMAN = Actor("human", frozenset({"p"}), frozenset({"read", "analyze", "review"}))


def setup(tmp_path, embeddings=None):
    root = tmp_path / "sources"
    root.mkdir()
    (root / "api.py").write_text('''class Contract:
    """Commercial agreement, not a customer entity."""
    def lookup(self, C_ID):
        return {"X17": C_ID}
''')
    (root / "dictionary.md").write_text("# Agreement dictionary\nX17 denotes the renewal deadline for a commercial agreement.\nC_ID identifies a contract.\n")
    (root / "schema.sql").write_text("CREATE TABLE agreement (C_ID INT PRIMARY KEY, X17 DATE, active BOOL);")
    (root / "contract.json").write_text(json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "properties": {
        "X17": {"type": "string", "description": "Renewal deadline", "examples": ["record-never-index-me"]}}}))
    (root / ".env").write_text("SECRET=must-not-copy")
    engine = Engine(Config(tmp_path / "state", (root,)), embeddings)
    app = engine.register(AI, "p", "Contracts", root)["id"]
    job = engine.start(AI, "p", app, background=False)
    assert job["status"] == "COMPLETED", job
    return engine, root, app, job["id"]


def test_unfamiliar_field_inventory_and_context(tmp_path):
    e, root, app, analysis = setup(tmp_path)
    found = e.search(AI, "p", app, analysis, "X17", "exact")
    assert any(i["kind"] == "contract_field" for i in found["findings"])
    assert any(i["kind"] == "literal_key_candidate" for i in found["findings"])
    assert all("example" not in i.get("description", "") for i in found["findings"])
    context = e.context(AI, "p", app, analysis, "renewal deadline")
    assert any("X17" in i["description"] or i["name"] == "X17" for i in context["evidence"])
    assert context["interpretation_status"] == "UNRESOLVED"
    assert any("contract" in i["description"].lower() for i in context["related"] + context["evidence"])
    assert not (e.snapshot_path(app, analysis) / ".env").exists()
    assert "record-never-index-me" not in json.dumps(e.store.findings("p", app, analysis))


def test_isolation_applies_to_all_channels_and_object_reads(tmp_path):
    e, _, app, analysis = setup(tmp_path)
    for operation in (lambda: e.search(Actor("other", frozenset({"q"})), "p", app, analysis, "X17"),
                      lambda: e.context(AI, "p", "foreign", analysis, "X17"),
                      lambda: e.report(AI, "p", "foreign", analysis),
                      lambda: e.graph(Actor("limited", frozenset({"p"}), applications=frozenset()), "p", app, analysis)):
        with pytest.raises((PermissionError, ValueError)):
            operation()
    other = e.register(AI, "p", "Other", e.config.roots[0])["id"]
    with pytest.raises(ValueError):
        e.search(AI, "p", other, analysis, "X17")


def test_suggestions_validity_reviews_staleness_and_no_self_approval(tmp_path):
    e, root, app, analysis = setup(tmp_path)
    id_ = e.search(AI, "p", app, analysis, "C_ID", "exact")["findings"][0]["id"]
    suggestion = dict(subject="C_ID", meaning="Contract identifier", evidence_ids=[id_], alternatives=["Customer identifier"], gaps=[])
    assert not e.validate(AI, "p", app, analysis, [{**suggestion, "evidence_ids": ["nonexistent"]}])["valid"]
    assert not e.validate(AI, "p", app, analysis, [{**suggestion, "status": "ACCEPTED"}])["valid"]
    result = e.propose(AI, "p", app, analysis, [suggestion])
    proposed = result["ids"][0]
    assert e.suggestions(AI, "p", app, analysis)[0]["kind"] == "ai_hypothesis"
    with pytest.raises(PermissionError):
        e.review(AI, "p", app, proposed, "ACCEPTED")
    privileged_ai = Actor("ai", AI.projects, frozenset({"read", "review"}))
    with pytest.raises(PermissionError):
        e.review(privileged_ai, "p", app, proposed, "ACCEPTED")
    approved = e.review(HUMAN, "p", app, proposed, "ACCEPTED")
    assert approved["history"][0]["actor"] == "human"
    assert any(r["channel"] == "reviewed_vocabulary" for f in e.search(AI, "p", app, analysis, "identifier")["findings"] for r in f["retrieval"])
    (root / "dictionary.md").write_text("C_ID is no longer used.\n")
    latest = e.start(AI, "p", app, background=False)
    assert latest["status"] == "COMPLETED"
    assert not e.validate(AI, "p", app, analysis, [suggestion])["valid"]
    assert e.suggestions(AI, "p", app, analysis)[0]["status"] == "NEEDS_REVALIDATION"
    assert "commercial agreement" in (e.snapshot_path(app, analysis) / "dictionary.md").read_text()


def test_metadata_default_excerpts_opt_in_secret_filter_and_audit(tmp_path):
    e, root, app, analysis = setup(tmp_path)
    with pytest.raises(PermissionError):
        e.search(AI, "p", app, analysis, "C_ID", excerpts=True)
    with pytest.raises(PermissionError):
        e.set_disclosure(AI, "p", app, True)
    e.set_disclosure(HUMAN, "p", app, True)
    assert any("excerpt" in f for f in e.search(AI, "p", app, analysis, "C_ID", excerpts=True)["findings"])
    from diamond_mcp.sources import redact
    assert "secret-value" not in redact('password="secret-value"')
    from diamond_mcp.store import audit
    from sqlalchemy import select
    with e.store.engine.connect() as conn:
        logs = str(conn.execute(select(audit)).fetchall())
    assert "commercial agreement" not in logs


def test_shared_mode_denies_paths_and_zip_slip(tmp_path):
    e = Engine(Config(tmp_path / "state", (tmp_path,), shared=True))
    with pytest.raises(PermissionError):
        e.register(AI, "p", "remote", tmp_path)
    app = e.register(AI, "p", "remote")["id"]
    for filename in ("../escape.py", "/escape.py", "C:\\escape.py"):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            z.writestr(filename, "pass")
        with pytest.raises(ValueError):
            e.import_archive(AI, "p", app, stream.getvalue())


def test_neural_unavailable_is_explicit_and_no_network_fallback(tmp_path):
    e, _, app, analysis = setup(tmp_path)
    result = e.search(AI, "p", app, analysis, "agreement renewal", "semantic")
    assert not result["findings"] and "unavailable" in result["summary"]


class TestEmbeddings:
    """Controlled adapter for rank-fusion correctness, not a neural quality claim."""
    __test__ = False
    profile = {"model": "test-only", "revision": "1"}
    def encode(self, values, query=False):
        return [[1.0, 0.0] if any(x in v.lower() for x in ["renewal", "expires", "deadline"]) else [0.0, 1.0] for v in values]


def test_vector_channel_fusion_and_persistence(tmp_path):
    e, _, app, analysis = setup(tmp_path, TestEmbeddings())
    result = e.search(AI, "p", app, analysis, "when agreement expires", "semantic")
    assert any("renewal" in i["description"].lower() for i in result["findings"][:3])
    assert all(i["retrieval"][0]["channel"] == "neural" for i in result["findings"])
    restarted = Engine(e.config, TestEmbeddings())
    assert restarted.search(AI, "p", app, analysis, "expires", "semantic")["findings"]


def test_sql_values_separate_from_predicate_and_schema_resolution():
    result = analyze_sql("SELECT a.X17 AS expiry FROM agreement a JOIN party p ON a.C_ID=p.id WHERE a.active=TRUE",
                         schema={"agreement": {"X17": "DATE", "C_ID": "INT", "active": "BOOLEAN"}, "party": {"id": "INT"}})
    assert result["value_lineage"][0]["sources"] == ["agreement.x17"] or result["value_lineage"][0]["sources"] == ["agreement.X17"]
    assert {i["kind"] for i in result["predicate_dependencies"]} == {"WHERE", "JOIN"}
    assert "active" not in str(result["value_lineage"])


def test_cancellation_and_recovery(tmp_path, monkeypatch):
    e, _, app, _ = setup(tmp_path)
    def cancel_copy(source, target, check):
        jobs = e.store.list("p", app, "analysis")
        active = next(j for j in jobs if j["status"] == "RUNNING")
        e.cancel(AI, "p", app, active["id"])
        check()
    monkeypatch.setattr("diamond_mcp.engine.copy_folder", cancel_copy)
    job = e.start(AI, "p", app, background=False)
    assert job["status"] == "CANCELLED"
