"""Production discovery service over normalized catalog and durable workers.

Legacy 0.2 analyses remain readable. New analyses use streaming content capture,
per-file cache, fenced leases and database-side evidence selection by default.
"""
from __future__ import annotations
from .compat import getenv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
import uuid
from sqlalchemy import select, update, func, or_, and_

from .engine import Engine as LegacyEngine, Config, Actor
from .catalog import Catalog, jobs, tasks, files, chunks, units, edges,interpretations
from .pipeline import Pipeline,EXTRACTION_VERSION
from .retrieval import Retrieval
from .corpus import normalize, tokens
from .sources import redact


class DiscoveryService(LegacyEngine):
    def __init__(self, config, embeddings=None, *, migrate=None):
        super().__init__(config, embeddings)
        self.catalog = Catalog(self.store.engine, migrate=not config.shared if migrate is None else migrate)
        self.native_vectors = getenv("DIAMOND_PGVECTOR", "false").lower() == "true"
        self.retrieval = Retrieval(self.catalog, self.native_vectors)
        self.pipeline = Pipeline(self.catalog, config, self.embeddings)
        self.pipeline.retrieval = self.retrieval
        self._background = set()
        self._semantic_warming=False
        self._semantic_error=None

    def warm_semantic(self):
        from .embeddings import LocalEmbeddings
        if not isinstance(self.embeddings,LocalEmbeddings) or self.embeddings.ready or self._semantic_warming:
            return
        self._semantic_warming=True
        def warm():
            try:
                self.embeddings._load()
            except Exception as exc:
                self._semantic_error=type(exc).__name__
            finally:
                self._semantic_warming=False
        threading.Thread(target=warm,name="diamond-local-model-warmup",daemon=True).start()

    def is_new(self, project, app, analysis):
        with self.store.engine.connect() as conn:
            return bool(conn.execute(select(jobs.c.id).where(jobs.c.project == project, jobs.c.app == app, jobs.c.id == analysis)).first())

    def start(self, actor, project, app, *, background=True):
        application = self.app(actor, project, app, "analyze")
        if not application.get("source"):
            raise ValueError("Register a source or upload an approved snapshot first")
        payload = dict(actor=actor.subject, created_at=datetime.now(timezone.utc).isoformat(), index_version=EXTRACTION_VERSION,
                       neural_profile=self.embeddings.profile if self.embeddings else None, source_provenance=application.get("source_provenance"))
        id_ = self.catalog.create_job(project, app, application["source"], payload)
        self.store.log(project, app, actor.subject, "analysis_queued")
        # Shared API defaults to external workers. Local stdio can run a worker
        # alongside the transport, but durable tasks survive either process.
        embedded = not self.config.shared and getenv("DIAMOND_EXTERNAL_WORKERS", "false").lower() != "true"
        if background and embedded and not getattr(self,"_local_worker",None):
            worker = threading.Thread(target=self.pipeline.run, args=("embedded-"+uuid.uuid4().hex, id_), daemon=True)
            self._background.add(worker)
            worker.start()
        elif not background:
            self.pipeline.run("cli-"+uuid.uuid4().hex, id_)
        return self.job(actor, project, app, id_)

    def job(self, actor, project, app, analysis):
        self.app(actor, project, app)
        if not self.is_new(project, app, analysis):
            return super().job(actor, project, app, analysis)
        row = self.catalog.job(project, app, analysis)
        return dict(id=analysis, application_id=app, status=row["state"], created_at=row["payload"]["created_at"],
            progress="Inventory and extraction are durable leased tasks", tasks=row["tasks"],
            snapshot=dict(files=row["selected_files"], inventoried_files=row["file_count"], bytes=row["source_bytes"], consistency="per-file content snapshot; filesystem traversal is not a transactional VCS checkout"),
            evidence_count=self.catalog.count_chunks(project, app, analysis), index_version=row["payload"].get("index_version"),
            neural=dict(available=row["payload"].get("neural_profile") is not None, profile=row["payload"].get("neural_profile"),
                        runtime_ready=not self._semantic_warming and not self._semantic_error,
                        runtime_status="WARMING" if self._semantic_warming else "UNAVAILABLE" if self._semantic_error else "READY",
                        backend="pgvector-hnsw" if self.retrieval.pgvector else "streaming-local-exact"),
            limitations=["No runtime records or application code executed", "Failed/oversized artifacts are visible in the coverage report"],
            error="Inspect dead letters and coverage" if row["state"] in {"FAILED","PARTIAL"} else None)

    def list_analyses(self, actor, project, app, limit=20, offset=0):
        self.app(actor, project, app)
        if not 1 <= limit <= 50 or offset < 0:
            raise ValueError("Invalid pagination")
        with self.store.engine.connect() as conn:
            ids = list(conn.execute(select(jobs.c.id).where(jobs.c.project == project, jobs.c.app == app)
                                   .order_by(jobs.c.created.desc()).limit(limit).offset(offset)).scalars())
            total = conn.execute(select(func.count()).select_from(jobs).where(jobs.c.project == project, jobs.c.app == app)).scalar()
        return dict(analyses=[self.job(actor,project,app,id_) for id_ in ids], total=total,
                    next_offset=offset+limit if offset+limit<total else None)

    def ensure_local_worker(self):
        if self.config.shared or getenv("DIAMOND_EXTERNAL_WORKERS","false").lower()=="true":
            return
        if getattr(self,"_local_worker",None):
            return
        self._worker_stop=threading.Event()
        self._local_worker=threading.Thread(target=self.pipeline.run,args=("local-"+uuid.uuid4().hex,None,self._worker_stop),daemon=True)
        self._local_worker.start()

    def stop_workers(self):
        if getattr(self,"_worker_stop",None):
            self._worker_stop.set()
            self._local_worker.join(5)

    def cancel(self, actor, project, app, analysis):
        self.app(actor, project, app, "analyze")
        if not self.is_new(project, app, analysis):
            return super().cancel(actor, project, app, analysis)
        with self.store.engine.begin() as conn:
            conn.execute(update(jobs).where(jobs.c.id == analysis, jobs.c.project == project, jobs.c.app == app,
                jobs.c.state.in_(["QUEUED","RUNNING"])).values(cancelled=1,state="CANCELLING"))
        self.pipeline.reconcile(analysis)
        return self.job(actor,project,app,analysis)

    def ready(self, actor, project, app, analysis):
        result = self.job(actor,project,app,analysis)
        if result["status"] not in {"COMPLETED","PARTIAL"}:
            raise ValueError("Analysis is not published; poll progress or run a discovery worker")
        return result

    def public_item(self, actor, project, app, item, excerpts=False):
        if not self.is_new(project,app,item["analysis_id"]):
            return super().public_item(actor,project,app,item,excerpts)
        application = self.app(actor,project,app)
        result = {k:v for k,v in item.items() if k not in {"search_text","vector"}}
        if excerpts:
            if not application["snippets"]:
                raise PermissionError("Source excerpts are disabled for this application")
            with self.store.engine.connect() as conn:
                digest = conn.execute(select(files.c.sha).where(files.c.project == project, files.c.app == app,
                    files.c.job == item["analysis_id"], files.c.path == item["file"])).scalar_one()
            lines = []
            start = max(1,item["line"]-1)
            with self.pipeline.blobs.path(digest).open(encoding="utf-8-sig",errors="replace") as stream:
                for number,line in enumerate(stream,1):
                    if number >= start:
                        lines.append(line[:1000])
                    if number >= start+11:
                        break
            result["excerpt"] = redact("".join(lines))[:3000]
        return result

    def evidence_item(self, actor, project, app, analysis, evidence_id, excerpts=False):
        self.ready(actor,project,app,analysis)
        if not self.is_new(project,app,analysis):
            return super().evidence_item(actor,project,app,analysis,evidence_id,excerpts)
        values = self.catalog.get_chunks(project,app,analysis,[evidence_id],limit=1)
        if not values:
            raise ValueError("Evidence does not exist in this snapshot")
        return self.public_item(actor,project,app,values[0],excerpts)

    def search(self, actor, project, app, analysis, query, mode="hybrid", limit=10, offset=0, excerpts=False):
        job = self.ready(actor,project,app,analysis)
        if not self.is_new(project,app,analysis):
            return super().search(actor,project,app,analysis,query,mode,limit,offset,excerpts)
        if mode not in {"exact","lexical","semantic","hybrid"} or not query.strip() or len(query)>2000:
            raise ValueError("Invalid query or mode")
        if not 1 <= limit <= 50 or not 0 <= offset <= 400:
            raise ValueError("Invalid result-window pagination; narrow the query beyond 400 candidates")
        channels, limitations = {}, []
        if excerpts and not self.app(actor,project,app)["snippets"]:
            excerpts = False
            limitations.append("Source excerpts are not permitted; authorized metadata results returned instead")
        if mode in {"exact","hybrid"}:
            channels["exact_alias"] = self.catalog.exact(project,app,analysis,normalize(query))
        if mode in {"lexical","hybrid"}:
            channels["full_text"] = self.catalog.lexical(project,app,analysis,tokens(query))
        neural = job["neural"]
        if mode in {"semantic","hybrid"}:
            if self._semantic_warming or self._semantic_error:
                limitations.append("Local neural model is warming; retry shortly" if self._semantic_warming else "Local model initialization failed; operator action required")
            elif self.embeddings and neural["available"]:
                if neural["profile"] != self.embeddings.profile:
                    raise ValueError("Configured model profile differs from the published index")
                channels["neural"] = self.retrieval.vectors(project,app,analysis,self.embeddings.encode([query],query=True)[0])
            else:
                limitations.append("Neural retrieval unavailable; no remote provider called")
        if mode == "hybrid":
            with self.store.engine.connect() as conn:
                vocabulary=conn.execute(select(interpretations.c.payload).where(self.catalog.scope(interpretations,project,app,analysis),
                    interpretations.c.status=="ACCEPTED",or_(*[func.lower(interpretations.c.meaning).contains(t,autoescape=True) for t in tokens(query)[:30]]))
                    .limit(100)).scalars()
                channels["reviewed_vocabulary"]=list(dict.fromkeys(r for raw in vocabulary for r in json.loads(raw)["evidence_ids"]))[:150]
        scores, provenance = {}, {}
        for channel, ids in channels.items():
            for rank,id_ in enumerate(ids,1):
                scores[id_] = scores.get(id_,0) + (1.5 if channel in {"exact_alias","reviewed_vocabulary"} else 1)/(60+rank)
                provenance.setdefault(id_,[]).append(dict(channel=channel,rank=rank))
        ordered = sorted(scores,key=lambda id_:(-scores[id_],id_))
        lookup = {i["id"]:i for i in self.catalog.get_chunks(project,app,analysis,ordered,limit=500)}
        # Round-robin file diversification preserves all bounded candidates while
        # preventing twenty near-identical fields from occupying the first page.
        if mode == "hybrid":
            groups = {}
            for id_ in ordered:
                if id_ in lookup:
                    groups.setdefault(lookup[id_]["file"],[]).append(id_)
            ordered = [value for level in range(max((len(v) for v in groups.values()),default=0))
                       for values in groups.values() for value in values[level:level+1]]
        chosen = [id_ for id_ in ordered[offset:offset+limit] if id_ in lookup]
        return dict(summary=f"{len(ordered)} ranked candidates in a bounded retrieval window", findings=[
            {**self.public_item(actor,project,app,lookup[id_],excerpts),"retrieval":provenance[id_],"rrf_score":scores[id_]} for id_ in chosen],
            total=len(ordered), total_is_candidate_window=True, next_offset=offset+limit if offset+limit<len(ordered) else None,
            limitations=limitations, neural=neural, resource=f"originspan://{project}/{app}/{analysis}/report")

    def context(self, actor, project, app, analysis, question, limit=12):
        if not self.is_new(project,app,analysis):
            return super().context(actor,project,app,analysis,question,limit)
        found = self.search(actor,project,app,analysis,question,limit=min(25,limit))
        ids = [i["id"] for i in found["findings"]]
        relations = self.catalog.neighbors(project,app,analysis,ids,limit=100)
        neighbors = list({r[k] for r in relations for k in ("source","target")} - set(ids))[:25]
        return dict(summary="Scoped evidence for customer-AI interpretation", evidence=found["findings"],
            related=[self.public_item(actor,project,app,i) for i in self.catalog.get_chunks(project,app,analysis,neighbors,limit=max(1,25-len(ids)))],
            relationships=relations, interpretation_status="UNRESOLVED", alternatives=[],
            suggestions=self.suggestions(actor,project,app,analysis), limitations=found["limitations"],
            coverage_gaps=["Business meaning is not proved by similarity", "No runtime records or protected-trait inference"],
            next_steps=["Compare dictionary, schema and call-site evidence", "Search gaps and contradictory meanings", "Validate citations and record unapproved hypotheses"])

    def graph(self, actor, project, app, analysis, focus=None, limit=100, offset=0):
        self.ready(actor,project,app,analysis)
        if not self.is_new(project,app,analysis):
            return super().graph(actor,project,app,analysis,focus,limit,offset)
        if not 1<=limit<=200 or offset<0:
            raise ValueError("Invalid pagination")
        if focus and not self.catalog.get_chunks(project,app,analysis,[focus],limit=1):
            raise ValueError("focus must be an evidence ID from search_findings, not a path; use get_architecture components with a service root for directories")
        relations = self.catalog.neighbors(project,app,analysis,[focus],limit=1000) if focus else []
        ids = list({r[k] for r in relations for k in ("source","target")} | {focus}) if focus else None
        nodes = self.catalog.get_chunks(project,app,analysis,ids,limit=limit,offset=offset)
        if not focus:
            relations = self.catalog.neighbors(project,app,analysis,[n["id"] for n in nodes],limit=1000)
        selected={n["id"] for n in nodes}
        total = len(ids) if ids is not None else self.catalog.count_chunks(project,app,analysis)
        return dict(nodes=[self.public_item(actor,project,app,n) for n in nodes],edges=[r for r in relations if r["source"] in selected and r["target"] in selected],
            total=total,next_offset=offset+limit if offset+limit<total else None,limitations=["Static/candidate relationships; expand architecture aggregates for a system overview"])

    def impact(self, actor, project, app, analysis, evidence_id, depth=2):
        self.evidence_item(actor,project,app,analysis,evidence_id)
        if not self.is_new(project,app,analysis):
            return super().impact(actor,project,app,analysis,evidence_id,depth)
        if not 1<=depth<=4:
            raise ValueError("Depth must be 1–4")
        seen,frontier,relations={evidence_id},[evidence_id],[]
        for _ in range(depth):
            page=self.catalog.neighbors(project,app,analysis,frontier,limit=500)
            relations.extend(page)
            frontier=sorted({r[k] for r in page for k in ("source","target")}-seen)[:200-len(seen)]
            seen.update(frontier)
            if not frontier:
                break
        return dict(nodes=self.catalog.get_chunks(project,app,analysis,list(seen),limit=200), edges=relations[:500],
                    truncated=len(seen)>=200,limitations=["Static bounded reachability, not guaranteed runtime impact"])

    def coverage(self, actor, project, app, analysis, limit=50, offset=0):
        self.app(actor,project,app)
        self.job(actor,project,app,analysis)
        if not 1<=limit<=100 or offset<0:
            raise ValueError("Invalid pagination")
        with self.store.engine.connect() as conn:
            rows=conn.execute(select(files.c.path,files.c.state,files.c.bytes,files.c.detail).where(self.catalog.scope(files,project,app,analysis))
                              .order_by(files.c.path).limit(limit).offset(offset)).mappings().all()
            failed=conn.execute(select(tasks.c.id,tasks.c.state,tasks.c.error).where(tasks.c.job==analysis,tasks.c.state=="DEAD").limit(50)).mappings().all()
        return dict(files=[dict(r) for r in rows],dead_letters=[dict(r) for r in failed],
            extractor_coverage=self.catalog.unit_page(project,app,analysis,"coverage",limit,offset),
            next_offset=offset+limit if len(rows)==limit else None,
            limitations=["Only supported approved artifacts are selected; excluded record/binary/secret paths are not parser failures"])

    def report(self, actor, project, app, analysis):
        job=self.ready(actor,project,app,analysis)
        if not self.is_new(project,app,analysis):
            return super().report(actor,project,app,analysis)
        suggestions=self.suggestions(actor,project,app,analysis)
        with self.store.engine.connect() as conn:
            kinds=dict(conn.execute(select(chunks.c.kind,func.count()).where(self.catalog.scope(chunks,project,app,analysis)).group_by(chunks.c.kind)).all())
        return dict(application_id=app,analysis_id=analysis,summary=f"{job['evidence_count']} indexed evidence items",job=job,
            observed_facts=dict(files=job["snapshot"]["files"],bytes=job["snapshot"]["bytes"],kinds=kinds),
            hypotheses=[s for s in suggestions if s["status"]=="PROPOSED"],reviewed_knowledge=[s for s in suggestions if s["status"]=="ACCEPTED"],
            unresolved=[s for s in suggestions if s.get("unresolved")], limitations=job["limitations"],neural=job["neural"])

    def validate(self, actor, project, app, analysis, suggestions):
        self.ready(actor,project,app,analysis)
        if not self.is_new(project,app,analysis):
            return super().validate(actor,project,app,analysis,suggestions)
        if not isinstance(suggestions,list) or not 1<=len(suggestions)<=50:
            raise ValueError("Provide 1–50 suggestions")
        references=[]
        for s in suggestions:
            if not isinstance(s,dict) or not isinstance(s.get("evidence_ids"),list) or len(s["evidence_ids"])>100:
                raise ValueError("Each suggestion requires at most 100 evidence IDs")
            references.extend(r for r in s["evidence_ids"] if isinstance(r,str))
        valid_ids=set()
        for start in range(0,len(references),500):
            valid_ids.update(i["id"] for i in self.catalog.get_chunks(project,app,analysis,references[start:start+500],limit=500))
        results=[]
        for s in suggestions:
            errors=[]
            if set(s)-{"subject","meaning","evidence_ids","alternatives","gaps","confidence","model","client","unresolved"}:
                errors.append("Unknown or privileged suggestion fields")
            if not isinstance(s.get("subject"),str) or not isinstance(s.get("meaning"),str):
                errors.append("Subject and meaning are required")
            if not s["evidence_ids"] or any(not isinstance(r,str) or r not in valid_ids for r in s["evidence_ids"]):
                errors.append("Invalid or foreign evidence references")
            if self.app(actor,project,app)["current_analysis"]!=analysis:
                errors.append("Stale analysis")
            if len(json.dumps(s))>15000:
                errors.append("Suggestion exceeds size limit")
            results.append(dict(valid=not errors,errors=errors,business_correctness="NOT_ESTABLISHED"))
        return dict(valid=all(r["valid"] for r in results),results=results,limitations=["Reference validation is not business validation"])

    def suggestions(self,actor,project,app,analysis):
        self.app(actor,project,app)
        if not self.is_new(project,app,analysis):
            return super().suggestions(actor,project,app,analysis)
        with self.store.engine.connect() as conn:
            return [json.loads(v) for v in conn.execute(select(interpretations.c.payload).where(
                self.catalog.scope(interpretations,project,app,analysis)).order_by(interpretations.c.id).limit(100)).scalars()]

    def propose(self,actor,project,app,analysis,suggestions):
        self.app(actor,project,app,"propose")
        if not self.is_new(project,app,analysis):
            return super().propose(actor,project,app,analysis,suggestions)
        validation=self.validate(actor,project,app,analysis,suggestions)
        if not validation["valid"]:
            raise ValueError(json.dumps(validation))
        from .store import objects,now
        ids=[]
        with self.store.engine.begin() as conn:
            for suggestion in suggestions:
                id_=uuid.uuid4().hex
                value={**suggestion,"id":id_,"analysis_id":analysis,"status":"PROPOSED","kind":"ai_hypothesis",
                    "actor":actor.subject,"created_at":now(),"history":[]}
                value["meaning"]=redact(value["meaning"])
                self.catalog.upsert(conn,objects,dict(project=project,application=app,kind="suggestion",id=id_,payload=json.dumps(value)),
                    ["project","application","kind","id"])
                self.catalog.upsert(conn,interpretations,dict(project=project,app=app,job=analysis,id=id_,status="PROPOSED",
                    subject_alias=normalize(value["subject"])[:600],meaning=value["meaning"],payload=json.dumps(value)),["project","app","job","id"])
                ids.append(id_)
        self.store.log(project,app,actor.subject,"suggestions_recorded")
        return dict(ids=ids,status="PROPOSED",validation=validation)

    def review(self,actor,project,app,suggestion_id,decision,correction=None):
        self.app(actor,project,app,"review")
        value=self.store.get(project,app,"suggestion",suggestion_id)
        if not self.is_new(project,app,value["analysis_id"]):
            return super().review(actor,project,app,suggestion_id,decision,correction)
        if actor.subject==value["actor"]:
            raise PermissionError("A separate human identity must review this proposal")
        if decision not in {"ACCEPTED","REJECTED"}:
            raise ValueError("Invalid decision")
        if self.app(actor,project,app)["current_analysis"]!=value["analysis_id"]:
            raise ValueError("Stale evidence requires a new proposal")
        from .store import objects,now
        with self.store.engine.begin() as conn:
            if self.store.engine.dialect.name=="sqlite":
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            application_query=select(objects.c.payload).where(objects.c.project==project,objects.c.application==app,objects.c.kind=="application",objects.c.id==app)
            proposal_query=select(objects.c.payload).where(objects.c.project==project,objects.c.application==app,objects.c.kind=="suggestion",objects.c.id==suggestion_id)
            if self.store.engine.dialect.name=="postgresql":
                application_query=application_query.with_for_update()
                proposal_query=proposal_query.with_for_update()
            application=json.loads(conn.execute(application_query).scalar_one())
            value=json.loads(conn.execute(proposal_query).scalar_one())
            if application["current_analysis"]!=value["analysis_id"]:
                raise ValueError("Stale evidence requires a new proposal")
            value["history"].append(dict(actor=actor.subject,at=now(),decision=decision,previous_meaning=value["meaning"]))
            value["status"]=decision
            value["kind"]="reviewed_knowledge" if decision=="ACCEPTED" else "ai_hypothesis"
            if correction:value["meaning"]=redact(correction)[:6000]
            conn.execute(update(objects).where(objects.c.project==project,objects.c.application==app,objects.c.kind=="suggestion",objects.c.id==suggestion_id).values(payload=json.dumps(value)))
            conn.execute(update(interpretations).where(self.catalog.scope(interpretations,project,app,value["analysis_id"]),
                interpretations.c.id==suggestion_id).values(status=decision,meaning=value["meaning"],payload=json.dumps(value)))
        self.store.log(project,app,actor.subject,"suggestion_"+decision.lower())
        return value

    def recover_jobs(self, actor, project, app):
        self.app(actor,project,app,"analyze")
        super().recover_jobs(actor,project,app)
        # Durable expired leases are reclaimed automatically. Do not fail active
        # jobs simply because a different CLI process cannot see their threads.
        return self.list_analyses(actor,project,app)

    def chat_report(self, actor, project, app, analysis, topic="overview", limit=12, offset=0, question=""):
        from .chat_discovery import report
        return report(self,actor,project,app,analysis,topic,limit,offset,question)

    def discover_application(self, actor, project, name, topic="overview", limit=12, offset=0, question=""):
        from .chat_discovery import select_application
        app,analysis=select_application(self,actor,project,name)
        return self.chat_report(actor,project,app["id"],analysis,topic,limit,offset,question)

    def complete_discovery(self, actor, project, app, analysis, section_limit=8):
        from .discovery_dossier import complete
        return complete(self,actor,project,app,analysis,section_limit)

    def discovery_section(self, actor, project, app, analysis, section, limit=12, offset=0):
        from .discovery_dossier import section as read_section
        return read_section(self,actor,project,app,analysis,section,limit,offset)

    def demographic_context(self, actor, project, app, analysis, question="Discover demographic fields and their meaning"):
        self.ready(actor,project,app,analysis)
        result=self.context(actor,project,app,analysis,question+" birth age residence language nationality household demographic",limit=20)
        result["field_inventory"] = self.discovery_section(actor,project,app,analysis,"fields",limit=12)
        result["definition_context"] = self.discovery_section(actor,project,app,analysis,"documentation",limit=4)
        result["inventory_instruction"] = "Field inventory is not a demographic classification. Page all fields/definitions using get_discovery_section; follow usage candidates and preserve unknown meanings."
        result.update(domain="demographic_metadata",classification_status="HYPOTHESES_REQUIRE_REVIEW",
            prohibitions=["No individual demographic values are read", "Do not infer protected traits from names, addresses or unrelated fields"],
            interpretation_guidance=["Cite declared fields, documentation, dictionaries and consuming APIs",
                "Distinguish date of birth from account creation dates and age from record age",
                "Preserve unknown/ambiguous meanings; request missing definitions", "Use record_suggestions for cited, unapproved interpretations"])
        return result

    def sql_inventory(self, actor, project, app, analysis, limit=30, offset=0):
        self.ready(actor,project,app,analysis)
        if not 1<=limit<=100 or offset<0:
            raise ValueError("Invalid pagination")
        if not self.is_new(project,app,analysis):
            structure=self.store.get(project,app,"structural",analysis)
            values=structure["sql"][offset:offset+limit]
            return dict(queries=values,next_offset=offset+limit if len(values)==limit else None)
        values=self.catalog.unit_page(project,app,analysis,"sql",limit,offset)
        safe=[]
        orm_facts = None
        for value in values:
            if value.get("language")=="JPQL" and value.get("metadata_expression"):
                from .discovery_dossier import facts_for, resolve_jpql
                if orm_facts is None:
                    orm_facts,_ = facts_for(self,project,app,analysis,kinds={"orm_column"})
                resolved=resolve_jpql(value["metadata_expression"],orm_facts)
                if resolved:
                    value={**value,**resolved}
            analysis_=value.get("analysis",{})
            safe.append(dict(id=value["id"],file=value["file"],line=value["line"],language=value["language"],
                status=analysis_.get("status"),resolution=value.get("resolution"),mapping_evidence=value.get("mapping_evidence",[]),metadata_sql=value.get("metadata_sql"),tables=analysis_.get("tables",[]),
                columns=analysis_.get("columns",[]),schema_fields=analysis_.get("schema_fields",[]),
                value_lineage=analysis_.get("value_lineage",[]),predicate_dependencies=analysis_.get("predicate_dependencies",[]),limitations=[redact(s)[:500] for s in analysis_.get("limitations",[])]))
        return dict(queries=safe,next_offset=offset+limit if len(values)==limit else None,
            limitations=["Static query inventory; dynamic SQL remains unresolved", "Embedded literal values are not returned as customer data"])

    def sql_lineage(self, actor, project, app, analysis, sql, dialect="postgres"):
        self.ready(actor,project,app,analysis)
        if not self.is_new(project,app,analysis):
            return super().sql_lineage(actor,project,app,analysis,sql,dialect)
        from .sql_intelligence import analyze_sql
        schema={}
        declarations={}
        # Fetch only table-relevant DDL units instead of loading every schema in
        # a potentially enormous repository. SQLGlot supplies requested tables.
        initial=analyze_sql(sql,dialect)
        requested={t.casefold().split(".")[-1] for t in initial["tables"]}
        with self.store.engine.connect() as conn:
            query=select(chunks.c.payload).where(self.catalog.scope(chunks,project,app,analysis),chunks.c.kind=="declared_schema_field",func.lower(chunks.c.entity).in_(requested))
            for raw in conn.execution_options(stream_results=True,yield_per=256).execute(query).scalars():
                field=json.loads(raw)
                entity=field.get("entity","")
                if entity.casefold() in requested:
                    declarations.setdefault(entity,set()).add(field["file"])
                    schema.setdefault(entity,{})[field["name"]]=field.get("data_type") or "UNKNOWN"
                    if sum(len(v) for v in schema.values())>20000:
                        raise ValueError("Relevant schema exceeds one query's resolution budget; narrow the SQL")
        ambiguous=[entity for entity,locations in declarations.items() if len(locations)>1]
        for entity in ambiguous:
            schema.pop(entity,None)
        result=analyze_sql(sql,dialect,schema=schema)
        if ambiguous:
            result["limitations"].append("Multiple source declarations require explicit database/schema context: "+", ".join(sorted(ambiguous)))
        return result

    def source_query_lineage(self,actor,project,app,analysis,query_id,dialect="postgres"):
        import re
        self.ready(actor,project,app,analysis)
        if not re.fullmatch(r'[a-f0-9]{12,64}',query_id):
            raise ValueError('Use a query reference from list_sql_queries (at least 12 hexadecimal characters)')
        with self.store.engine.connect() as conn:
            values=[json.loads(raw) for raw in conn.execute(select(units.c.payload).where(
                self.catalog.scope(units,project,app,analysis),units.c.kind=='sql',
                units.c.id.startswith(query_id,autoescape=True)).limit(2)).scalars()]
        if len(values)!=1:
            raise ValueError('Source query reference is missing or ambiguous in this application snapshot; use the full query ID')
        query=values[0]
        sql=query.get('metadata_sql')
        if query.get('language')=='JPQL':
            from .discovery_dossier import facts_for,resolve_jpql
            facts,_=facts_for(self,project,app,analysis,kinds={'orm_column'})
            translated=resolve_jpql(query.get('metadata_expression'),facts)
            if translated:
                sql=translated['metadata_sql']
        if not sql:
            raise ValueError('This observed source query is unresolved; SQL lineage cannot be fabricated')
        result=self.sql_lineage(actor,project,app,analysis,sql,dialect)
        result['provenance']=dict(kind='observed_source_query',query_id=query['id'],file=query['file'],line=query['line'],
            source_language=query['language'],scope='Static metadata expression, not observed runtime SQL execution')
        return result

    def architecture(self, actor, project, app, analysis, level="services", service=None, limit=100, offset=0):
        self.ready(actor,project,app,analysis)
        if not self.is_new(project,app,analysis):
            return dict(summary="Legacy snapshot; use the paginated evidence graph",**super().graph(actor,project,app,analysis,limit=limit,offset=offset))
        if level not in {"system","services","components"} or not 1<=limit<=200 or offset<0:
            raise ValueError("Invalid architecture level or pagination")
        if level=="components":
            if not service:
                raise ValueError("Choose a service root for component drill-down")
            source_root=service
            for value in self.catalog.unit_page(project,app,analysis,"architecture",limit=10000):
                if value.get("type")=="DECLARED_SERVICE" and value.get("name")==service and value.get("source_root"):
                    source_root=value["source_root"]
                    break
            with self.store.engine.connect() as conn:
                scope=self.catalog.scope(chunks,project,app,analysis)
                path=chunks.c.file.startswith(source_root+"/",autoescape=True) if source_root not in {"application","."} else ~chunks.c.file.contains("/")
                query=select(chunks.c.payload).where(scope,path,chunks.c.kind.in_(["class","function","method","api_endpoint","declared_property","orm_field"]))
                values=[json.loads(v) for v in conn.execute(query.order_by(chunks.c.id).limit(limit).offset(offset)).scalars()]
            ids={v["id"] for v in values}
            relationships=self.catalog.neighbors(project,app,analysis,list(ids),limit=500)
            return dict(summary="Service component drill-down",level=level,nodes=[self.public_item(actor,project,app,v) for v in values],
                edges=[e for e in relationships if e["source"] in ids and e["target"] in ids],
                total_is_page_estimate=True,total=offset+len(values),next_offset=offset+limit if len(values)==limit else None,
                limitations=["Static components; unresolved dynamic bindings remain candidates"])
        with self.store.engine.connect() as conn:
            manifests=[]
            # Only manifest units determine service roots. Keep this overview
            # bounded even if the repository contains millions of symbols.
            query=select(units.c.payload).where(self.catalog.scope(units,project,app,analysis),units.c.kind=="architecture")
            for raw in conn.execution_options(stream_results=True,yield_per=256).execute(query).scalars():
                unit=json.loads(raw)
                if unit["type"] in {"SERVICE_MANIFEST","DECLARED_SERVICE"}:
                    manifests.append(unit)
                    if len(manifests)>=10000:
                        break
            service_roots={u["source_root"]:u["name"] for u in manifests if u["type"]=="DECLARED_SERVICE" and u.get("source_root")}
            roots=sorted({u["name"] for u in manifests if u["type"]=="SERVICE_MANIFEST"}|set(service_roots),key=len,reverse=True)
            def owner(file):
                root=next((r for r in roots if r!="." and file.startswith(r+"/")),None)
                return service_roots.get(root,root) if root else file.split("/")[0] if "/" in file else "application"
            groups={}
            relationships={}
            for raw in conn.execution_options(stream_results=True,yield_per=256).execute(query).scalars():
                unit=json.loads(raw)
                group=unit["name"] if unit["type"] in {"DECLARED_SERVICE","SERVICE_DEPENDENCY"} else owner(unit["file"])
                if service and group!=service and unit.get("name")!=service:
                    continue
                node=groups.setdefault(group,dict(id=group,name=group,kind="service",counts={},evidence=[]))
                node["counts"][unit["type"]]=node["counts"].get(unit["type"],0)+1
                if len(node["evidence"])<8:
                    node["evidence"].append(dict(file=unit["file"],line=unit["line"],kind=unit["type"],status=unit["status"]))
                if unit["type"]=="SERVICE_DEPENDENCY":
                    edge=dict(source=unit["name"],target=unit["target"],kind="depends_on",status=unit["status"],evidence=dict(file=unit["file"],line=unit["line"]))
                    relationships[(edge["source"],edge["target"],edge["kind"])]=edge
                elif unit["type"] in {"TABLE","CONFIGURED_ENDPOINT"}:
                    target=("database:" if unit["type"]=="TABLE" else "endpoint:")+unit["name"]
                    edge=dict(source=group,target=target,kind="references" if unit["type"]=="TABLE" else "configured_candidate",
                        status=unit["status"],evidence=dict(file=unit["file"],line=unit["line"]))
                    relationships[(group,target,edge["kind"])]=edge
        if level=="system":
            groups={"application":dict(id="application",name=self.app(actor,project,app)["name"],kind="system",
                counts={"services":len(groups)},evidence=[e for g in groups.values() for e in g["evidence"]][:8])}
            relationships={("application",e["target"],e["kind"]):{**e,"source":"application"} for e in relationships.values()
                           if e["target"].startswith(("database:","endpoint:"))}
        selected=sorted(groups)[offset:offset+limit]
        diagram=["flowchart LR"]
        # Mermaid IDs are generated; untrusted labels are encoded, never code.
        identifiers={name:"n"+str(index) for index,name in enumerate(sorted(set(selected)|{e["target"] for e in relationships.values() if e["source"] in selected}))}
        for name,id_ in identifiers.items():
            label=name.replace('"',"'").replace("[","(").replace("]",")").replace("\n"," ").replace("`","")
            diagram.append(f'  {id_}["{label[:150]}"]')
        visible=[]
        for edge in relationships.values():
            if edge["source"] in identifiers and edge["target"] in identifiers and len(visible)<500:
                visible.append(edge)
                arrow="-.->" if edge["status"]=="CANDIDATE" else "-->"
                diagram.append(f'  {identifiers[edge["source"]]} {arrow} {identifiers[edge["target"]]}')
        result = dict(summary="Evidence-backed architecture aggregation",level=level,nodes=[groups[k] for k in selected],edges=visible,
            mermaid="\n".join(diagram),total=len(groups),next_offset=offset+limit if offset+limit<len(groups) else None,
            limitations=["Runtime routes, service meshes and dynamic endpoints need additional approved evidence", "Candidate/configuration edges are not runtime observations", "Overview caps 10000 manifests and 500 visible edges; paginate/drill down"])
        from .discovery_dossier import facts_for, topology
        if level=="services":
            facts,_=facts_for(self,project,app,analysis,kinds={"configured_address","config_consumer","service_declaration","environment_reference"})
            result=topology(self,project,app,analysis,facts,result)
        return result
