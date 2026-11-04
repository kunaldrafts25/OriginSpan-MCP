"""Bounded local interpretation over an authorized immutable MCP snapshot."""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
import uuid

from .profiles import atomic_json, fingerprint, replace_file
from .runtime import ContextExceeded, SCHEMA, SYSTEM, GENERATION_POLICY, FIELD_SYSTEM, FIELD_TASK
from .model_input import VERSION as MODEL_INPUT_VERSION
from .observed import observed_section, render_observed
from .fields import select_fields, validate_annotations, VERSION as FIELD_POLICY_VERSION
from .grammar import VERSION as GRAMMAR_VERSION

VERSION = "offline-workflow-4"
STAGES = {
    "purpose": "Explain application purpose and APIs using source descriptions and declared routes.",
    "architecture": "Explain recorded service dependencies by source and target, plus schema references. API route declarations are not service-to-service edges. Distinguish static evidence from runtime behavior.",
    "demographics": "Discover business and demographic field meanings from declarations, definitions and usage. Retain unfamiliar names and competing meanings; never infer personal traits.",
    "sql": "Explain observed SQL value lineage separately from joins and filter dependencies; never invent a source query.",
    "transformations": "Explain observed transformations and field usage; distinguish candidate associations from explicit mappings.",
    "handoff": "Prepare a draft source-to-target mapping discussion. Without a target schema, do not invent target fields or approve mappings. Identify missing rules.",
}
SECTIONS = ("purpose", "apis", "fields", "orm", "transformations", "documentation", "questions")


class BudgetExceeded(ValueError):
    pass


class FieldAlignmentError(ValueError):
    pass


def safe_text(value, limit=900):
    # Imported descriptions and model text are data, never report markup/code.
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")[:limit]


def compact(value, depth=0):
    if isinstance(value, str):
        return value if len(value) <= 500 else value[:480] + ' [truncated metadata]'
    if not isinstance(value, (dict, list)):
        return value
    if depth > 3:
        return "[bounded related metadata]"
    if isinstance(value, dict):
        return {k: compact(v, depth+1) for k, v in value.items()
                if k not in {"excerpt", "source_value", "options", "default", "password", "token"}}
    if isinstance(value, list):
        return [compact(v, depth+1) for v in value[:6]]
    return value[:500] if isinstance(value, str) else value


class Evidence:
    def __init__(self, saved=None):
        self.items = saved or {}
        self.keys = {fingerprint(v): k for k, v in self.items.items()}

    def add(self, data, topic):
        if isinstance(data, list):
            for item in data:
                self.add(item, topic)
        elif isinstance(data, dict):
            location = data if isinstance(data.get("file"), str) else data.get("evidence")
            if isinstance(location, list):
                # A context envelope contains MANY evidence items, not one fact
                # located at its first result. Edges, in contrast, have a kind.
                location = next((v for v in location if isinstance(v, dict) and isinstance(v.get('file'), str)), None) if 'kind' in data else None
            if not isinstance(location, dict) and isinstance(data.get('provenance'), dict):
                location = data['provenance']
            if isinstance(location, dict) and isinstance(location.get("file"), str):
                metadata = data
                if topic == 'lineage' and data.get('provenance', {}).get('kind') == 'observed_source_query':
                    metadata = dict(kind='observed_sql_lineage', query_id=data['provenance']['query_id'],
                                    tables=data.get('tables', []),
                                    value_sources=[s for item in data.get('lineage', []) for s in item.get('sources', [])],
                                    transformations=[item.get('transformation', '') for item in data.get('lineage', [])],
                                    selection_conditions=[dict(kind=c.get('kind'),expression=c.get('expression'), fields=c.get('dependent_fields', [])) for c in data.get('conditions', [])],
                                    statement_kinds=data.get('statement_kinds', []), limitations=data.get('limitations', []))
                record = dict(topic=topic, file=location["file"], line=location.get("line", 1), metadata=compact(metadata))
                key = fingerprint(record)
                if key not in self.keys:
                    ref = "E" + str(len(self.items)+1).zfill(6)
                    self.items[ref] = record
                    self.keys[key] = ref
            for key, item in data.items():
                if key == 'evidence' and isinstance(item, list) and not isinstance(location, dict):
                    self.add(item, topic)
                if isinstance(item, (dict, list)) and key not in {"excerpt", "source", "evidence"}:
                    self.add(item, topic)

    def package(self, stage, limit=12):
        topics = {
            "purpose": {"purpose", "apis", "overview"}, "architecture": {"architecture", "overview", "orm"},
            "demographics": {"fields", "documentation", "questions", "overview"},
            "sql": {"sql", "lineage", "orm"}, "transformations": {"transformations", "fields"},
            "handoff": {"fields", "documentation", "questions"},
        }[stage]
        targeted = "context_" + stage
        priorities = {
            'purpose': ['purpose', 'apis', 'overview'], 'architecture': ['architecture', 'overview', 'orm'],
            'demographics': ['fields', 'documentation', 'questions', 'overview'],
            'sql': ['lineage', 'sql', 'orm'], 'transformations': ['transformations', 'fields'],
            'handoff': ['fields', 'questions', 'documentation'],
        }[stage]
        ordered = [(key, value) for key, value in self.items.items() if value["topic"] in topics or value["topic"] == targeted]
        ordered.sort(key=lambda pair: -1 if pair[1]['topic'] == targeted else priorities.index(pair[1]['topic']))
        if stage == 'demographics':
            def strength(pair):
                metadata=pair[1]['metadata']
                if metadata.get('definition') and not metadata.get('title'):return 0
                return {'declared_schema_field':1,'form_field':2,'field_usage':4}.get(metadata.get('kind'),3)
            ordered.sort(key=strength)
            # Repeated usages of one field must not crowd every other field out
            # of a small model's package. No business glossary/classification is
            # used: preserve distinct observed names and their definitions.
            seen = set()
            distinct, repeated = [], []
            for pair in ordered:
                name = pair[1]['metadata'].get('name')
                identity=(name,pair[1]['metadata'].get('entity'))
                if name and identity in seen:
                    repeated.append(pair)
                else:
                    distinct.append(pair)
                    if name: seen.add(identity)
            ordered = distinct + repeated
        return [dict(ref=key, **value) for key, value in ordered[:limit]]


def validate_interpretation(value, allowed):
    if not isinstance(value, dict) or set(value) != {"claims", "unknowns", "requests"}:
        raise ValueError("Interpretation must contain only claims, unknowns and requests")
    claims, unknowns, requests = value["claims"], value["unknowns"], value["requests"]
    if not isinstance(claims, list) or len(claims) > 3 or not isinstance(unknowns, list) or len(unknowns) > 3:
        raise ValueError("Interpretation exceeds its item budget")
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {"text", "citations"}:
            raise ValueError("Invalid claim fields")
        if not isinstance(claim["text"], str) or not 1 <= len(claim["text"]) <= 450:
            raise ValueError("Invalid claim text")
        refs = claim["citations"]
        if not isinstance(refs, list) or not 1 <= len(refs) <= 8 or any(not isinstance(r, str) or r not in allowed for r in refs):
            raise ValueError("Claim cites nonexistent, stale or undisclosed evidence")
    if any(not isinstance(x, str) or len(x) > 250 for x in unknowns):
        raise ValueError("Invalid unresolved question")
    if not isinstance(requests, list) or len(requests) > 2:
        raise ValueError("Too many evidence requests")
    for request in requests:
        if not isinstance(request, dict) or set(request) != {"action", "query"}:
            raise ValueError("Evidence requests cannot override scope or runtime settings")
        if request["action"] not in {"search", "context"} or not isinstance(request["query"], str) or not 1 <= len(request["query"]) <= 500:
            raise ValueError("Evidence request is not allowed")
    return value


class Discovery:
    def __init__(self, tools, runtime, profile, state: Path, project: str):
        self.tools, self.runtime, self.profile = tools, runtime, profile
        self.state, self.project = state, project
        self.budgets = profile["budgets"]
        self.counts = dict(tools=0, model=0)
        self.run = None
        self.folder = None
        self.usage_start = len(runtime.calls)

    async def call(self, tool, **arguments):
        if self.counts["tools"] >= self.budgets["tool_calls"]:
            raise BudgetExceeded("MCP call budget reached; requested scope is incomplete")
        if tool not in {"list_applications", "list_analyses"}:
            arguments.update(application_id=self.run["application_id"], analysis_id=self.run["analysis_id"])
        arguments["project_id"] = self.project
        self.counts["tools"] += 1
        return await self.tools.call(tool, arguments)

    def save(self):
        self.run["counts"] = dict(self.counts)
        self.run["evidence"] = self.evidence.items
        self.run["usage"] = self.run.get("prior_usage", []) + self.runtime.calls[self.usage_start:]
        self.run['runtime_resources'] = getattr(self.runtime, 'resources', {})
        atomic_json(self.folder / "run.json", self.run)
        # Markdown is a local presentation artifact, never sent automatically.
        path = self.folder / "report.md"
        temporary = self.folder / "report.md.tmp"
        temporary.write_text(render(self.run), encoding="utf-8")
        replace_file(temporary, path)

    async def run_report(self, application, analysis=None, question="", resume=None):
        if len(question) > 2000:
            raise ValueError("Question exceeds 2000 characters")
        apps = (await self.call("list_applications"))["data"]["applications"]
        matches = [a for a in apps if a["id"] == application or a["name"] == application]
        if len(matches) != 1:
            raise ValueError("Application must match one authorized ID or exact name")
        app = matches[0]
        jobs = []
        offset = 0
        while True:
            page = (await self.call("list_analyses", application_id=app["id"], limit=20, offset=offset))["data"]
            jobs += page["analyses"]
            if analysis:
                selected = next((j for j in jobs if j["id"] == analysis), None)
            else:
                selected = next((j for j in jobs if j["status"] == "COMPLETED"), None)
            if selected or page.get("next_offset") is None:
                break
            offset = page["next_offset"]
        if not selected or selected["status"] != "COMPLETED":
            raise ValueError("A completed authorized analysis is required; run analyze first")
        scope = dict(project_id=self.project, application_id=app["id"], analysis_id=selected["id"])
        inference_contract = fingerprint(dict(schema=SCHEMA, system=SYSTEM, model_input=MODEL_INPUT_VERSION,
                                             generation=GENERATION_POLICY,field_system=FIELD_SYSTEM,field_task=FIELD_TASK,grammar=GRAMMAR_VERSION,field_policy=FIELD_POLICY_VERSION))
        identity = fingerprint(dict(**scope, question=question, profile=self.profile, workflow=VERSION,
                                    inference_contract=inference_contract))
        base = self.state / "offline-runs"
        if resume:
            if not re.fullmatch(r"[0-9a-f]{32}", resume):
                raise ValueError("Resume requires a run ID, not a file path")
            self.folder = base / resume
            self.run = json.loads((self.folder / "run.json").read_text(encoding="utf-8"))
            if self.run.get("identity") != identity:
                raise ValueError("Resume scope, question, snapshot or model profile has changed")
            fresh_authorization_calls = self.counts['tools']
            self.counts = dict(self.run["counts"])
            # Fresh authorization calls also consume budget, even on resume.
            self.counts["tools"] += fresh_authorization_calls
            self.run["prior_usage"] = self.run.get("usage", [])
        else:
            run_id = uuid.uuid4().hex
            self.folder = base / run_id
            self.run = dict(version=VERSION, inference_contract=inference_contract,
                            id=run_id, identity=identity, **scope, application=app["name"],
                            question=question, profile=self.profile, status="RUNNING", stages={}, facts={},
                            gaps=[], evidence={}, counts={}, usage=[], model_qualified=False,
                            limitations=["Model interpretations require review; citation validity is not business correctness",
                                         "No live records, protected-trait inference, source execution or target writes",
                                         "Full-session token/cost savings and model quality are not certified by this run",
                                         "AI interpretation covers selected evidence, not a business classification of every inventoried field"])
        self.evidence = Evidence(self.run["evidence"])
        self.run["status"] = "RUNNING"
        self.save()
        started = time.monotonic()
        try:
            await self.collect()
            for stage in STAGES:
                if stage in self.run["stages"]:
                    continue
                await self.interpret(stage)
                self.save()
            self.run["status"] = "COMPLETED_WITH_GAPS" if self.run["gaps"] or any(v["unknowns"] for v in self.run["stages"].values()) else "COMPLETED"
        except (BudgetExceeded, ValueError) as exc:
            self.run["status"] = "PARTIAL"
            self.run["gaps"].append(str(exc)[:500])
        except BaseException:
            self.run["status"] = "INTERRUPTED"
            self.save()
            raise
        finally:
            self.run["seconds"] = round(self.run.get("seconds", 0) + time.monotonic()-started, 3)
            self.save()
        return self.run, self.folder

    async def collect(self):
        if "overview" not in self.run["facts"]:
            result = await self.call("get_complete_discovery", section_limit=8)
            self.run["overview_markdown"] = result["text"]
            self.run["facts"]["overview"] = result["data"]
            self.evidence.add(result["data"], "overview")
            self.evidence.add(result['data'].get('architecture', {}), 'architecture')
            self.save()
        for section in SECTIONS:
            page_key = "section_" + section
            saved = self.run["facts"].setdefault(page_key, dict(items=[], next_offset=0, done=False))
            # Budget relevant context instead of exhausting every static-flow row
            # before the interpreter can investigate the actual question.
            page_cap = (2 if self.profile['tier'] == 'laptop' else 4) if section in {'fields', 'apis'} else (1 if self.profile['tier'] == 'laptop' else 2)
            if section == 'documentation': page_cap = 1
            page_size = 50 if section in {'fields', 'apis'} else (25 if section == 'transformations' else 12)
            pages = 0
            while not saved["done"]:
                result = (await self.call("get_discovery_section", section=section, limit=page_size, offset=saved["next_offset"]))["data"]
                saved["items"].extend(result["items"])
                saved["total"] = result["total"]
                self.evidence.add(result["items"], section)
                saved["next_offset"] = result.get("next_offset")
                saved["done"] = saved["next_offset"] is None
                pages += 1
                if pages >= page_cap and not saved['done']:
                    self.run["gaps"].append(f"{section}: {len(saved['items'])}/{saved['total']} records collected; continuation offset {saved['next_offset']} remains available through get_discovery_section")
                    saved["done"] = True
                if result.get("fact_scan", {}).get("truncated"):
                    self.run["gaps"].append(section + ": extraction fact scan was bounded")
                self.save()
        if "sql" not in self.run["facts"]:
            result = (await self.call("list_sql_queries", limit=20))["data"]
            self.run["facts"]["sql"] = result
            self.evidence.add(result, "sql")
            if result.get("next_offset") is not None:
                self.run["gaps"].append("SQL inventory is sampled; use its continuation for all source queries")
            self.save()
        queries = self.run["facts"]["sql"].get("queries", [])
        queries = sorted(queries, key=lambda q: ("SUM(" not in str(q).upper(), q["id"]))
        # Show both an aggregate and a non-aggregate when available instead of
        # spending both lineage slots on equivalent service implementations.
        if queries and 'SUM(' in str(queries[0]).upper():
            other=next((q for q in queries[1:] if 'SUM(' not in str(q).upper()),None)
            if other: queries=[queries[0],other]+[q for q in queries[1:] if q['id']!=other['id']]
        for query in queries[:2]:
            key = "lineage_" + query["id"]
            if key not in self.run["facts"]:
                result = (await self.call("get_sql_lineage", query_id=query["id"]))["data"]
                self.run["facts"][key] = result
                self.evidence.add(result, "lineage")
                self.save()

    async def interpret(self, stage):
        observed = observed_section(stage, self.run, self.evidence.items)
        if observed is not None:
            self.run.setdefault('observed_sections', {})[stage] = observed
            self.run['stages'][stage] = dict(claims=[], unknowns=observed['limitations'], requests=[])
            return
        instruction = STAGES[stage] + (" User question: " + self.run["question"] if self.run["question"] else "")
        base_instruction = instruction
        # Context retrieval runs inside the same application and snapshot.
        if self.run["question"] or stage == "demographics":
            query = self.run['question'] or 'person demographic identity names birth address contact identifiers'
            result = (await self.call("get_semantic_context", question=query[:1800]))["data"]
            self.evidence.add(result, "context_" + stage)
        package = self.evidence.package(stage)
        if stage=='demographics' and hasattr(self.runtime,'annotate_fields') and select_fields(package):
            while True:
                if self.counts['model']>=self.budgets['model_calls']:raise BudgetExceeded('Local model call budget reached')
                self.counts['model']+=1;self.save()
                try:
                    value=await self.runtime.annotate_fields(instruction,package)
                    break
                except ContextExceeded:
                    self.counts['model']-=1
                    if len(package)<=1:raise
                    package=package[:max(1,len(package)//2)]
                    self.run['gaps'].append('Field annotation context was reduced to fit the local model')
            rows=validate_annotations(value,select_fields(package),package)
            self.run['semantic_fields']=rows
            self.run.setdefault('model_evidence',{})[stage]=[row['ref'] for row in package]
            self.run['stages'][stage]=dict(claims=[],unknowns=[row['name']+': '+row['meaning'] for row in rows if row['status']=='unresolved'],requests=[])
            self.run['gaps'].append(f"Local model annotated {len(rows)} selected field evidence records; the inventory is not fully business-classified")
            return
        if not package:
            self.run["stages"][stage] = dict(claims=[], unknowns=["No source-located evidence available for this section"], requests=[])
            return
        # At most one follow-up round and one invalid-output repair per stage.
        for attempt in range(3):
            while True:
                if self.counts["model"] >= self.budgets["model_calls"]:
                    raise BudgetExceeded("Local model call budget reached")
                self.counts["model"] += 1
                self.save()
                try:
                    value = await self.runtime.complete(stage, instruction, package)
                    break
                except ContextExceeded:
                    self.counts["model"] -= 1  # Preflight did not invoke generation.
                    if len(package) <= 1:
                        raise
                    package = package[:max(1, len(package)//2)]
                    note = stage + ": interpretation context was reduced to fit the local model"
                    if note not in self.run['gaps']: self.run["gaps"].append(note)
            try:
                value = validate_interpretation(value, {e["ref"] for e in package})
                if stage=='demographics':
                    names={str(row['metadata']['name']) for row in package if row['metadata'].get('name') and
                           (row.get('topic')=='fields' or row['metadata'].get('kind') in {'declared_schema_field','form_field','field_usage','literal_key_candidate'})}
                    if names and any(not any(re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',claim['text'],re.I) for name in names) for claim in value['claims']):
                        raise FieldAlignmentError('Field interpretations must name supplied source fields')
            except ValueError as exc:
                if attempt == 2:
                    if isinstance(exc,FieldAlignmentError):
                        self.run['stages'][stage]=dict(claims=[],unknowns=['Local model did not produce field-specific interpretations; review the observed inventory and definitions.'],requests=[])
                        self.run['gaps'].append('Demographic interpretations failed field-alignment checks; no unrelated claims were accepted')
                        return
                    raise
                instruction = base_instruction + " Return only the allowed JSON fields and cite ONLY supplied E references. Field claims must name source fields; return no claims if meanings are unsupported."
                continue
            if value["requests"] and attempt == 0:
                for request in value["requests"]:
                    tool = "search_findings" if request["action"] == "search" else "get_semantic_context"
                    arg = "query" if request["action"] == "search" else "question"
                    result = (await self.call(tool, **{arg: request["query"]}))["data"]
                    self.evidence.add(result, "context_" + stage)
                    # Re-rank with the section's evidence policy. A generic
                    # README returned by a follow-up must not displace fields.
                    package = self.evidence.package(stage)
                continue
            if value["requests"]:
                value["unknowns"].append("Additional evidence requests remain unresolved after the bounded follow-up")
            self.run["stages"][stage] = value
            self.run.setdefault("model_evidence", {})[stage] = [e["ref"] for e in package]
            return
        raise ValueError(stage + ": no valid interpretation within the response/context budget")


def render(run):
    out = ["# " + safe_text(run["application"]) + " — local discovery", "",
           "Status: **" + run["status"] + "**. Local interpretation; no online model called.",
           f"Project `{run['project_id']}` · analysis `{run['analysis_id']}` · run `{run['id']}`", "",
           "## Observed application evidence", run.get("overview_markdown", "Evidence collection is incomplete."),
           "", "## Additional field inventory", "| Field | Entity / type | Source |", "| --- | --- | --- |"]
    for item in run["facts"].get("section_fields", {}).get("items", []):
        loc = item.get("evidence", item)
        out.append("| " + " | ".join(safe_text(v) for v in (item.get("name", ""), str(item.get("entity", ""))+" / "+str(item.get("data_type", "")),
                                                          str(loc.get("file", ""))+":"+str(loc.get("line", 1)))) + " |")
    out += ["", "## Discovery findings", "AI interpretations are **unapproved hypotheses**. Observed sections retain parser facts. A valid citation does not establish business correctness."]
    for name, result in run["stages"].items():
        out += ["", "### " + name.replace("_", " ").title()]
        if name=='demographics' and 'semantic_fields' in run:
            out += ['All proposed meanings are **unapproved**.', '', '| Source field / entity | Proposed meaning | Status | Evidence |', '| --- | --- | --- | --- |']
            for row in run['semantic_fields']:
                out.append('| '+' | '.join((safe_text(row['name']+' / '+str(row['entity'])),safe_text(row['meaning']),row['status'],
                    ' '.join('['+ref+']' for ref in row['citations'])))+' |')
            continue
        if name in run.get('observed_sections', {}):
            out += render_observed(run['observed_sections'][name], safe_text)
            continue
        for claim in result["claims"]:
            out.append("- " + safe_text(claim["text"]) + " " + " ".join("["+r+"]" for r in claim["citations"]))
        for unknown in result["unknowns"]:
            out.append("- **Unresolved:** " + safe_text(unknown))
    out += ["", "## Coverage and outstanding work"]
    out += ["- " + safe_text(gap) for gap in dict.fromkeys(run["gaps"] + run["limitations"])]
    pending = set(STAGES)-set(run["stages"])
    if pending:
        out.append("- Incomplete sections: " + ", ".join(sorted(pending)))
    out += ["", "## Model and usage", "- Model: " + safe_text(run["profile"]["model_id"]) + " / " + safe_text(run["profile"]["revision"]),
            "- Qualification: unqualified unless separately accepted by the held-out evaluation.",
            "- Tool calls: " + str(run["counts"].get("tools", 0)) + "; model attempts: " + str(run["counts"].get("model", 0)),
            "- Reported input/output counts are in run.json. Missing reasoning/cached counts remain null, not estimates.",
            "- No full-session token or cost saving is inferred from response size.", "", "## Interpretation sources"]
    used = {r for v in run["stages"].values() for c in v["claims"] for r in c["citations"]}
    used.update(ref for row in run.get('semantic_fields',[]) for ref in row['citations'])
    for section in run.get('observed_sections', {}).values():
        for row in section.get('edges', []) + section.get('queries', []):
            used.add(row['citation'])
    for ref in sorted(used):
        e = run["evidence"][ref]
        out.append(f"- [{ref}] `{safe_text(e['file'])}:{e['line']}`")
    return "\n".join(out) + "\n"


def export_escalation(state: Path, run_id: str, question: str, refs: list[str], destination: Path, approved=False):
    """Export operator-selected metadata only; never contact an online provider."""
    if not approved:
        raise PermissionError("Explicit approval of the exact question and references is required")
    if not re.fullmatch(r"[0-9a-f]{32}", run_id) or not question.strip() or len(question) > 2000 or not 1 <= len(refs) <= 12:
        raise ValueError("Provide a run ID, bounded question and 1–12 reviewed evidence references")
    run = json.loads((state / "offline-runs" / run_id / "run.json").read_text(encoding="utf-8"))
    if any(r not in run["evidence"] for r in refs):
        raise ValueError("Unknown evidence reference")
    value = dict(status="APPROVED_FOR_MANUAL_EXPORT_ONLY", provider_called=False,
                 question=question, scope={k: run[k] for k in ("project_id", "application_id", "analysis_id")},
                 evidence=[dict(ref=r, **run["evidence"][r]) for r in refs],
                 approval="Only this packet; no permission for subsequent disclosures")
    if len(json.dumps(value).encode()) > 30_000:
        raise ValueError("Selected escalation packet exceeds 30 KB")
    atomic_json(destination, value)
    return value
