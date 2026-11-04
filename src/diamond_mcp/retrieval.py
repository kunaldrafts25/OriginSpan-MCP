"""Scoped retrieval: bounded SQL pages, streaming local cosine, native pgvector.

Shared vector tables are physically scoped by project/application/snapshot. ANN
therefore cannot consider another tenant's vectors and filter afterward.
"""
from __future__ import annotations
import hashlib
import heapq
import json
import math
from sqlalchemy import select, text
from .catalog import chunks


class Retrieval:
    def __init__(self, catalog, pgvector=False):
        self.catalog = catalog
        self.pgvector = pgvector and catalog.engine.dialect.name == "postgresql"

    @staticmethod
    def table(project, app, job):
        return "d3_vec_" + hashlib.sha256((project + "\0" + app + "\0" + job).encode()).hexdigest()[:32]

    def initialize_native(self, project, app, job, dimensions):
        if not self.pgvector:
            return
        table = self.table(project, app, job)
        if not 1 <= dimensions <= 2000:
            raise ValueError("Embedding dimensions are outside the supported native-index range")
        with self.catalog.engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),dict(scope=table))
            if not conn.execute(text("SELECT 1 FROM pg_extension WHERE extname='vector'")).scalar():
                raise ValueError("pgvector is not installed; provision CREATE EXTENSION vector as the database owner")
            conn.exec_driver_sql(f'CREATE TABLE IF NOT EXISTS "{table}" (id varchar(100) PRIMARY KEY, embedding vector({int(dimensions)}) NOT NULL)')

    def put_native(self, conn, project, app, job, id_, vector):
        if self.pgvector:
            table = self.table(project, app, job)
            conn.execute(text(f'INSERT INTO "{table}" (id,embedding) VALUES (:id,CAST(:v AS vector)) ON CONFLICT(id) DO UPDATE SET embedding=EXCLUDED.embedding'),
                         dict(id=id_, v=json.dumps(vector)))

    def build_native(self, project, app, job):
        if self.pgvector:
            table = self.table(project, app, job)
            with self.catalog.engine.begin() as conn:
                if conn.execute(text("SELECT to_regclass(:table)"),dict(table=table)).scalar():
                    conn.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS "{table}_hnsw" ON "{table}" USING hnsw (embedding vector_cosine_ops)')

    def vectors(self, project, app, job, query, limit=150):
        if not query or any(not math.isfinite(float(v)) for v in query):
            raise ValueError("Embedding query contains invalid values")
        if self.pgvector:
            table = self.table(project, app, job)
            with self.catalog.engine.connect() as conn:
                if not conn.execute(text("SELECT to_regclass(:table)"),dict(table=table)).scalar():
                    return []
                conn.execute(text("SET LOCAL statement_timeout='10s'"))
                return list(conn.execute(text(f'SELECT id FROM "{table}" ORDER BY embedding <=> CAST(:q AS vector) LIMIT :n'),
                                         dict(q=json.dumps(query), n=limit)).scalars())
        # Local compatibility fallback has bounded memory, not bounded runtime.
        # Production shared deployment uses native indexes, never this O(N) path.
        norm = math.sqrt(sum(float(v)**2 for v in query)) or 1
        best = []
        with self.catalog.engine.connect() as conn:
            stream = conn.execution_options(stream_results=True, yield_per=256).execute(select(chunks.c.id, chunks.c.vector).where(
                self.catalog.scope(chunks, project, app, job), chunks.c.vector.is_not(None)))
            for id_, raw in stream:
                vector = json.loads(raw)
                if len(vector) != len(query):
                    raise ValueError("Index/model vector dimensions disagree; rebuild this analysis")
                score = sum(float(a)*float(b) for a,b in zip(query,vector))/(norm*(math.sqrt(sum(float(v)**2 for v in vector)) or 1))
                item = (score, id_)
                if len(best) < limit:
                    heapq.heappush(best, item)
                elif item > best[0]:
                    heapq.heapreplace(best, item)
        return [id_ for _,id_ in sorted(best, reverse=True)]
