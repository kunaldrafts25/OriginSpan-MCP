"""Streaming content-addressed inventory and independently leased extraction.

Workers execute only OriginSpan parsers in separate processes, never repository code.
The API can stop without losing the durable work queue.
"""
from __future__ import annotations

from .compat import getenv
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
import time
import uuid
import yaml
from .discovery_facts import yaml_documents

from sqlalchemy import select, update, delete, insert, func, and_, or_

from .catalog import Catalog, LeaseLost, jobs, tasks, files, chunks, edges, units, cache, active,interpretations
from .corpus import build_corpus, normalize
from .sources import accepted, checked_path, redact, SKIP, SECRET_NAME
from .structural import scan_application

EXTRACTION_VERSION = "originspan-production-8"
MAX_PARSE_BYTES = int(getenv("DIAMOND_MAX_PARSE_BYTES", str(8 * 1024 * 1024)))


def hash_id(*values):
    return hashlib.sha256("\0".join(str(v) for v in values).encode()).hexdigest()


def io_path(path):
    """Extended Windows paths for deep Java packages; logical paths stay portable."""
    value = str(Path(path).absolute())
    return Path("\\\\?\\" + value) if os.name == "nt" and not value.startswith("\\\\?\\") else Path(value)


class BlobStore:
    def __init__(self, state):
        self.root = Path(state) / "blobs"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, digest):
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid content digest")
        return self.root / digest[:2] / digest[2:4] / digest

    def capture(self, source, check=lambda: None, allowed_root=None):
        """Stream/hash a stable regular file with O(1) memory and atomic placement."""
        source = io_path(source)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(source, flags)
        temp = self.root / ("pending-" + uuid.uuid4().hex)
        digest, size = hashlib.sha256(), 0
        try:
            with os.fdopen(descriptor, "rb") as stream, temp.open("xb") as output:
                if allowed_root is not None:
                    actual=opened_path(stream.fileno())
                    if not actual.is_relative_to(Path(allowed_root)):
                        raise PermissionError("Opened source escaped its approved root")
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError("Non-regular source is not authorized")
                while data := stream.read(1024 * 1024):
                    check()
                    size += len(data)
                    digest.update(data)
                    output.write(data)
                after = os.fstat(stream.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    raise ValueError("Source changed during capture; retry on a stable source")
                output.flush()
                os.fsync(output.fileno())
            destination = self.path(digest.hexdigest())
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                os.replace(temp, destination)
            return digest.hexdigest(), size
        finally:
            temp.unlink(missing_ok=True)


def opened_path(descriptor):
    """Validate the opened handle, rather than racing parent-path symlink checks."""
    if os.name=="nt":
        import ctypes,msvcrt
        from ctypes import wintypes
        function=ctypes.windll.kernel32.GetFinalPathNameByHandleW
        function.argtypes=[wintypes.HANDLE,wintypes.LPWSTR,wintypes.DWORD,wintypes.DWORD]
        function.restype=wintypes.DWORD
        buffer=ctypes.create_unicode_buffer(32768)
        length=function(msvcrt.get_osfhandle(descriptor),buffer,len(buffer),0)
        if not length or length>=len(buffer):
            raise OSError("Cannot validate opened source handle")
        value=buffer.value
        value="\\\\"+value[8:] if value.startswith("\\\\?\\UNC\\") else value.removeprefix("\\\\?\\")
        return Path(value)
    if Path("/proc/self/fd").is_dir():
        return Path(os.readlink(f"/proc/self/fd/{descriptor}").removesuffix(" (deleted)"))
    # Platforms without a handle-path primitive must be deployed with immutable
    # managed sources; do not silently weaken the shared-worker boundary.
    raise OSError("Secure handle validation is unavailable on this platform")


def source_files(root):
    """Depth-first scandir: no repository-sized or directory-sized filename list."""
    def visit(directory,depth=0):
        if depth>128:
            raise ValueError("Source nesting exceeds the scanner's file-descriptor budget")
        with os.scandir(directory) as entries:
            for entry in entries:
                path=Path(entry.path)
                if entry.is_symlink() or path.is_junction():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in SKIP and not SECRET_NAME.search(entry.name):
                        yield from visit(path,depth+1)
                elif entry.is_file(follow_symlinks=False):
                    yield path
    yield from visit(io_path(root))


def _sql_metadata(sql):
    """Preserve query structure without exporting embedded literal values."""
    import sqlglot
    from sqlglot import exp
    try:
        parsed = sqlglot.parse(sql)
        return ";\n".join(s.transform(lambda node: exp.Placeholder() if isinstance(node, exp.Literal) else node).sql()
                          for s in parsed if s is not None)
    except Exception:
        return None


def parse_file(blob, relative, work_root):
    """Called in a parser process. Input is an immutable approved blob, not code to import."""
    from .sql_intelligence import analyze_sql
    # Inherit the operator's private state-directory ACL. Python 3.13's Windows
    # mkdtemp(0700) ACL is incompatible with managed subprocess sandboxes.
    directory = Path(work_root) / ("parse-" + uuid.uuid4().hex)
    directory.mkdir(exist_ok=False)
    try:
        # A short internal basename avoids Windows path ceilings. Output locations
        # are rebased to the original path before becoming evidence.
        local = directory / ("source" + Path(relative).suffix)
        if Path(relative).name == "Dockerfile":
            local = directory / "Dockerfile"
        shutil.copyfile(blob, local)
        structural = scan_application(directory, trusted_root=Path(work_root), max_file_bytes=MAX_PARSE_BYTES)
        evidence, relationships, gaps = build_corpus(directory, structural)
        content = local.read_text(encoding="utf-8-sig") if local.suffix != ".scip" else ""
        if local.suffix==".scip":
            from .scip import read_index
            occurrences=read_index(local.read_bytes())
            definitions={}
            pending=[]
            for occurrence in occurrences:
                symbol=occurrence["symbol"]
                key=(occurrence["file"],symbol) if symbol.startswith("local ") else symbol
                id_=hash_id(relative,occurrence["file"],occurrence["line"],symbol,occurrence["definition"])[:30]
                item=dict(id=id_,name=symbol[-200:],kind="compiler_definition" if occurrence["definition"] else "compiler_reference",
                    file=local.name,line=1,end_line=1,description=redact(occurrence["documentation"]),declaration="supplied_index",
                    extractor="scip",extractor_version=EXTRACTION_VERSION,search_text=symbol+" "+occurrence["documentation"],
                    compiler_symbol=symbol,supplied_location={"file":occurrence["file"],"line":occurrence["line"]},
                    source_sha256=hashlib.sha256(local.read_bytes()).hexdigest())
                evidence.append(item)
                if occurrence["definition"]:definitions.setdefault(key,[]).append(id_)
                else:pending.append((key,id_))
            for key,id_ in pending:
                for target in definitions.get(key,[])[:10]:
                    relationships.append(dict(source=id_,target=target,type="COMPILER_REFERENCE",status="SUPPLIED_INDEX"))
            gaps=[g for g in gaps if "absent from approved snapshot" not in g]
            gaps.append("SCIP locations are supplied compiler metadata; index freshness and source bindings require review")
        sql_units = []
        for query in structural["sql"]:
            sql_units.append({"analysis": query, "line": query.get("evidence", {}).get("line") or 1,
                              "language": query.get("query_language", "SQL"), "metadata_sql": ";\n".join(query.get("metadata_statements",[])) or None})
        for query in structural.get("non_sql_queries",[]):
            sql_units.append(dict(line=query.get("evidence",{}).get("line") or 1,language=query.get("query_language","UNKNOWN"),metadata_sql=None,metadata_expression=_sql_metadata(re.sub(r"\?(\d+)", r":p\1", query.get("expression", ""))),
                analysis=dict(status="UNRESOLVED",limitations=[query.get("reason","ORM query language requires semantic bindings")],tables=[],columns=[])))
        if local.suffix == ".sql" and not structural["sql"]:
            sql_units.append({"analysis": analyze_sql(_sql_metadata(content) or ""), "line": 1,
                              "language": "SQL", "metadata_sql": _sql_metadata(content)})
        if local.suffix == ".py":
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.match(r"\s*(SELECT|WITH|INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+VIEW)\b", node.value, re.I):
                        sql = _sql_metadata(node.value)
                        if sql:
                            sql_units.append(dict(analysis=analyze_sql(sql), metadata_sql=sql, line=node.lineno,
                                                  language="SQL", extraction="python_constant"))
                    elif isinstance(node, ast.JoinedStr):
                        parts = [v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
                        if re.match(r"\s*(SELECT|WITH|INSERT|UPDATE|DELETE)\b", "".join(parts), re.I):
                            sql_units.append(dict(line=node.lineno, language="SQL", metadata_sql=None,
                                analysis={"status":"UNRESOLVED", "limitations":["Dynamic f-string SQL requires runtime bindings; not executed"]}))
            except SyntaxError:
                pass
        # Language-neutral symbol candidates help unsupported grammars without
        # falsely claiming compiler resolution.
        if local.suffix in {".ts", ".tsx", ".js", ".go", ".rs", ".cs", ".rb", ".c", ".cpp", ".h"}:
            for match in re.finditer(r"(?m)\b(?:class|interface|function|func|def|struct|enum|fn)\s+([A-Za-z_]\w*)", content):
                name, line = match[1], content.count("\n", 0, match.start()) + 1
                evidence.append(dict(id=hash_id(relative, line, name)[:30], name=name, kind="symbol_candidate", file=local.name,
                    line=line, end_line=line, declaration="candidate", extractor="structural_pattern",
                    extractor_version=EXTRACTION_VERSION, description="Structural declaration candidate; compiler binding unresolved",
                    search_text=name + " " + relative, source_sha256=hashlib.sha256(local.read_bytes()).hexdigest()))
        from .discovery_facts import extract_facts, deployment_facts, evidence_for_facts
        discovery = extract_facts(content, relative)
        if local.suffix in {".yaml", ".yml"}:
            try:
                discovery.extend(deployment_facts(content, relative))
            except (ValueError, yaml.YAMLError, RecursionError):
                gaps.append("Deployment discovery metadata could not be parsed")
        for fact_item in evidence_for_facts(discovery):
            fact_item["source_sha256"] = hashlib.sha256(local.read_bytes()).hexdigest()
            evidence.append(fact_item)
        id_map = {}
        for item in evidence:
            old = item["id"]
            item["file"] = relative
            item["id"] = "ev-" + hash_id(relative, item["line"], item["kind"], item["name"],item.get("entity",""),
                json.dumps(item.get("supplied_location"),sort_keys=True) if item.get("supplied_location") else "")[:24]
            item["search_text"] = item["search_text"].replace(local.name, relative)
            id_map[old] = item["id"]
        seen={i["id"] for i in evidence}
        for unit in sql_units:
            for table in unit.get("analysis",{}).get("tables",[]):
                id_="ev-"+hash_id(relative,unit["line"],"sql_table_reference",table)[:24]
                if id_ not in seen:
                    evidence.append(dict(id=id_,name=table,kind="sql_table_reference",file=relative,line=unit["line"],end_line=unit["line"],
                        entity=table,description="Static SQL table reference; bind to approved DDL for field meaning",declaration="observed_fact",
                        extractor="sqlglot",extractor_version=EXTRACTION_VERSION,search_text=table+" SQL query "+relative,
                        source_sha256=hashlib.sha256(local.read_bytes()).hexdigest()))
                    seen.add(id_)
        relationships = [{**r, "source": id_map[r["source"]], "target": id_map[r["target"]]}
                         for r in relationships if r["source"] in id_map and r["target"] in id_map]
        for unit in sql_units:
            unit["file"] = relative
            # Parser exceptions can include SQL literals; only sanitized metadata
            # SQL is exposed. Underlying schema/lineage metadata contains names.
        architecture = []
        for node in structural["nodes"]:
            if node["type"] in {"API_ENDPOINT", "TABLE", "PACKAGE", "SERVICE"}:
                architecture.append(dict(type=node["type"], name=node["name"], file=relative,
                    line=node.get("evidence", {}).get("line") or 1, status="STATIC_EVIDENCE"))
        if Path(relative).name in {"Dockerfile", "pom.xml", "pyproject.toml", "package.json", "go.mod", "Cargo.toml"}:
            architecture.append(dict(type="SERVICE_MANIFEST", name=Path(relative).parent.as_posix(), file=relative, line=1, status="DECLARED_MANIFEST"))
        if local.suffix in {".yaml", ".yml", ".json"}:
            try:
                configurations = [json.loads(content)] if local.suffix == ".json" else yaml_documents(content)
                for configuration in configurations:
                    if isinstance(configuration,dict):
                        services=configuration.get("services",{})
                        for name,spec in services.items() if isinstance(services,dict) else []:
                            if not isinstance(spec,dict):
                                continue
                            build=spec.get("build")
                            context=build.get("context") if isinstance(build,dict) else build if isinstance(build,str) else None
                            root=str((Path(relative).parent/str(context)).as_posix()) if context else None
                            architecture.append(dict(type="DECLARED_SERVICE",name=name,file=relative,line=1,status="DECLARED_CONFIG",source_root=root))
                            dependencies=spec.get("depends_on",[])
                            for target in dependencies if isinstance(dependencies,(dict,list)) else []:
                                architecture.append(dict(type="SERVICE_DEPENDENCY",name=name,target=str(target),file=relative,line=1,status="DECLARED_CONFIG"))
                        if configuration.get("kind") in {"Service","Deployment","StatefulSet","Ingress"}:
                            name=configuration.get("metadata",{}).get("name")
                            if isinstance(name,str):
                                architecture.append(dict(type="KUBERNETES_"+configuration["kind"].upper(),name=name,file=relative,line=1,status="DECLARED_CONFIG"))
            except (ValueError,yaml.YAMLError,RecursionError):
                gaps.append("Deployment metadata could not be parsed")
        for match in re.finditer(r"https?://([A-Za-z0-9_.-]+)(?::(\d+))?(/[A-Za-z0-9_/{}/.-]*)?", content if local.suffix not in {".md", ".txt"} else ""):
            if "license" not in match[0].lower() and "apache.org" not in match[1]:
                architecture.append(dict(type="CONFIGURED_ENDPOINT", name=match[1], file=relative,
                    line=content.count("\n",0,match.start())+1, status="CANDIDATE", route=match[3] or "/"))
        # Explicit source coverage; no automatic business label is asserted.
        return dict(evidence=evidence, edges=relationships, gaps=[redact(g.replace(local.name, relative)) for g in gaps],
                    sql=sql_units, architecture=architecture, discovery=discovery, components=len(structural["components"]),
                    source_hash=hashlib.sha256(local.read_bytes()).hexdigest())
    finally:
        # Only this parser-created internal directory is removed; never a source path.
        resolved=directory.resolve()
        boundary=Path(work_root).resolve()
        if resolved==boundary or not resolved.is_relative_to(boundary):
            raise PermissionError("Parser cleanup path escaped its internal work directory")
        shutil.rmtree(resolved)


def parser_entry(connection, blob, relative, work_root):
    try:
        if os.name != "nt":
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        connection.send((True, parse_file(blob, relative, work_root)))
    except BaseException as exc:
        connection.send((False, type(exc).__name__ + ": " + redact(str(exc))[:500]))
    finally:
        connection.close()


def parser_limits():
    if os.name!="nt":
        import resource
        ceiling=int(getenv("DIAMOND_PARSER_MEMORY_BYTES",str(2*1024**3)))
        resource.setrlimit(resource.RLIMIT_AS,(ceiling,ceiling))


class Pipeline:
    def __init__(self, catalog: Catalog, config, embeddings=None):
        self.catalog, self.config, self.embeddings = catalog, config, embeddings
        self.blobs = BlobStore(config.state)
        self.work = Path(config.state) / "work"
        self.work.mkdir(parents=True, exist_ok=True)
        self._parser_pools = threading.local()

    def isolated_parse(self, blob, relative):
        # Amortize interpreter/grammar startup without allowing a hung native
        # parser to survive its deadline. Each worker thread owns its own pool.
        pool=getattr(self._parser_pools,"pool",None)
        if pool is None:
            pool=multiprocessing.get_context("spawn").Pool(processes=1,maxtasksperchild=100,initializer=parser_limits)
            self._parser_pools.pool=pool
        future=pool.apply_async(parse_file,(str(blob),relative,str(self.work)))
        try:
            return future.get(timeout=int(getenv("DIAMOND_PARSE_TIMEOUT","90")))
        except BaseException:
            pool.terminate()
            pool.join()
            self._parser_pools.pool=None
            raise

    def close_parser(self):
        pool=getattr(self._parser_pools,"pool",None)
        if pool is not None:
            pool.terminate()
            pool.join()
            self._parser_pools.pool=None

    def inventory(self, task):
        catalog = self.catalog
        job = catalog.job(task["project"], task["app"], task["job"])
        root = checked_path(job["source"], self.config.roots + (self.config.state / "imports",))
        last_beat = [time.monotonic()]
        max_bytes = int(getenv("DIAMOND_SOURCE_QUOTA_BYTES", "0"))
        min_free = int(getenv("DIAMOND_MIN_FREE_BYTES", str(256 * 1024**2)))
        captured = job["source_bytes"]
        def check():
            if time.monotonic() - last_beat[0] > 5:
                catalog.heartbeat(task)
                last_beat[0] = time.monotonic()
        for path in source_files(root):
            check()
            relative = path.relative_to(io_path(root)).as_posix()
            if path.is_symlink() or path.is_junction() or not accepted(relative):
                # Unknown artifact types are visible without opening record or
                # binary exports. Secret paths remain excluded from the inventory.
                if not any(SECRET_NAME.search(p) for p in Path(relative).parts):
                    size=path.stat().st_size
                    state="RECORD_DATA_EXCLUDED" if path.suffix.lower() in {".csv",".parquet",".orc",".avro"} else "UNSUPPORTED"
                    with catalog.engine.begin() as conn:
                        catalog.fence(conn,task)
                        catalog.upsert(conn,files,dict(job=task["job"],path=relative,project=task["project"],app=task["app"],
                            sha=None,bytes=size,state=state,detail="Not opened: outside approved extractor formats"),["job","path"])
                continue
            with catalog.engine.connect() as conn:
                if conn.execute(select(files.c.path).where(files.c.job == task["job"], files.c.path == relative)).first():
                    continue
            size = path.stat().st_size
            if max_bytes and captured + size > max_bytes:
                raise ValueError("Operator source quota reached; increase quota explicitly or reduce scope")
            if shutil.disk_usage(self.config.state).free < min_free + size:
                raise ValueError("Content storage free-space reserve reached")
            digest, size = self.blobs.capture(path, check,allowed_root=root)
            captured += size
            state = "PENDING" if size <= MAX_PARSE_BYTES else "OVERSIZED"
            detail = "Artifact captured; native parse limit exceeded; narrow/split the artifact" if state == "OVERSIZED" else ""
            with catalog.engine.begin() as conn:
                catalog.fence(conn, task)
                catalog.upsert(conn, files, dict(job=task["job"], path=relative, project=task["project"], app=task["app"],
                    sha=digest, bytes=size, state=state, detail=detail), ["job", "path"])
                if state == "PENDING":
                    statement = dict(id=hash_id(task["job"], relative), job=task["job"], project=task["project"], app=task["app"],
                        kind="extract", state="PENDING", attempts=0, lease_until=0, available_at=0,
                        payload=json.dumps(dict(path=relative, sha=digest, bytes=size)))
                    conn.execute(tasks.insert().values(**statement))
        with catalog.engine.begin() as conn:
            catalog.fence(conn, task)
            conn.execute(update(jobs).where(jobs.c.id == task["job"]).values(inventory_done=1))
            conn.execute(update(tasks).where(tasks.c.id == task["id"], tasks.c.token == task["token"]).values(state="DONE", token=None, lease_until=0))

    def extract(self, task, isolate=True):
        c = self.catalog
        relative, sha = task["payload"]["path"], task["payload"]["sha"]
        profile = self.embeddings.profile if self.embeddings else None
        key = hash_id(EXTRACTION_VERSION, relative, sha, json.dumps(profile, sort_keys=True))
        with c.engine.connect() as conn:
            cached = conn.execute(select(cache.c.payload).where(cache.c.project == task["project"], cache.c.app == task["app"], cache.c.key == key)).scalar()
        reused = cached is not None
        if reused:
            result = json.loads(cached)
        elif isolate:
            result=self.isolated_parse(self.blobs.path(sha),relative)
        else:
            result = parse_file(self.blobs.path(sha), relative, self.work)
        if not reused and self.embeddings:
            vectors = []
            for start in range(0, len(result["evidence"]), 32):
                c.heartbeat(task)
                vectors.extend(self.embeddings.encode([i["search_text"] for i in result["evidence"][start:start+32]]))
            for item, vector in zip(result["evidence"], vectors):
                item["vector"] = vector
        c.heartbeat(task)
        retrieval = getattr(self, "retrieval", None)
        if retrieval and retrieval.pgvector:
            first_vector = next((i["vector"] for i in result["evidence"] if i.get("vector") is not None), None)
            if first_vector is not None:
                retrieval.initialize_native(task["project"], task["app"], task["job"], len(first_vector))
        with c.engine.begin() as conn:
            c.fence(conn, task)
            for item in result["evidence"]:
                value = {k:v for k,v in item.items() if k != "vector"}
                value.update(application_id=task["app"], analysis_id=task["job"], snapshot=sha)
                row = dict(project=task["project"], app=task["app"], job=task["job"], id=value["id"], file=relative,
                    name=value["name"], alias=normalize(value["name"]), kind=value["kind"], body=value["search_text"],
                    payload=json.dumps(value), entity=str(value.get("entity",""))[:300], vector=json.dumps(item.get("vector")) if item.get("vector") is not None else None)
                c.upsert(conn, chunks, row, ["project", "app", "job", "id"])
                if retrieval and item.get("vector") is not None:
                    retrieval.put_native(conn, task["project"], task["app"], task["job"], value["id"], item["vector"])
                if c.engine.dialect.name == "sqlite":
                    conn.execute(__import__('sqlalchemy').text("INSERT INTO d3_fts(project,app,job,id,body) VALUES(:project,:app,:job,:id,:body)"), row)
            for edge in result["edges"]:
                c.upsert(conn, edges, dict(job=task["job"], id=hash_id(edge["source"], edge["target"], edge["type"]),
                    project=task["project"], app=task["app"], source=edge["source"], target=edge["target"], kind=edge["type"], payload=json.dumps(edge)), ["job", "id"])
            for kind in ("sql", "architecture", "discovery"):
                for index, value in enumerate(result.get(kind, [])):
                    id_ = hash_id(relative, kind, index)
                    c.upsert(conn, units, dict(job=task["job"], id=id_, project=task["project"], app=task["app"],
                        kind=("discovery_"+value["kind"] if kind=="discovery" else kind), file=relative, payload=json.dumps({**value, "id":id_})), ["job", "id"])
            c.upsert(conn, units, dict(job=task["job"], id=hash_id(relative,"coverage"), project=task["project"], app=task["app"],
                kind="coverage", file=relative, payload=json.dumps(dict(file=relative, limitations=result["gaps"], reused=reused,
                    components=result["components"], neural=profile))), ["job", "id"])
            if not reused:
                c.upsert(conn, cache, dict(project=task["project"], app=task["app"], key=key, payload=json.dumps(result)), ["project", "app", "key"])
            conn.execute(update(files).where(files.c.job == task["job"], files.c.path == relative).values(state="INDEXED"))
            conn.execute(update(tasks).where(tasks.c.id == task["id"], tasks.c.token == task["token"]).values(state="DONE", token=None, lease_until=0))

    def reconcile(self, job):
        c = self.catalog
        with c.engine.begin() as conn:
            # Exhausted expired leases become visible dead letters, never a
            # permanently RUNNING analysis after repeated worker crashes.
            conn.execute(update(tasks).where(tasks.c.job == job, tasks.c.state == "RUNNING", tasks.c.lease_until < c.clock(conn), tasks.c.attempts >= 3)
                         .values(state="DEAD", error="Worker lease expired after maximum attempts", token=None))
            query = select(jobs).where(jobs.c.id == job)
            if c.engine.dialect.name == "postgresql":
                query = query.with_for_update()
            row = conn.execute(query).mappings().first()
            if not row or row["state"] not in {"QUEUED", "RUNNING", "CANCELLING"}:
                return
            if row["cancelled"]:
                conn.execute(update(tasks).where(tasks.c.job == job, tasks.c.state.in_(["PENDING", "RUNNING"])).values(state="CANCELLED", token=None, lease_until=0))
                conn.execute(update(jobs).where(jobs.c.id == job).values(state="CANCELLED"))
                conn.execute(delete(active).where(active.c.job == job))
                return
            outstanding = conn.execute(select(func.count()).select_from(tasks).where(tasks.c.job == job, tasks.c.state.in_(["PENDING", "RUNNING"]))).scalar()
            dead = conn.execute(select(func.count()).select_from(tasks).where(tasks.c.job == job, tasks.c.state == "DEAD")).scalar()
            if outstanding or not row["inventory_done"]:
                if not outstanding and dead:
                    conn.execute(update(jobs).where(jobs.c.id == job).values(state="FAILED"))
                    conn.execute(delete(active).where(active.c.job == job))
                return
            finalize_id = hash_id(job, "finalize")
            finalizer = conn.execute(select(tasks.c.state).where(tasks.c.id == finalize_id)).scalar()
            if finalizer is None:
                conn.execute(tasks.insert().values(id=finalize_id, job=job, project=row["project"], app=row["app"],
                    kind="finalize", state="PENDING", attempts=0, lease_until=0, available_at=0, payload="{}"))
                return
            coverage_gaps = conn.execute(select(func.count()).select_from(files).where(files.c.job == job, files.c.state.in_(["OVERSIZED","FAILED","PENDING"]))).scalar()
            # Lock publication for PostgreSQL to serialize competing finalizers.
            from .store import objects
            query = select(objects.c.payload).where(objects.c.project == row["project"], objects.c.application == row["app"], objects.c.kind == "application", objects.c.id == row["app"])
            if c.engine.dialect.name == "postgresql":
                query = query.with_for_update()
            application = json.loads(conn.execute(query).scalar_one())
            application["current_analysis"] = job
            conn.execute(update(objects).where(objects.c.project == row["project"], objects.c.application == row["app"], objects.c.kind == "application", objects.c.id == row["app"]).values(payload=json.dumps(application)))
            conn.execute(update(jobs).where(jobs.c.id == job).values(state="PARTIAL" if dead or coverage_gaps else "COMPLETED"))
            conn.execute(delete(active).where(active.c.job == job))
            # Invalidate old approvals in database-side batches, not by loading
            # every historical hypothesis into a worker's memory.
            from sqlalchemy import text
            if c.engine.dialect.name=="sqlite":
                conn.execute(text("UPDATE discovery_objects SET payload=json_set(payload,'$.status',:status) WHERE project=:p AND application=:a AND kind='suggestion' AND json_extract(payload,'$.status')='ACCEPTED' AND json_extract(payload,'$.analysis_id')<>:job"),dict(status="NEEDS_REVALIDATION",p=row["project"],a=row["app"],job=job))
                conn.execute(text("UPDATE d3_interpretations SET status=:status,payload=json_set(payload,'$.status',:status) WHERE project=:p AND app=:a AND job<>:job AND status='ACCEPTED'"),dict(status="NEEDS_REVALIDATION",p=row["project"],a=row["app"],job=job))
            else:
                conn.execute(text("UPDATE discovery_objects SET payload=jsonb_set(payload::jsonb,'{status}',to_jsonb(CAST(:status AS text)),true)::text WHERE project=:p AND application=:a AND kind='suggestion' AND payload::jsonb->>'status'='ACCEPTED' AND payload::jsonb->>'analysis_id'<>:job"),dict(status="NEEDS_REVALIDATION",p=row["project"],a=row["app"],job=job))
                conn.execute(text("UPDATE d3_interpretations SET status=CAST(:status AS text),payload=jsonb_set(payload::jsonb,'{status}',to_jsonb(CAST(:status AS text)),true)::text WHERE project=:p AND app=:a AND job<>:job AND status='ACCEPTED'"),dict(status="NEEDS_REVALIDATION",p=row["project"],a=row["app"],job=job))

    def step(self, owner, job=None, isolate=True):
        task = self.catalog.claim(owner, job)
        if not task:
            if job:
                self.reconcile(job)
            else:
                # Recover the commit/reconcile crash window and exhausted leases.
                # A worker must not need the old API process to publish its work.
                pending=select(tasks.c.id).where(tasks.c.job==jobs.c.id,tasks.c.state.in_(["PENDING","RUNNING"])).exists()
                with self.catalog.engine.connect() as conn:
                    expired=select(tasks.c.id).where(tasks.c.job==jobs.c.id,tasks.c.state=="RUNNING",tasks.c.lease_until<self.catalog.clock(conn),tasks.c.attempts>=3).exists()
                    recoverable=list(conn.execute(select(jobs.c.id).where(jobs.c.state.in_(["RUNNING","CANCELLING"]),or_(~pending,expired,jobs.c.cancelled==1)).limit(50)).scalars())
                for id_ in recoverable:
                    self.reconcile(id_)
            return False
        stopped = threading.Event()
        def keep_lease():
            while not stopped.wait(10):
                try:
                    self.catalog.heartbeat(task)
                except Exception:
                    return
        keeper = threading.Thread(target=keep_lease, daemon=True)
        keeper.start()
        try:
            if task["kind"] == "inventory":
                self.inventory(task)
            elif task["kind"] == "extract":
                self.extract(task, isolate)
            else:
                retrieval = getattr(self,"retrieval",None)
                if retrieval and retrieval.pgvector and self.embeddings:
                    retrieval.build_native(task["project"],task["app"],task["job"])
                with self.catalog.engine.begin() as conn:
                    self.catalog.fence(conn,task)
                    self.reduce_schema_edges(conn,task)
                    conn.execute(update(tasks).where(tasks.c.id==task["id"],tasks.c.token==task["token"]).values(state="DONE",token=None,lease_until=0))
        except LeaseLost:
            pass
        except Exception as exc:
            try:
                self.catalog.fail(task, type(exc).__name__ + ": " + redact(str(exc))[:450])
            except LeaseLost:
                pass
        finally:
            stopped.set()
            keeper.join(2)
        self.reconcile(task["job"])
        return True

    def reduce_schema_edges(self,conn,task):
        """Database-side cross-file binding; never builds an application-sized Python graph."""
        refs=chunks.alias("refs")
        fields_=chunks.alias("fields")
        json_builder=func.json_object if self.catalog.engine.dialect.name=="sqlite" else func.json_build_object
        payload=json_builder("source",refs.c.id,"target",fields_.c.id,"type","SCHEMA_REFERENCE","status","CANDIDATE_SCHEMA_BINDING")
        selected=select(refs.c.job,(refs.c.id+":"+fields_.c.id),refs.c.project,refs.c.app,refs.c.id,fields_.c.id,
            __import__('sqlalchemy').literal("SCHEMA_REFERENCE"),payload.cast(__import__('sqlalchemy').Text)).select_from(
                refs.join(fields_,and_(refs.c.project==fields_.c.project,refs.c.app==fields_.c.app,refs.c.job==fields_.c.job,
                    refs.c.name==fields_.c.entity))).where(refs.c.job==task["job"],refs.c.project==task["project"],refs.c.app==task["app"],
                        refs.c.kind.in_(["table","sql_table_reference"]),fields_.c.kind=="declared_schema_field")
        from sqlalchemy.dialects.sqlite import insert as sq
        from sqlalchemy.dialects.postgresql import insert as pg
        statement=(sq if self.catalog.engine.dialect.name=="sqlite" else pg)(edges).from_select(
            ["job","id","project","app","source","target","kind","payload"],selected)
        conn.execute(statement.on_conflict_do_nothing(index_elements=["job","id"]))

    def run(self, owner, job=None, stop=None, isolate=True):
        from sqlalchemy.exc import OperationalError, InterfaceError
        import logging
        failures = 0
        try:
            while stop is None or not stop.is_set():
                try:
                    worked = self.step(owner, job, isolate)
                    if job:
                        with self.catalog.engine.connect() as conn:
                            state = conn.execute(select(jobs.c.state).where(jobs.c.id == job)).scalar()
                        if state not in {"QUEUED", "RUNNING", "CANCELLING"}:
                            return
                    failures = 0
                except (OperationalError, InterfaceError):
                    # A temporary catalog outage must not kill a queue consumer.
                    # Never log exception text: drivers include URLs/SQL/values.
                    failures += 1
                    delay = min(30, 2 ** min(failures - 1, 5))
                    logging.getLogger(__name__).warning("worker_catalog_unavailable retry_seconds=%s", delay)
                    if stop:
                        stop.wait(delay)
                    else:
                        time.sleep(delay)
                    continue
                if not worked:
                    if stop:
                        stop.wait(.25)
                    else:
                        time.sleep(.25)
        finally:
            self.close_parser()
