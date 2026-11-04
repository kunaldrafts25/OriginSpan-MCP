from diamond_mcp.engine import Engine, Config, Actor


def test_jpa_sqlalchemy_unknown_properties_and_never_executes_source(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "entity.py").write_text('''raise RuntimeError("MUST NOT EXECUTE")
class Agreement:
    C_ID = mapped_column("contract_key", Integer, primary_key=True)
    X17 = Column(Date)
''')
    (source / "Agreement.java").write_text('''class Agreement {
 @Column(name="contract_key")
 private String C_ID;
 private String unexplainedCode;
}''')
    e = Engine(Config(tmp_path / "state", (source,)))
    a = Actor("test", frozenset({"p"}))
    app = e.register(a, "p", "ORM", source)["id"]
    job = e.start(a, "p", app, background=False)
    assert job["status"] == "COMPLETED", job
    items = e.store.findings("p", app, job["id"])
    assert any(i["kind"] == "orm_field" and "contract_key" in i["description"] for i in items)
    assert any(i["name"] == "unexplainedCode" for i in items)


def test_recursive_yaml_contract_is_bounded(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "contract.yaml").write_text('openapi: "3.0.0"\nloop: &a\n  again: *a\n')
    e = Engine(Config(tmp_path / "state", (source,)))
    a = Actor("test", frozenset({"p"}))
    app = e.register(a, "p", "Recursive", source)["id"]
    job = e.start(a, "p", app, background=False)
    assert job["status"] == "COMPLETED", job
