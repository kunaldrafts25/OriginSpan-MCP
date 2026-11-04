"""Normalized discovery catalog and durable leased work queue.

No catalog query needs to deserialize a complete application's evidence or graph.
PostgreSQL uses SKIP LOCKED; SQLite uses fenced compare-and-swap claims.
"""
from __future__ import annotations

from .compat import getenv
import json
import time
import os
from uuid import uuid4

from sqlalchemy import (MetaData, Table, Column, String, Text, Integer, BigInteger, Float, Index,
                        select, update, delete, insert, and_, or_, func, text)
from sqlalchemy.exc import IntegrityError

meta = MetaData()
versions = Table("diamond_schema", meta, Column("component", String(30), primary_key=True), Column("version", Integer, nullable=False))
jobs = Table("d3_jobs", meta, Column("id", String(64), primary_key=True),
    Column("project", String(120), nullable=False), Column("app", String(80), nullable=False),
    Column("state", String(30), nullable=False), Column("created", Float, nullable=False),
    Column("inventory_done", Integer, default=0), Column("cancelled", Integer, default=0),
    Column("source", Text, nullable=False), Column("payload", Text, nullable=False))
active = Table("d3_active", meta, Column("project", String(120), primary_key=True),
    Column("app", String(80), primary_key=True), Column("job", String(64), nullable=False))
tasks = Table("d3_tasks", meta, Column("id", String(64), primary_key=True),
    Column("job", String(64), nullable=False), Column("project", String(120), nullable=False), Column("app", String(80), nullable=False),
    Column("kind", String(20), nullable=False), Column("state", String(20), nullable=False),
    Column("attempts", Integer, nullable=False, default=0), Column("token", String(64)),
    Column("owner", String(200)), Column("lease_until", Float, nullable=False, default=0),
    Column("available_at", Float, nullable=False, default=0), Column("payload", Text, nullable=False), Column("error", Text))
Index("d3_claim_idx", tasks.c.state, tasks.c.available_at, tasks.c.lease_until)
Index("d3_job_tasks_idx", tasks.c.job, tasks.c.state)
files = Table("d3_files", meta, Column("job", String(64), primary_key=True), Column("path", Text, primary_key=True),
    Column("project", String(120), nullable=False), Column("app", String(80), nullable=False),
    Column("sha", String(64)), Column("bytes", BigInteger, nullable=False), Column("state", String(40)), Column("detail", Text))
chunks = Table("d3_chunks", meta, Column("project", String(120), primary_key=True), Column("app", String(80), primary_key=True),
    Column("job", String(64), primary_key=True), Column("id", String(100), primary_key=True), Column("file", Text, nullable=False),
    Column("name", Text, nullable=False), Column("alias", String(600), nullable=False), Column("kind", String(80), nullable=False),
    Column("body", Text, nullable=False), Column("payload", Text, nullable=False), Column("vector", Text))
chunks.append_column(Column("entity", String(300), nullable=False, server_default=""))
Index("d3_alias_idx", chunks.c.project, chunks.c.app, chunks.c.job, chunks.c.alias)
Index("d3_kind_idx", chunks.c.project, chunks.c.app, chunks.c.job, chunks.c.kind)
entity_index=Index("d3_entity_idx", chunks.c.project, chunks.c.app, chunks.c.job, chunks.c.entity)
edges = Table("d3_edges", meta, Column("job", String(64), primary_key=True), Column("id", String(64), primary_key=True),
    Column("project", String(120), nullable=False), Column("app", String(80), nullable=False),
    Column("source", String(100), nullable=False), Column("target", String(100), nullable=False),
    Column("kind", String(80), nullable=False), Column("payload", Text, nullable=False))
Index("d3_edge_source_idx", edges.c.project, edges.c.app, edges.c.job, edges.c.source)
Index("d3_edge_target_idx", edges.c.project, edges.c.app, edges.c.job, edges.c.target)
units = Table("d3_units", meta, Column("job", String(64), primary_key=True), Column("id", String(100), primary_key=True),
    Column("project", String(120), nullable=False), Column("app", String(80), nullable=False),
    Column("kind", String(40), nullable=False), Column("file", Text, nullable=False), Column("payload", Text, nullable=False))
Index("d3_units_kind_idx", units.c.project, units.c.app, units.c.job, units.c.kind)
cache = Table("d3_cache", meta, Column("project", String(120), primary_key=True), Column("app", String(80), primary_key=True),
    Column("key", String(100), primary_key=True), Column("payload", Text, nullable=False))
interpretations=Table("d3_interpretations",meta,Column("project",String(120),primary_key=True),Column("app",String(80),primary_key=True),
    Column("job",String(64),primary_key=True),Column("id",String(100),primary_key=True),Column("status",String(40),nullable=False),
    Column("subject_alias",String(600),nullable=False),Column("meaning",Text,nullable=False),Column("payload",Text,nullable=False))
Index("d3_review_idx",interpretations.c.project,interpretations.c.app,interpretations.c.job,interpretations.c.status,interpretations.c.subject_alias)


class LeaseLost(RuntimeError):
    pass


class CapacityExceeded(ValueError):
    pass


class Catalog:
    VERSION = 4

    def __init__(self, engine, migrate=False):
        self.engine = engine
        if migrate:
            self.migrate()
        else:
            from sqlalchemy import inspect
            if "diamond_schema" not in inspect(engine).get_table_names():
                raise ValueError("Run originspan migrate before starting shared discovery workers")
            with engine.connect() as conn:
                if conn.execute(select(versions.c.version).where(versions.c.component == "catalog")).scalar() != self.VERSION:
                    raise ValueError("Catalog schema version mismatch; run the documented migration")

    def migrate(self):
        meta.create_all(self.engine)
        with self.engine.begin() as conn:
            current = conn.execute(select(versions.c.version).where(versions.c.component == "catalog")).scalar()
            if current is None:
                conn.execute(versions.insert().values(component="catalog", version=self.VERSION))
            elif current in {1,2,3}:
                if current==1:
                    conn.exec_driver_sql("ALTER TABLE d3_chunks ADD COLUMN entity VARCHAR(300) NOT NULL DEFAULT ''")
                    if self.engine.dialect.name=="sqlite":
                        conn.exec_driver_sql("UPDATE d3_chunks SET entity=substr(COALESCE(json_extract(payload,'$.entity'),''),1,300)")
                    else:
                        conn.exec_driver_sql("UPDATE d3_chunks SET entity=left(COALESCE(payload::jsonb->>'entity',''),300)")
                    entity_index.create(conn,checkfirst=True)
                if current<3 and self.engine.dialect.name=="postgresql":
                    conn.exec_driver_sql("ALTER TABLE d3_files ALTER COLUMN bytes TYPE BIGINT")
                conn.execute(update(versions).where(versions.c.component=="catalog").values(version=self.VERSION))
            elif current != self.VERSION:
                raise ValueError("Unsupported catalog migration; database was left unchanged")
            if self.engine.dialect.name == "sqlite":
                conn.exec_driver_sql("CREATE VIRTUAL TABLE IF NOT EXISTS d3_fts USING fts5(project UNINDEXED,app UNINDEXED,job UNINDEXED,id UNINDEXED,body)")
            else:
                conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS d3_text_idx ON d3_chunks USING GIN(to_tsvector('simple',body))")

    def upsert(self, conn, table, values, keys):
        from sqlalchemy.dialects.sqlite import insert as sq
        from sqlalchemy.dialects.postgresql import insert as pg
        statement = (sq if self.engine.dialect.name == "sqlite" else pg)(table).values(**values)
        conn.execute(statement.on_conflict_do_update(index_elements=keys,
            set_={k: getattr(statement.excluded, k) for k in values if k not in keys}))

    def clock(self,conn):
        # Shared leases use the database clock, not potentially skewed clocks on
        # different API/worker hosts. SQLite is the single-host compatibility tier.
        if self.engine.dialect.name=="postgresql":
            return float(conn.execute(text("SELECT extract(epoch FROM clock_timestamp())")).scalar())
        return time.time()

    def create_job(self, project, app, source, payload):
        id_ = uuid4().hex
        try:
            with self.engine.begin() as conn:
                if self.engine.dialect.name=="sqlite":
                    conn.exec_driver_sql("BEGIN IMMEDIATE")
                if self.engine.dialect.name=="postgresql":
                    conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),dict(scope="admission:"+project))
                count=conn.execute(select(func.count()).select_from(active).where(active.c.project==project)).scalar()
                if count>=int(getenv("DIAMOND_MAX_ACTIVE_ANALYSES","8")):
                    raise CapacityExceeded("Project analysis capacity reached; retry after existing work finishes")
                conn.execute(active.insert().values(project=project, app=app, job=id_))
                conn.execute(jobs.insert().values(id=id_, project=project, app=app, state="QUEUED", created=time.time(),
                    inventory_done=0, cancelled=0, source=source, payload=json.dumps(payload)))
                conn.execute(tasks.insert().values(id=id_, job=id_, project=project, app=app, kind="inventory", state="PENDING",
                    attempts=0, lease_until=0, available_at=0, payload="{}"))
        except IntegrityError as exc:
            raise ValueError("Application already has an active analysis") from exc
        return id_

    def job(self, project, app, id_):
        with self.engine.connect() as conn:
            row = conn.execute(select(jobs).where(jobs.c.id == id_, jobs.c.project == project, jobs.c.app == app)).mappings().first()
            if row is None:
                raise ValueError("Analysis not found in authorized application")
            counts = dict(conn.execute(select(tasks.c.state, func.count()).where(tasks.c.job == id_).group_by(tasks.c.state)).all())
            from sqlalchemy import case
            totals = conn.execute(select(func.count(), func.coalesce(func.sum(case((files.c.sha.is_not(None),files.c.bytes),else_=0)), 0),
                func.count(files.c.sha)).where(files.c.job == id_)).first()
        # PostgreSQL SUM(bigint) is NUMERIC/Decimal; these are integer byte
        # counters, and must remain JSON numbers in both viewer and MCP APIs.
        return {**row, "payload": json.loads(row["payload"]), "tasks": counts,
                "file_count": int(totals[0]), "source_bytes": int(totals[1]), "selected_files": int(totals[2])}

    def claim(self, owner, job=None, lease_seconds=60):
        for _ in range(10):
            with self.engine.begin() as conn:
                clock=self.clock(conn)
                eligible = and_(tasks.c.attempts < 3, jobs.c.cancelled == 0, jobs.c.state.in_(["QUEUED", "RUNNING"]),
                    or_(and_(tasks.c.state == "PENDING", tasks.c.available_at <= clock),
                        and_(tasks.c.state == "RUNNING", tasks.c.lease_until < clock)))
                query = select(tasks).join(jobs, jobs.c.id == tasks.c.job).where(eligible)
                if job:
                    query = query.where(tasks.c.job == job)
                query = query.order_by(tasks.c.available_at, tasks.c.id).limit(1)
                if self.engine.dialect.name == "postgresql":
                    query = query.with_for_update(skip_locked=True, of=tasks)
                row = conn.execute(query).mappings().first()
                if row is None:
                    return None
                token = uuid4().hex
                result = conn.execute(update(tasks).where(tasks.c.id == row["id"], tasks.c.attempts == row["attempts"],
                    tasks.c.state == row["state"], tasks.c.lease_until == row["lease_until"]).values(state="RUNNING", token=token,
                        owner=owner, attempts=row["attempts"] + 1, lease_until=clock + lease_seconds))
                if result.rowcount:
                    conn.execute(update(jobs).where(jobs.c.id == row["job"], jobs.c.cancelled == 0,jobs.c.state=="QUEUED").values(state="RUNNING"))
                    return {**row, "token": token, "owner": owner, "attempts": row["attempts"] + 1,
                            "lease_until": clock + lease_seconds, "payload": json.loads(row["payload"])}
        return None

    def fence(self, conn, task):
        query = select(tasks.c.token, tasks.c.state, tasks.c.lease_until, jobs.c.cancelled).join(jobs, jobs.c.id == tasks.c.job).where(tasks.c.id == task["id"])
        if self.engine.dialect.name == "postgresql":
            query = query.with_for_update(of=tasks)
        row = conn.execute(query).first()
        if row is None or row[0] != task["token"] or row[1] != "RUNNING" or row[2] < self.clock(conn) or row[3]:
            raise LeaseLost("Worker lease expired, was reassigned, or analysis was cancelled")

    def heartbeat(self, task, lease_seconds=60):
        with self.engine.begin() as conn:
            self.fence(conn, task)
            conn.execute(update(tasks).where(tasks.c.id == task["id"], tasks.c.token == task["token"])
                         .values(lease_until=self.clock(conn) + lease_seconds))

    def fail(self, task, error):
        with self.engine.begin() as conn:
            self.fence(conn, task)
            conn.execute(update(tasks).where(tasks.c.id == task["id"], tasks.c.token == task["token"]).values(
                state="DEAD" if task["attempts"] >= 3 else "PENDING", token=None,
                lease_until=0, available_at=self.clock(conn) + min(60, 2 ** task["attempts"]), error=error[:500]))
            if task["attempts"]>=3 and task["kind"]=="extract":
                conn.execute(update(files).where(files.c.job==task["job"],files.c.path==task["payload"]["path"])
                             .values(state="FAILED",detail=error[:500]))

    @staticmethod
    def scope(table, project, app, job):
        return and_(table.c.project == project, table.c.app == app, table.c.job == job)

    def get_chunks(self, project, app, job, ids=None, limit=100, offset=0, kind=None):
        if ids is not None and not ids:
            return []
        query = select(chunks.c.payload).where(self.scope(chunks, project, app, job))
        if ids is not None:
            query = query.where(chunks.c.id.in_(ids[:500]))
        if kind:
            query = query.where(chunks.c.kind.in_(kind))
        with self.engine.connect() as conn:
            return [json.loads(v) for v in conn.execute(query.order_by(chunks.c.id).limit(limit).offset(offset)).scalars()]

    def count_chunks(self, project, app, job):
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(chunks).where(self.scope(chunks, project, app, job))).scalar()

    def exact(self, project, app, job, alias, limit=150):
        with self.engine.connect() as conn:
            return list(conn.execute(select(chunks.c.id).where(self.scope(chunks, project, app, job), chunks.c.alias == alias)
                                     .order_by(chunks.c.id).limit(limit)).scalars())

    def lexical(self, project, app, job, terms, limit=150):
        if not terms:
            return []
        with self.engine.connect() as conn:
            if self.engine.dialect.name == "sqlite":
                query = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms[:60])
                rows = conn.execute(text("SELECT id FROM d3_fts WHERE project=:p AND app=:a AND job=:j AND body MATCH :q ORDER BY bm25(d3_fts) LIMIT :n"),
                                    dict(p=project, a=app, j=job, q=query, n=limit))
            else:
                query = " | ".join(t.replace("'", "") for t in terms[:60])
                rows = conn.execute(text("SELECT id FROM d3_chunks WHERE project=:p AND app=:a AND job=:j AND to_tsvector('simple',body) @@ to_tsquery('simple',:q) ORDER BY ts_rank(to_tsvector('simple',body),to_tsquery('simple',:q)) DESC,id LIMIT :n"),
                                    dict(p=project, a=app, j=job, q=query, n=limit))
            return [r[0] for r in rows]

    def neighbors(self, project, app, job, ids, limit=200):
        with self.engine.connect() as conn:
            rows = conn.execute(select(edges.c.payload).where(self.scope(edges, project, app, job),
                or_(edges.c.source.in_(ids[:200]), edges.c.target.in_(ids[:200]))).order_by(edges.c.id).limit(limit))
            return [json.loads(r[0]) for r in rows]

    def unit_page(self, project, app, job, kind, limit=100, offset=0):
        with self.engine.connect() as conn:
            query = select(units.c.payload).where(self.scope(units, project, app, job), units.c.kind == kind)
            return [json.loads(v) for v in conn.execute(query.order_by(units.c.id).limit(limit).offset(offset)).scalars()]
