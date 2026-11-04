"""Portable durable state. Every object is keyed by project and application.

JSON payloads retain extractor provenance; SQL indexes keep retrieval within a
single authorized snapshot. SQLite FTS5 / PostgreSQL tsvector supply lexical rank.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from sqlalchemy import create_engine, MetaData, Table, Column, String, Text, Integer, select, text

metadata = MetaData()
objects = Table("discovery_objects", metadata,
    Column("project", String(120), primary_key=True),
    Column("application", String(80), primary_key=True),
    Column("kind", String(40), primary_key=True),
    Column("id", String(100), primary_key=True),
    Column("payload", Text, nullable=False))
evidence = Table("discovery_evidence", metadata,
    Column("project", String(120), primary_key=True),
    Column("application", String(80), primary_key=True),
    Column("analysis", String(80), primary_key=True),
    Column("id", String(100), primary_key=True),
    Column("name", Text, nullable=False), Column("kind", String(60), nullable=False),
    Column("body", Text, nullable=False), Column("payload", Text, nullable=False))
audit = Table("discovery_audit", metadata,
    Column("id", String(80), primary_key=True), Column("project", String(120)),
    Column("application", String(80)), Column("actor", String(200)),
    Column("action", String(80)), Column("at", String(40)))


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


class Store:
    def __init__(self, url: str):
        self.url = url
        self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}
                                    if url.startswith("sqlite") else {}, pool_pre_ping=True)
        metadata.create_all(self.engine)
        if self.engine.dialect.name == "sqlite":
            with self.engine.begin() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=WAL")
                conn.exec_driver_sql("CREATE VIRTUAL TABLE IF NOT EXISTS discovery_fts USING fts5(project UNINDEXED, application UNINDEXED, analysis UNINDEXED, id UNINDEXED, body)")
        else:
            with self.engine.begin() as conn:
                conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS discovery_text_idx ON discovery_evidence USING GIN (to_tsvector('simple', body))")

    def put(self, project, app, kind, id_, payload):
        with self.engine.begin() as conn:
            # Dialect upsert also supports concurrent job/viewer processes.
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            insert = sqlite_insert if self.engine.dialect.name == "sqlite" else pg_insert
            stmt = insert(objects).values(project=project, application=app, kind=kind, id=id_, payload=json.dumps(payload))
            conn.execute(stmt.on_conflict_do_update(index_elements=["project", "application", "kind", "id"],
                         set_={"payload": stmt.excluded.payload}))

    def get(self, project, app, kind, id_):
        with self.engine.connect() as conn:
            value = conn.execute(select(objects.c.payload).where(objects.c.project == project,
                objects.c.application == app, objects.c.kind == kind, objects.c.id == id_)).scalar_one_or_none()
        if value is None:
            raise ValueError("Object not found in authorized application")
        return json.loads(value)

    def list(self, project, app, kind):
        with self.engine.connect() as conn:
            rows = conn.execute(select(objects.c.payload).where(objects.c.project == project,
                objects.c.application == app, objects.c.kind == kind).order_by(objects.c.id)).scalars()
            return [json.loads(row) for row in rows]

    def add_evidence(self, project, app, analysis, items):
        with self.engine.begin() as conn:
            conn.execute(evidence.insert(), [dict(project=project, application=app, analysis=analysis,
                id=item["id"], name=item["name"], kind=item["kind"], body=item["search_text"],
                payload=json.dumps(item)) for item in items]) if items else None
            if self.engine.dialect.name == "sqlite":
                conn.execute(text("INSERT INTO discovery_fts(project,application,analysis,id,body) VALUES (:p,:a,:s,:id,:body)"),
                    [dict(p=project, a=app, s=analysis, id=item["id"], body=item["search_text"]) for item in items]) if items else None

    def findings(self, project, app, analysis):
        with self.engine.connect() as conn:
            rows = conn.execute(select(evidence.c.payload).where(evidence.c.project == project,
                evidence.c.application == app, evidence.c.analysis == analysis).order_by(evidence.c.id)).scalars()
            return [json.loads(row) for row in rows]

    def lexical(self, project, app, analysis, terms, limit=100):
        if not terms:
            return []
        with self.engine.connect() as conn:
            if self.engine.dialect.name == "sqlite":
                query = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
                rows = conn.execute(text("SELECT id FROM discovery_fts WHERE project=:p AND application=:a AND analysis=:s AND body MATCH :q ORDER BY bm25(discovery_fts) LIMIT :n"),
                                    dict(p=project, a=app, s=analysis, q=query, n=limit))
            else:
                rows = conn.execute(text("SELECT id FROM discovery_evidence WHERE project=:p AND application=:a AND analysis=:s AND to_tsvector('simple',body) @@ plainto_tsquery('simple',:q) ORDER BY ts_rank(to_tsvector('simple',body),plainto_tsquery('simple',:q)) DESC LIMIT :n"),
                                    dict(p=project, a=app, s=analysis, q=" ".join(terms), n=limit))
            return [row[0] for row in rows]

    def log(self, project, app, actor, action):
        with self.engine.begin() as conn:
            conn.execute(audit.insert().values(id=uid(), project=project, application=app,
                                              actor=actor, action=action, at=now()))
