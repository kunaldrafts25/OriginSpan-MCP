"""Application-scoped discovery engine shared by CLI, MCP and viewer."""
from __future__ import annotations

from .compat import getenv
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading

from .corpus import build_corpus, tokens, normalize
from .embeddings import LocalEmbeddings, PROFILE
from .sources import checked_path, copy_folder, extract_zip, public_github, redact
from .store import Store, uid, now
from .structural import scan_application


@dataclass(frozen=True)
class Actor:
    subject: str
    projects: frozenset[str]
    scopes: frozenset[str] = frozenset({"read", "analyze", "propose"})
    applications: frozenset[str] | None = None

    def require(self, project, scope, app=None):
        if not isinstance(project, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", project):
            raise PermissionError("Invalid project identifier")
        if project not in self.projects or scope not in self.scopes or (app and self.applications is not None and app not in self.applications):
            raise PermissionError("Operation denied for this project/application")


@dataclass
class Config:
    state: Path
    roots: tuple[Path, ...] = ()
    database_url: str | None = None
    model_path: str | None = None
    shared: bool = False
    allow_github: bool = False

    @classmethod
    def load(cls, shared=False):
        from .compat import default_state
        state = Path(getenv("DIAMOND_STATE", str(default_state(Path.cwd())))).resolve()
        roots = tuple(Path(p).resolve() for p in getenv("DIAMOND_TRUSTED_ROOTS", "").split(os.pathsep) if p)
        return cls(state, roots, getenv("DIAMOND_DATABASE_URL"), getenv("DIAMOND_MODEL_PATH"),
                   shared, getenv("DIAMOND_ALLOW_GITHUB", "false").lower() == "true")


class Cancelled(Exception):
    pass


class Engine:
    def __init__(self, config: Config, embeddings=None):
        self.config = config
        config.state.mkdir(parents=True, exist_ok=True)
        self.store = Store(config.database_url or "sqlite:///" + (config.state / "state.sqlite").as_posix())
        approved_model = config.state / "models" / "multilingual-e5-small"
        model_path = config.model_path or (str(approved_model) if (approved_model / "diamond-model.json").is_file() else None)
        if getenv("DIAMOND_NEURAL_ENABLED","true").lower()=="false":
            model_path=None
        self.embeddings = embeddings or (LocalEmbeddings(model_path) if model_path else None)
        self._threads = {}
        self._locks = {}
        self._lock = threading.RLock()

    def snapshot_path(self, app, analysis):
        # Analysis UUIDs are global; a compact path avoids Windows MAX_PATH
        # failures on deep JVM package names. Authorization precedes every read.
        return self.config.state / "s" / analysis

    def app(self, actor, project, app, scope="read"):
        actor.require(project, scope, app)
        return self.store.get(project, app, "application", app)

    def register(self, actor, project, name, path=None, *, snippets=False):
        actor.require(project, "analyze")
        if not name.strip() or len(name) > 120:
            raise ValueError("Application name must contain 1–120 characters")
        if self.config.shared and path:
            raise PermissionError("Shared servers accept uploads or public GitHub imports, never client paths")
        source = checked_path(path, self.config.roots) if path else None
        if source and (self.config.state.is_relative_to(source) or source.is_relative_to(self.config.state)):
            raise ValueError("Source and state directories must be separate")
        app = uid()
        value = dict(id=app, name=redact(name), source=str(source) if source else None, snippets=snippets,
                     created_at=now(), owner=actor.subject, current_analysis=None)
        self.store.put(project, app, "application", app, value)
        self.store.log(project, app, actor.subject, "register")
        return self.application_public(value)

    @staticmethod
    def application_public(app):
        return {k: v for k, v in app.items() if k not in {"source"}}

    def applications(self, actor, project):
        actor.require(project, "read")
        from .store import objects
        from sqlalchemy import select
        with self.store.engine.connect() as conn:
            rows = conn.execute(select(objects.c.payload).where(objects.c.project == project, objects.c.kind == "application")).scalars()
            return [self.application_public(a) for row in rows if (a := json.loads(row)) and
                    (actor.applications is None or a["id"] in actor.applications)]

    def import_archive(self, actor, project, app, body, provenance="uploaded ZIP"):
        application = self.app(actor, project, app, "analyze")
        target = self.config.state / "imports" / uid()
        extract_zip(body, target)
        application.update(source=str(target), source_provenance=provenance)
        self.store.put(project, app, "application", app, application)
        self.store.log(project, app, actor.subject, "import_archive")
        return dict(application_id=app, imported=True, provenance=provenance)

    def import_github(self, actor, project, app, repository, revision):
        self.app(actor, project, app, "analyze")
        if not self.config.allow_github:
            raise PermissionError("Public GitHub import is disabled by operator policy")
        return self.import_archive(actor, project, app, public_github(repository, revision), f"github:{repository}@{revision}")

    def set_disclosure(self, actor, project, app, enabled):
        application = self.app(actor, project, app, "review")
        application["snippets"] = bool(enabled)
        self.store.put(project, app, "application", app, application)
        self.store.log(project, app, actor.subject, "disclosure_changed")
        return self.application_public(application)

    def start(self, actor, project, app, *, background=True):
        application = self.app(actor, project, app, "analyze")
        if not application.get("source"):
            raise ValueError("Register a local source or import a snapshot first")
        with self._lock:
            # One active job per application in this process; immutable IDs prevent
            # concurrent workers from overwriting one another's evidence.
            if any(j["status"] in {"QUEUED", "RUNNING", "CANCELLING"} for j in self.store.list(project, app, "analysis")):
                raise ValueError("An analysis is already active; cancel or finish it first")
            analysis = uid()
            job = dict(id=analysis, application_id=app, status="QUEUED", created_at=now(),
                       progress="Waiting to snapshot source", cancel_requested=False)
            self.store.put(project, app, "analysis", analysis, job)
        self.store.log(project, app, actor.subject, "analysis_started")
        if background:
            thread = threading.Thread(target=self._run, args=(project, app, analysis, application), daemon=True)
            self._threads[analysis] = thread
            thread.start()
        else:
            self._run(project, app, analysis, application)
        return self.job(actor, project, app, analysis)

    def job(self, actor, project, app, analysis):
        self.app(actor, project, app)
        return self.store.get(project, app, "analysis", analysis)

    def cancel(self, actor, project, app, analysis):
        self.app(actor, project, app, "analyze")
        with self._lock:
            job = self.store.get(project, app, "analysis", analysis)
            if job["status"] in {"QUEUED", "RUNNING", "CANCELLING"}:
                job.update(cancel_requested=True, status="CANCELLING")
                self.store.put(project, app, "analysis", analysis, job)
        return job

    def _run(self, project, app, analysis, application):
        def check():
            if self.store.get(project, app, "analysis", analysis).get("cancel_requested"):
                raise Cancelled()

        def progress(message):
            with self._lock:
                job = self.store.get(project, app, "analysis", analysis)
                job.update(status="RUNNING", progress=message)
                self.store.put(project, app, "analysis", analysis, job)
            check()

        try:
            progress("Creating content-addressed snapshot")
            source = Path(application["source"])
            roots = self.config.roots + (self.config.state / "imports",)
            checked_path(source, roots)
            snapshot = self.snapshot_path(app, analysis)
            snapshot_info = copy_folder(source, snapshot, check)
            progress("Extracting structural evidence")
            structural = scan_application(snapshot, trusted_root=self.config.state / "s", check_cancel=check)
            progress("Indexing schema, contract and document evidence")
            items, edges, gaps = build_corpus(snapshot, structural, check)
            for item in items:
                item.update(application_id=app, analysis_id=analysis, snapshot=snapshot_info["content_sha256"])
            neural = dict(available=False, reason="No approved local embedding model configured", profile=None)
            if self.embeddings:
                progress("Building local neural index")
                # Content-keyed reuse is local to the same authorized application.
                cache = {}
                previous = application.get("current_analysis")
                if previous:
                    try:
                        prior = self.store.get(project, app, "vectors", previous)
                        if prior["profile"] == self.embeddings.profile:
                            prior_items = {i["id"]: i for i in self.store.findings(project, app, previous)}
                            cache = {hashlib.sha256(prior_items[id_]["search_text"].encode()).hexdigest(): vector
                                     for id_, vector in zip(prior["ids"], prior["values"]) if id_ in prior_items}
                    except ValueError:
                        pass
                keys = [hashlib.sha256(i["search_text"].encode()).hexdigest() for i in items]
                missing = {k: item["search_text"] for k, item in zip(keys, items) if k not in cache}
                pending = list(missing)
                for start in range(0, len(pending), 32):
                    check()
                    batch = pending[start:start + 32]
                    encoded = self.embeddings.encode([missing[k] for k in batch])
                    cache.update(zip(batch, encoded))
                vectors = [cache[k] for k in keys]
                self.store.put(project, app, "vectors", analysis,
                    dict(ids=[i["id"] for i in items], values=vectors, profile=self.embeddings.profile))
                neural = dict(available=True, reason=None, profile=self.embeddings.profile,
                              embedded_chunks=len(pending), reused_chunks=len(items) - sum(k in missing for k in keys))
            check()
            self.store.add_evidence(project, app, analysis, items)
            self.store.put(project, app, "graph", analysis, {"edges": edges})
            # Structural evidence is retained internally; disclosure is handled by
            # narrow read methods, never by returning this whole payload to a tool.
            self.store.put(project, app, "structural", analysis, structural)
            with self._lock:
                check()
                job = self.store.get(project, app, "analysis", analysis)
                job.update(status="COMPLETED", progress="Ready", finished_at=now(), snapshot=snapshot_info,
                           evidence_count=len(items), neural=neural, limitations=gaps, index_version="diamond-1")
                self.store.put(project, app, "analysis", analysis, job)
                current = self.store.get(project, app, "application", app)
                current["current_analysis"] = analysis
                self.store.put(project, app, "application", app, current)
                # A new snapshot never silently revalidates old interpretations.
                for suggestion in self.store.list(project, app, "suggestion"):
                    if suggestion["analysis_id"] != analysis and suggestion["status"] == "ACCEPTED":
                        suggestion["status"] = "NEEDS_REVALIDATION"
                        self.store.put(project, app, "suggestion", suggestion["id"], suggestion)
        except Exception as exc:
            with self._lock:
                job = self.store.get(project, app, "analysis", analysis)
                job.update(status="CANCELLED" if isinstance(exc, Cancelled) else "FAILED", finished_at=now(),
                           error=None if isinstance(exc, Cancelled) else type(exc).__name__ + ": " + redact(str(exc))[:500])
                self.store.put(project, app, "analysis", analysis, job)

    def recover_jobs(self, actor, project, app):
        """Explicit operator recovery after a stopped worker; no hidden restart scans."""
        self.app(actor, project, app, "analyze")
        for job in self.store.list(project, app, "analysis"):
            if job["status"] in {"RUNNING", "QUEUED", "CANCELLING"}:
                job.update(status="INTERRUPTED", finished_at=now(), error="Worker stopped; start a new immutable analysis")
                self.store.put(project, app, "analysis", job["id"], job)

    def ready(self, actor, project, app, analysis):
        job = self.job(actor, project, app, analysis)
        if job["status"] != "COMPLETED":
            raise ValueError("Analysis is not completed")
        return job

    def public_item(self, actor, project, app, item, excerpts=False):
        application = self.app(actor, project, app)
        # Definitions are approved metadata. Raw code excerpts are opt-in.
        data = {k: v for k, v in item.items() if k not in {"search_text", "vector"}}
        if excerpts:
            if not application["snippets"]:
                raise PermissionError("Source excerpts are disabled for this application")
            root = self.snapshot_path(app, item["analysis_id"])
            path = (root / item["file"]).resolve(strict=True)
            if not path.is_relative_to(root.resolve()):
                raise ValueError("Invalid evidence path")
            lines = path.read_text(encoding="utf-8-sig").splitlines()
            start = max(0, item["line"] - 2)
            data["excerpt"] = redact("\n".join(lines[start:min(start + 12, len(lines))]))[:3000]
        return data

    def search(self, actor, project, app, analysis, query, mode="hybrid", limit=10, offset=0, excerpts=False):
        job = self.ready(actor, project, app, analysis)
        if mode not in {"exact", "lexical", "semantic", "hybrid"} or not query.strip() or len(query) > 2000:
            raise ValueError("Invalid query or search mode")
        if not 1 <= limit <= 50 or not 0 <= offset <= 10000:
            raise ValueError("Invalid pagination")
        # All channels load only this authorized project's application snapshot.
        items = self.store.findings(project, app, analysis)
        lookup = {item["id"]: item for item in items}
        ranks = {}
        normalized = normalize(query)
        if mode in {"exact", "hybrid"}:
            ranks["exact_alias"] = [i["id"] for i in items if normalize(i["name"]) == normalized]
        if mode in {"lexical", "hybrid"}:
            ranks["full_text"] = self.store.lexical(project, app, analysis, tokens(query))
        # Reviewed vocabulary augments query meaning; hypotheses cannot influence
        # observed-fact searches until a human accepts them.
        if mode == "hybrid":
            reviewed = self.store.list(project, app, "suggestion")
            ranks["reviewed_vocabulary"] = list(dict.fromkeys(ref for s in reviewed if s["status"] == "ACCEPTED"
                and s["analysis_id"] == analysis and set(tokens(query)) & set(tokens(s["meaning"])) for ref in s["evidence_ids"] if ref in lookup))
        neural = job["neural"]
        if mode == "semantic" and (not neural["available"] or not self.embeddings):
            return dict(summary="Neural retrieval unavailable; no provider was called", findings=[], total=0,
                        limitations=[neural.get("reason") or "Server has no configured embedding adapter"], neural=neural, next_offset=None)
        if mode in {"semantic", "hybrid"} and neural["available"] and self.embeddings:
            vectors = self.store.get(project, app, "vectors", analysis)
            if vectors["profile"] != self.embeddings.profile:
                raise ValueError("Embedding profile differs from index; rebuild analysis")
            q = self.embeddings.encode([query], query=True)[0]
            score = lambda v: sum(a*b for a,b in zip(q,v)) / max(1e-12, math.sqrt(sum(a*a for a in q)*sum(b*b for b in v)))
            ranks["neural"] = [id_ for id_, _ in sorted(zip(vectors["ids"], vectors["values"]), key=lambda p: score(p[1]), reverse=True)[:100]]
        scores, provenance = {}, {}
        for channel, ids in ranks.items():
            for rank, id_ in enumerate(ids, 1):
                scores[id_] = scores.get(id_, 0) + 1 / (60 + rank)
                provenance.setdefault(id_, []).append(dict(channel=channel, rank=rank))
        ordered = sorted(scores, key=lambda id_: (-scores[id_], id_))
        selected = ordered[offset:offset + limit]
        results = [{**self.public_item(actor, project, app, lookup[id_], excerpts), "retrieval": provenance[id_],
                    "rrf_score": scores[id_]} for id_ in selected]
        limitations = ([] if neural["available"] else ["Neural search unavailable; using explicit lexical/alias/graph evidence"])
        return dict(summary=f"{len(ordered)} matching evidence items", findings=results, total=len(ordered),
                    next_offset=offset + limit if offset + limit < len(ordered) else None,
                    limitations=limitations, neural=neural, resource=f"originspan://{project}/{app}/{analysis}/report")

    def context(self, actor, project, app, analysis, question, limit=12):
        result = self.search(actor, project, app, analysis, question, limit=min(limit, 25))
        facts = result["findings"]
        ids = {i["id"] for i in facts}
        graph = self.store.get(project, app, "graph", analysis)
        neighbors = set()
        for edge in graph["edges"]:
            if edge["source"] in ids:
                neighbors.add(edge["target"])
            if edge["target"] in ids:
                neighbors.add(edge["source"])
        related = [self.public_item(actor, project, app, i) for i in self.store.findings(project, app, analysis)
                   if i["id"] in neighbors - ids][:max(0, 25 - len(facts))]
        job = self.job(actor, project, app, analysis)
        return dict(summary="Evidence for host-AI interpretation; no business classification has been asserted",
                    evidence=facts, related=related, suggestions=self.suggestions(actor, project, app, analysis),
                    interpretation_status="UNRESOLVED", alternatives=[],
                    next_steps=["Compare competing meanings against entities, contracts and dictionary definitions",
                                "Search missing context or record an unresolved suggestion with explicit gaps",
                                "Check citations before submitting a proposal for human review"],
                    limitations=result["limitations"] + job["limitations"][:30],
                    coverage_gaps=["Runtime values and live database records are outside scope", "A valid citation does not prove business correctness"])

    def evidence_item(self, actor, project, app, analysis, evidence_id, excerpts=False):
        self.ready(actor, project, app, analysis)
        item = next((i for i in self.store.findings(project, app, analysis) if i["id"] == evidence_id), None)
        if item is None:
            raise ValueError("Evidence does not exist in this snapshot")
        return self.public_item(actor, project, app, item, excerpts)

    def sql_lineage(self, actor, project, app, analysis, sql, dialect="postgres"):
        from .sql_intelligence import analyze_sql
        self.ready(actor, project, app, analysis)
        structure = self.store.get(project, app, "structural", analysis)
        schema = {}
        for query in structure["sql"]:
            for field in query.get("schema_fields", []):
                schema.setdefault(field["table"], {})[field["name"]] = field.get("data_type") or "UNKNOWN"
        return analyze_sql(sql, dialect, schema=schema)

    def validate(self, actor, project, app, analysis, suggestions):
        self.ready(actor, project, app, analysis)
        if not isinstance(suggestions, list) or not 1 <= len(suggestions) <= 50:
            raise ValueError("Provide 1–50 suggestions")
        ids = {i["id"] for i in self.store.findings(project, app, analysis)}
        current = self.app(actor, project, app)["current_analysis"]
        results = []
        existing = self.suggestions(actor, project, app, analysis)
        for suggestion in suggestions:
            errors = []
            if not isinstance(suggestion, dict):
                raise ValueError("Suggestion must be an object")
            allowed = {"subject", "meaning", "evidence_ids", "alternatives", "gaps", "confidence", "model", "client", "unresolved"}
            if set(suggestion) - allowed:
                errors.append("Unknown or privileged suggestion fields")
            if not isinstance(suggestion.get("subject"), str) or not isinstance(suggestion.get("meaning"), str):
                errors.append("Subject and meaning are required strings")
            references = suggestion.get("evidence_ids", [])
            if not isinstance(references, list) or not references or any(not isinstance(r, str) or r not in ids for r in references):
                errors.append("Nonexistent, foreign or missing evidence references")
            if current != analysis:
                errors.append("Snapshot is stale; use the current analysis")
            if len(json.dumps(suggestion)) > 15000:
                errors.append("Suggestion exceeds size limit")
            conflicts = [s["id"] for s in existing if normalize(str(suggestion.get("subject", ""))) == normalize(s["subject"])
                         and normalize(str(suggestion.get("meaning", ""))) != normalize(s["meaning"]) and s["status"] != "REJECTED"]
            results.append(dict(valid=not errors, errors=errors, conflicting_suggestion_ids=conflicts,
                                business_correctness="NOT_ESTABLISHED"))
        return dict(valid=all(r["valid"] for r in results), results=results,
                    limitations=["Only references, scope, freshness and structural constraints are validated"])

    def propose(self, actor, project, app, analysis, suggestions):
        self.app(actor, project, app, "propose")
        validation = self.validate(actor, project, app, analysis, suggestions)
        if not validation["valid"]:
            raise ValueError(json.dumps(validation))
        ids = []
        for suggestion in suggestions:
            id_ = uid()
            value = {**suggestion, "id": id_, "analysis_id": analysis, "status": "PROPOSED",
                     "kind": "ai_hypothesis", "actor": actor.subject, "created_at": now(), "history": []}
            # Store proposal text after secret filtering; never run it as a prompt or command.
            value["meaning"] = redact(value["meaning"])
            self.store.put(project, app, "suggestion", id_, value)
            ids.append(id_)
        self.store.log(project, app, actor.subject, "suggestions_recorded")
        return dict(ids=ids, status="PROPOSED", validation=validation)

    def suggestions(self, actor, project, app, analysis):
        self.app(actor, project, app)
        return [s for s in self.store.list(project, app, "suggestion") if s["analysis_id"] == analysis][:100]

    def review(self, actor, project, app, suggestion_id, decision, correction=None):
        self.app(actor, project, app, "review")
        if decision not in {"ACCEPTED", "REJECTED"}:
            raise ValueError("Invalid review decision")
        value = self.store.get(project, app, "suggestion", suggestion_id)
        # A proposal's own identity cannot approve its business interpretation.
        if value["actor"] == actor.subject:
            raise PermissionError("A different human reviewer must review this proposal")
        if value["analysis_id"] != self.app(actor, project, app)["current_analysis"]:
            raise ValueError("Stale suggestion requires a new proposal against current evidence")
        history = dict(actor=actor.subject, at=now(), decision=decision, previous_meaning=value["meaning"])
        value["history"].append(history)
        value["status"] = decision
        if correction:
            value["meaning"] = redact(correction)[:6000]
        value["kind"] = "reviewed_knowledge" if decision == "ACCEPTED" else "ai_hypothesis"
        self.store.put(project, app, "suggestion", suggestion_id, value)
        self.store.log(project, app, actor.subject, "suggestion_" + decision.lower())
        return value

    def graph(self, actor, project, app, analysis, focus=None, limit=100, offset=0):
        self.ready(actor, project, app, analysis)
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("Invalid graph pagination")
        items = self.store.findings(project, app, analysis)
        edges = self.store.get(project, app, "graph", analysis)["edges"]
        if focus:
            neighbors = {focus}
            for edge in edges:
                if edge["source"] == focus or edge["target"] == focus:
                    neighbors.update([edge["source"], edge["target"]])
            items = [i for i in items if i["id"] in neighbors]
        chosen = items[offset:offset + limit]
        ids = {i["id"] for i in chosen}
        return dict(nodes=[self.public_item(actor, project, app, i) for i in chosen],
                    edges=[e for e in edges if e["source"] in ids and e["target"] in ids], total=len(items),
                    next_offset=offset + limit if offset + limit < len(items) else None,
                    limitations=["Static relationships; co-location does not imply a runtime call"])

    def report(self, actor, project, app, analysis):
        job = self.ready(actor, project, app, analysis)
        suggestions = self.suggestions(actor, project, app, analysis)
        structural = self.store.get(project, app, "structural", analysis)
        return dict(application_id=app, analysis_id=analysis, summary=f"{job['evidence_count']} evidence items indexed",
            observed_facts=dict(files=len(structural["files"]), components=len(structural["components"]),
                                endpoints=sum(n["type"] == "API_ENDPOINT" for n in structural["nodes"])),
            hypotheses=[s for s in suggestions if s["status"] == "PROPOSED"],
            reviewed_knowledge=[s for s in suggestions if s["status"] == "ACCEPTED"],
            unresolved=[s for s in suggestions if s.get("unresolved")], limitations=job["limitations"], neural=job["neural"])

    def impact(self, actor, project, app, analysis, evidence_id, depth=2):
        self.ready(actor, project, app, analysis)
        if not 1 <= depth <= 4:
            raise ValueError("Depth must be 1–4")
        items = self.store.findings(project, app, analysis)
        lookup = {i["id"]: i for i in items}
        if evidence_id not in lookup:
            raise ValueError("Evidence does not exist in this snapshot")
        edges = self.store.get(project, app, "graph", analysis)["edges"]
        visited, frontier = {evidence_id}, {evidence_id}
        for _ in range(depth):
            neighbors = set()
            for e in edges:
                if e["source"] in frontier or e["target"] in frontier:
                    neighbors.update([e["source"], e["target"]])
            frontier = set(sorted(neighbors - visited)[:max(0, 200 - len(visited))])
            visited.update(frontier)
            if not frontier or len(visited) >= 200:
                break
        return dict(nodes=[self.public_item(actor, project, app, lookup[id_]) for id_ in sorted(visited)],
                    edges=[e for e in edges if e["source"] in visited and e["target"] in visited],
                    truncated=len(visited) >= 200, limitations=["Bounded static reachability; co-location is context, not a proven runtime dependency"])
