import asyncio
import copy
import json
from pathlib import Path

import pytest

from diamond_mcp.engine import Actor, Config
from diamond_mcp.service import DiscoveryService
from diamond_mcp.server import create_server
from diamond_mcp.chat_output import payload
from diamond_mcp.offline.profiles import install, load, TIERS, fingerprint
from diamond_mcp.offline.runtime import ContextExceeded, LocalRuntime
from diamond_mcp.offline.transport import StdioTools
from diamond_mcp.offline.workflow import Discovery, validate_interpretation, Evidence, export_escalation
from diamond_mcp.offline.evaluation import evaluate
from diamond_mcp.offline.resources import InferenceLock


@pytest.fixture
def local_profile(tmp_path):
    model = tmp_path / 'tiny.gguf'; model.write_bytes(b'GGUFtestfixture')
    runtime = tmp_path / 'llama-server.exe'; runtime.write_bytes(b'approved-runtime-test-placeholder')
    return install(tmp_path, 'test-profile', model=model, runtime=runtime, model_id='test/fake',
                   revision='revision-1', runtime_version='fixture-1', license_name='test', tier='office')


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    monkeypatch.setenv('DIAMOND_EXTERNAL_WORKERS', 'true')
    source = tmp_path / 'source'; source.mkdir()
    (source / 'README.md').write_text('# Registry\nA registry for customer and contract administration.\n')
    (source / 'schema.sql').write_text('CREATE TABLE people (id INT, C_ID INT, birthday DATE, name TEXT);')
    (source / 'dictionary.md').write_text('# Terms\nC_ID is ambiguous without the declaring entity.\nX17 means renewal date on a contract.\n')
    (source / 'api.py').write_text('raise RuntimeError("NEVER EXECUTE")\ndef get_person():\n return {"birthday": person.birthday}\n')
    state = tmp_path / 'state'
    engine = DiscoveryService(Config(state, (source,)))
    actor = Actor('test', frozenset({'p'}))
    app = engine.register(actor, 'p', 'Registry', source)['id']
    job = engine.start(actor, 'p', app)['id']
    for _ in range(30):
        engine.pipeline.step('fixture', job, isolate=False)
        if engine.job(actor, 'p', app, job)['status'] == 'COMPLETED': break
    assert engine.job(actor, 'p', app, job)['status'] == 'COMPLETED'
    return engine, actor, app, job, state


class InProcessTools:
    """SDK tool dispatch fixture; separate test below exercises actual stdio."""
    def __init__(self, engine, actor):
        self.server = create_server(engine, local_actor=actor)
        self.calls = []

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        result = await self.server.call_tool(name, arguments)
        envelope = payload(result)
        assert envelope['status'] == 'OK', envelope
        return dict(data=envelope['data'], text=result.content[0].text)


class ModelFixture:
    """Clearly synthetic model, never used for quality claims."""
    def __init__(self, mode='valid'):
        self.calls = []
        self.mode = mode
        self.tripped = False

    async def complete(self, section, question, evidence):
        if self.mode == 'interrupt' and not self.tripped:
            self.tripped = True
            raise asyncio.CancelledError()
        if self.mode == 'context' and not self.tripped:
            self.tripped = True
            raise ContextExceeded('fixture context limit')
        self.calls.append(dict(input_tokens=123, output_tokens=42, reasoning_tokens=None, cached_input_tokens=None))
        ref = evidence[0]['ref'] if self.mode != 'bad_citation' else 'E999999'
        text=(str(next((row['metadata']['name'] for row in evidence if row['metadata'].get('name')), 'field'))+' has an unreviewed meaning.') if section=='demographics' else 'A proposed interpretation from supplied evidence.'
        value = dict(claims=[dict(text=text, citations=[ref])], unknowns=['Target schema unavailable.'], requests=[])
        if self.mode == 'malicious': value['requests'] = [dict(action='execute_shell', query='run anything')]
        if self.mode == 'followup' and not self.tripped:
            self.tripped = True
            value['requests'] = [dict(action='search', query='X17 renewal')]
        return value


def test_model_profile_pins_content_and_has_no_implicit_download(tmp_path, local_profile):
    assert load(tmp_path, 'test-profile') == local_profile
    Path(local_profile['model']['path']).write_bytes(b'GGUFchanged')
    with pytest.raises(ValueError, match='hash differs'): load(tmp_path, 'test-profile')
    with pytest.raises(ValueError, match='not installed'): load(tmp_path, 'missing')
    with pytest.raises(ValueError, match='identifier'): load(tmp_path, '../escape')


def test_profile_cannot_enable_thinking_or_inflate_budgets(tmp_path, local_profile):
    path = tmp_path / 'offline-models/test-profile.json'
    value = copy.deepcopy(local_profile); value['thinking'] = True
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='policy'): load(tmp_path, 'test-profile')


@pytest.mark.parametrize('action_request', [
    dict(action='shell', query='whoami'), dict(action='search', query='x', project_id='other'),
    dict(action='context', query='x', approve=True), dict(action='search', query='x'*501),
])
def test_model_actions_cannot_override_security_scope(action_request):
    with pytest.raises(ValueError): validate_interpretation(dict(claims=[], unknowns=[], requests=[action_request]), set())


@pytest.mark.parametrize('value', [
    dict(claims=[dict(text='claim', citations=['E2'])], unknowns=[], requests=[]),
    dict(claims=[dict(text='claim', citations=[])], unknowns=[], requests=[]),
    dict(claims=[], unknowns=[], requests=[], approved=True),
])
def test_invalid_and_undisclosed_citations_rejected(value):
    with pytest.raises(ValueError): validate_interpretation(value, {'E1'})


def test_complete_report_uses_fixed_snapshot_and_unapproved_interpretations(indexed, local_profile):
    engine, actor, app, job, state = indexed
    tools = InProcessTools(engine, actor); model = ModelFixture()
    run, folder = asyncio.run(Discovery(tools, model, local_profile, state, 'p').run_report('Registry'))
    assert run['status'] in {'COMPLETED', 'COMPLETED_WITH_GAPS'}
    assert len(run['stages']) == 6 and run['analysis_id'] == job
    assert run['usage'][0]['reasoning_tokens'] is None
    text = (folder / 'report.md').read_text(encoding='utf-8')
    assert 'unapproved hypotheses' in text and 'No full-session token or cost saving' in text
    assert 'birthday' in text and '### Sources' in text
    for tool, args in tools.calls:
        assert args['project_id'] == 'p'
        if tool not in {'list_applications', 'list_analyses'}:
            assert args['application_id'] == app and args['analysis_id'] == job
    assert not any(tool in {'record_suggestions','review','import_public_github'} for tool, _ in tools.calls)


@pytest.mark.parametrize('mode', ['bad_citation', 'malicious'])
def test_invalid_model_never_becomes_a_fact(indexed, local_profile, mode):
    engine, actor, app, job, state = indexed
    run, folder = asyncio.run(Discovery(InProcessTools(engine, actor), ModelFixture(mode), local_profile, state, 'p').run_report(app))
    assert run['status'] == 'PARTIAL'
    assert run['stages'] == {}
    assert 'E999999' not in (folder / 'report.md').read_text(encoding='utf-8')


def test_interruption_resumes_only_same_snapshot_profile_and_question(indexed, local_profile):
    engine, actor, app, job, state = indexed
    async def scenario():
        d = Discovery(InProcessTools(engine, actor), ModelFixture('interrupt'), local_profile, state, 'p')
        with pytest.raises(asyncio.CancelledError): await d.run_report(app, job)
        saved = json.loads((d.folder / 'run.json').read_text())
        assert saved['status'] == 'INTERRUPTED'
        r, _ = await Discovery(InProcessTools(engine, actor), ModelFixture(), local_profile, state, 'p').run_report(app, job, resume=saved['id'])
        assert len(r['stages']) == 6
        with pytest.raises(ValueError, match='changed'):
            await Discovery(InProcessTools(engine, actor), ModelFixture(), local_profile, state, 'p').run_report(app, job, question='different', resume=saved['id'])
    asyncio.run(scenario())


def test_no_cross_project_resolution(indexed, local_profile):
    engine, actor, app, job, state = indexed
    with pytest.raises(AssertionError):
        asyncio.run(Discovery(InProcessTools(engine, actor), ModelFixture(), local_profile, state, 'other').run_report(app))


def test_evidence_omits_excerpts_and_private_defaults():
    e = Evidence()
    e.add(dict(name='birthday', default='PRIVATE', excerpt='PRIVATE', evidence=dict(file='schema.sql', line=4)), 'fields')
    assert 'PRIVATE' not in json.dumps(e.items)
    assert e.package('demographics')[0]['file'] == 'schema.sql'


def test_context_preflight_does_not_spend_generation_budget(indexed, local_profile):
    engine, actor, app, job, state = indexed
    model = ModelFixture('context')
    run, _ = asyncio.run(Discovery(InProcessTools(engine, actor), model, local_profile, state, 'p').run_report(app))
    assert len(run['stages']) == 6
    assert run['counts']['model'] == len(model.calls)


def test_approved_export_never_contacts_provider(indexed, local_profile, tmp_path):
    engine, actor, app, job, state = indexed
    run, _ = asyncio.run(Discovery(InProcessTools(engine, actor), ModelFixture(), local_profile, state, 'p').run_report(app))
    ref = next(iter(run['evidence']))
    path = tmp_path / 'packet.json'
    with pytest.raises(PermissionError): export_escalation(state, run['id'], 'What does this mean?', [ref], path)
    value = export_escalation(state, run['id'], 'What does this mean?', [ref], path, True)
    assert value['provider_called'] is False and path.is_file()
    assert len(value['evidence']) == 1


def test_real_stdio_tool_client_is_read_only_and_scoped(indexed, monkeypatch):
    _, _, app, job, state = indexed
    monkeypatch.setenv('ORIGINSPAN_DATABASE_URL', 'postgresql://must-not-connect.invalid/database')
    async def scenario():
        async with StdioTools(state, 'p') as tools:
            response = await tools.call('list_applications', dict(project_id='p'))
            assert response['data']['applications'][0]['id'] == app
            with pytest.raises(PermissionError): await tools.call('review', dict(project_id='p'))
            with pytest.raises(PermissionError): await tools.call('list_applications', dict(project_id='other'))
    asyncio.run(scenario())


def test_missing_reviewer_does_not_become_qualification(tmp_path):
    suite = tmp_path / 'suite.json'; suite.write_text(json.dumps(dict(owner_reviewed=False, tasks=[dict(id='x', split='held_out')])) )
    runs = tmp_path / 'runs.json'; runs.write_text('{}')
    result = evaluate(suite, runs)
    assert result['status'] == 'NOT_QUALIFIED' and result['reviewed'] == 0


def test_profile_detects_new_or_changed_runtime_library(tmp_path, local_profile):
    library = Path(local_profile['runtime']['path']).with_name('injected.dll')
    library.write_bytes(b'not approved')
    with pytest.raises(ValueError, match='library set'): load(tmp_path, 'test-profile')


def test_only_one_inference_owns_state_and_crash_safe_lock_releases(tmp_path):
    with InferenceLock(tmp_path):
        with pytest.raises(ValueError, match='Another offline'): InferenceLock(tmp_path).__enter__()
    with InferenceLock(tmp_path): pass


def test_runtime_uses_exact_token_preflight_and_never_estimates_reasoning(local_profile):
    r = LocalRuntime(local_profile)
    calls=[]
    async def post(route, data):
        calls.append((route,data))
        if route=='/apply-template': return dict(prompt='formatted prompt')
        if route=='/tokenize': return dict(tokens=[1]*100)
        return dict(choices=[dict(finish_reason='stop',message=dict(content='{"claims":[],"unknowns":["missing"],"requests":[]}'))],
                    usage=dict(prompt_tokens=100,completion_tokens=20))
    r.post=post
    result=asyncio.run(r.complete('purpose','question',[]))
    assert result['unknowns']==['missing'] and r.calls[0]['reasoning_tokens'] is None
    assert calls[-1][1]['chat_template_kwargs']['enable_thinking'] is False
    assert 'root ::=' in calls[-1][1]['grammar'] and 'claims' in calls[-1][1]['grammar']
    assert all(route.startswith('/') for route,_ in calls)


def test_context_preflight_rejects_before_generation(local_profile):
    r=LocalRuntime(local_profile)
    async def post(route,data):
        if route=='/apply-template': return dict(prompt='x')
        assert route=='/tokenize'
        return dict(tokens=[1]*20000)
    r.post=post
    with pytest.raises(ContextExceeded): asyncio.run(r.complete('purpose','q',[]))
    assert r.calls==[]


def test_memory_limit_terminates_owned_runtime(local_profile,monkeypatch):
    from diamond_mcp.offline import runtime as module
    class Process:
        stopped=False
        def poll(self):return None if not self.stopped else 1
        def terminate(self):self.stopped=True
    r=LocalRuntime(local_profile);r.process=Process()
    monkeypatch.setattr(module,'process_memory',lambda _:dict(rss_mib=999999,peak_rss_mib=999999))
    asyncio.run(r.monitor())
    assert r.process.stopped and r.resources['memory_limit_hit']


def test_targeted_definitions_are_not_lost_behind_generic_inventory():
    e=Evidence()
    for i in range(20):e.add(dict(file='schema.sql',line=i+1,name='field'+str(i)),'fields')
    e.add(dict(file='dictionary.md',line=4,definition='X17 means contract renewal date'),'context_demographics')
    assert e.package('demographics')[0]['file']=='dictionary.md'


def test_model_requests_followup_with_same_scope(indexed,local_profile):
    engine,actor,app,job,state=indexed;tools=InProcessTools(engine,actor)
    run,_=asyncio.run(Discovery(tools,ModelFixture('followup'),local_profile,state,'p').run_report(app))
    assert len(run['stages'])==6
    search=[args for tool,args in tools.calls if tool=='search_findings']
    assert search and search[0]['application_id']==app and search[0]['analysis_id']==job


def test_evaluation_requires_exact_run_question_binding(tmp_path):
    suite=tmp_path/'suite.json';suite.write_text(json.dumps(dict(owner_reviewed=True,tasks=[dict(id='x',split='held_out',question='frozen question')])) )
    run=tmp_path/'run.json';run.write_text(json.dumps(dict(id='run-1',status='COMPLETED',profile=dict(model_id='fake'),
        identity='id',question='different question',counts={},usage=[])))
    runs=tmp_path/'runs.json';runs.write_text(json.dumps(dict(x=str(run))))
    review=tmp_path/'review.json';review.write_text(json.dumps(dict(x=dict(reviewer='owner',run_id='run-1',supported_claims=1,
        unsupported_claims=0,required_findings=1,found_required_findings=1,citations_valid=True,ambiguity_correct=True,
        acceptable_without_online=True,security_passed=True))))
    with pytest.raises(ValueError,match='exact task'):evaluate(suite,runs,review)


def test_architecture_edges_preserve_source_target_and_list_evidence():
    e=Evidence()
    for i in range(20):e.add(dict(file='api.py',line=i+1,name='route'+str(i)),'overview')
    e.add(dict(source='frontend',target='ledgerwriter',kind='configured_service_call',
        evidence=[dict(file='frontend.py',line=663),dict(file='config.yaml',line=28)]),'architecture')
    row=e.package('architecture')[0]
    assert row['file']=='frontend.py'
    assert row['metadata']['source']=='frontend' and row['metadata']['target']=='ledgerwriter'


def test_source_lineage_takes_priority_over_orm_bindings():
    e=Evidence()
    for i in range(20):e.add(dict(file='db.py',line=i+1,name='orm'+str(i)),'orm')
    e.add(dict(provenance=dict(kind='observed_source_query',query_id='q1',file='Repo.java',line=32),
        tables=['TRANSACTIONS'],lineage=[dict(sources=['transactions.amount'],transformation='SUM(amount)')],
        conditions=[dict(expression='account = p1',dependent_fields=['account'])]),'lineage')
    row=e.package('sql')[0]
    assert row['file']=='Repo.java' and row['metadata']['value_sources']==['transactions.amount']
    assert row['metadata']['selection_conditions'][0]['expression']=='account = p1'


def test_context_envelope_does_not_become_one_oversized_falsely_located_fact():
    e=Evidence()
    e.add(dict(summary='context',evidence=[dict(file='dictionary.md',line=1,name='X17',description='renewal'),
        dict(file='model.py',line=5,name='X17')],related=[]),'context_demographics')
    assert len(e.items)==2
    assert all('summary' not in row['metadata'] for row in e.items.values())
    assert e.package('demographics')[0]['metadata']['name']=='X17'


def test_frozen_suite_tampering_is_rejected(tmp_path):
    import hashlib
    from diamond_mcp.offline.evaluation import read_suite
    suite = tmp_path/'suite.json'
    suite.write_text('{"tasks": []}')
    suite.with_suffix('.sha256').write_text(hashlib.sha256(suite.read_bytes()).hexdigest())
    assert read_suite(suite)['tasks']==[]
    suite.write_text('{"tasks": ["changed after comparison"]}')
    with pytest.raises(ValueError, match='checksum changed'):
        read_suite(suite)


def test_checkpoint_retries_transient_windows_reader_lock(tmp_path, monkeypatch):
    import os
    from diamond_mcp.offline.profiles import atomic_json
    original = os.replace
    calls = []
    def replace(source, destination):
        calls.append(1)
        if len(calls)==1:
            raise PermissionError('transient file sharing lock')
        original(source, destination)
    monkeypatch.setattr(os, 'replace', replace)
    destination=tmp_path/'run.json'
    atomic_json(destination, {'status': 'RUNNING'})
    assert json.loads(destination.read_text())['status']=='RUNNING' and len(calls)==2


def test_demographic_context_keeps_distinct_unfamiliar_fields():
    evidence=Evidence()
    for i in range(12):
        evidence.add(dict(file='api.py',line=i+1,name='postal'), 'context_demographics')
    evidence.add(dict(file='schema.sql',line=3,name='X17'), 'fields')
    package=evidence.package('demographics',limit=6)
    assert 'X17' in [row['metadata']['name'] for row in package]


def test_nested_predicate_fields_survive_metadata_compaction():
    from diamond_mcp.offline.workflow import compact
    record=compact(dict(selection_conditions=[dict(expression='account = p1',fields=['account'])]))
    assert record['selection_conditions'][0]['fields']==['account']
    assert 'truncated metadata' in compact('x'*600)


def test_model_digest_retains_unfamiliar_semantics_without_bookkeeping():
    from diamond_mcp.offline.model_input import digest_evidence
    value=digest_evidence([dict(ref='E1',file='dictionary.md',line=4,metadata=dict(
        id='internal-opaque',source_sha256='f'*64,retrieval=[dict(channel='full_text',rank=1)],
        name='X17',vendor_definition='Contract renewal date',nullable=False,scale=0,
        related=dict(entity='Policy',description='Not a date of birth'),excerpt='never disclose'))])
    assert value==[dict(ref='E1',source='dictionary.md:4',facts=dict(name='X17',
        vendor_definition='Contract renewal date',nullable=False,scale=0,
        related=dict(entity='Policy',description='Not a date of birth')))]


def test_response_grammar_only_allows_supplied_citation_ids():
    from diamond_mcp.offline.model_input import cited_schema
    from diamond_mcp.offline.runtime import SCHEMA
    schema=cited_schema(SCHEMA,['E2','E1','E1'])
    assert schema['properties']['claims']['items']['properties']['citations']['items']['enum']==['E1','E2']
    assert 'enum' not in SCHEMA['properties']['claims']['items']['properties']['citations']['items']
    assert cited_schema(SCHEMA,[])['properties']['claims']['maxItems']==0
    assert 'maxLength' not in schema['properties']['claims']['items']['properties']['text']
    assert SCHEMA['properties']['claims']['items']['properties']['text']['maxLength']==450
    with pytest.raises(ValueError,match='claim text'):
        validate_interpretation(dict(claims=[dict(text='x'*451,citations=['E1'])],unknowns=[],requests=[]),{'E1'})


def test_parser_facts_do_not_require_model_restatement(indexed,local_profile):
    engine,actor,app,job,state=indexed
    class Capturing(ModelFixture):
        def __init__(self):super().__init__();self.sections=[]
        async def complete(self,section,question,evidence):
            self.sections.append(section)
            return await super().complete(section,question,evidence)
    model=Capturing()
    run,folder=asyncio.run(Discovery(InProcessTools(engine,actor),model,local_profile,state,'p').run_report(app))
    assert len(run['stages'])==6 and {'purpose','demographics'}<=set(model.sections)
    assert not set(model.sections)&{'architecture','sql','handoff'}
    assert set(run['observed_sections'])=={'architecture','sql','handoff'}
    assert 'No approved target schema' in (folder/'report.md').read_text(encoding='utf-8')


def test_sql_observed_report_separates_value_sources_from_join_and_filter_fields():
    from diamond_mcp.offline.observed import observed_section,render_observed
    from diamond_mcp.offline.workflow import safe_text
    value=dict(provenance=dict(kind='observed_source_query',query_id='q'),
        lineage=[dict(output='total',sources=['t.amount'],transformation='SUM(t.amount)')],
        conditions=[dict(kind='WHERE',expression='t.account = p1',dependent_fields=['t.account']),
                    dict(kind='JOIN',expression='t.id = a.transaction_id',dependent_fields=['t.id','a.transaction_id'])])
    evidence={'E1':dict(topic='lineage',metadata=dict(query_id='q'))}
    result=observed_section('sql',dict(facts=dict(lineage_q=value)),evidence)
    row=result['queries'][0]
    assert row['outputs'][0]['sources']==['t.amount']
    assert row['conditions'][0]['dependent_fields']==['t.account']
    text='\n'.join(render_observed(result,safe_text))
    assert 'SUM(t.amount)' in text and 'WHERE' in text and 'JOIN' in text and '[E1]' in text


def test_local_stream_reassembles_json_preserves_usage_and_discards_reasoning(local_profile):
    r=LocalRuntime(local_profile)
    class Response:
        async def aiter_bytes(self):
            chunks=[dict(choices=[dict(delta=dict(reasoning_content='private trace never retain'))]),
                    dict(choices=[dict(delta=dict(content='{"claims":[],'))]),
                    dict(choices=[dict(delta=dict(content='"unknowns":[],"requests":[]}'),finish_reason='stop')]),
                    dict(choices=[],usage=dict(prompt_tokens=100,completion_tokens=20))]
            raw=b''.join(b'data: '+json.dumps(c).encode()+b'\n\n' for c in chunks)+b'data: [DONE]\n\n'
            for offset in range(0,len(raw),13):yield raw[offset:offset+13]
    result=asyncio.run(r.read_completion_stream(Response()))
    assert json.loads(result['choices'][0]['message']['content'])['claims']==[]
    assert result['usage']['prompt_tokens']==100 and 'private trace' not in json.dumps(result)


def test_incomplete_local_stream_cannot_become_an_accepted_claim(local_profile):
    r=LocalRuntime(local_profile)
    class Response:
        async def aiter_bytes(self):yield b'data: {"choices":[{"delta":{"content":"{}"}}]}\n\n'
    with pytest.raises(ValueError,match='ended before completion'):
        asyncio.run(r.read_completion_stream(Response()))


def test_demographic_package_keeps_same_alias_on_different_entities():
    e=Evidence()
    for i in range(8):e.add(dict(file='api.py',line=i+1,name='C_ID',entity='Customer',kind='field_usage'),'context_demographics')
    e.add(dict(file='schema.sql',line=1,name='C_ID',entity='Customer',kind='declared_schema_field'),'fields')
    e.add(dict(file='schema.sql',line=2,name='C_ID',entity='Contract',kind='declared_schema_field'),'fields')
    package=e.package('demographics',limit=2)
    assert {row['metadata']['entity'] for row in package}=={'Customer','Contract'}
    assert all(row['metadata']['kind']=='declared_schema_field' for row in package)


def test_generic_application_description_cannot_displace_declared_fields():
    e=Evidence()
    e.add(dict(title='Sample application',definition='This is a banking application.',evidence=dict(file='README.md',line=1)),'context_demographics')
    e.add(dict(name='birthday',entity='users',kind='declared_schema_field',file='schema.sql',line=2),'fields')
    assert e.package('demographics',limit=1)[0]['metadata']['name']=='birthday'


def test_valid_citations_do_not_make_unrelated_demographic_claims_acceptable(indexed,local_profile):
    engine,actor,app,job,state=indexed
    class Irrelevant(ModelFixture):
        async def complete(self,section,question,evidence):
            value=await super().complete(section,question,evidence)
            if section=='demographics':value['claims'][0]['text']='This application is an HTTP application.'
            return value
    run,_=asyncio.run(Discovery(InProcessTools(engine,actor),Irrelevant(),local_profile,state,'p').run_report(app))
    assert len(run['stages'])==6 and run['stages']['demographics']['claims']==[]
    assert any('field-alignment' in gap for gap in run['gaps'])


def test_response_repair_preserves_original_question(indexed,local_profile):
    engine,actor,app,job,state=indexed
    class Repair(ModelFixture):
        def __init__(self):super().__init__();self.questions=[]
        async def complete(self,section,question,evidence):
            self.questions.append(question)
            value=await super().complete(section,question,evidence)
            if len(self.questions)==1:value['claims'][0]['citations']=['E999999']
            return value
    model=Repair();query='Explain the X17 contract field'
    run,_=asyncio.run(Discovery(InProcessTools(engine,actor),model,local_profile,state,'p').run_report(app,question=query))
    assert len(run['stages'])==6 and all(query in question for question in model.questions)


def test_field_annotations_are_source_bound_and_cannot_approve_themselves():
    from diamond_mcp.offline.fields import validate_annotations
    fields=[dict(ref='E1',metadata=dict(name='C_ID',entity='Customer')),dict(ref='E2',metadata=dict(name='C_ID',entity='Contract'))]
    valid=dict(annotations=[dict(ref='E1',meaning='Customer identifier',status='hypothesis',citations=['E1'])])
    rows=validate_annotations(valid,fields,fields)
    assert rows[0]['name']=='C_ID' and rows[0]['entity']=='Customer' and rows[0]['approved'] is False
    assert rows[1]['entity']=='Contract' and rows[1]['status']=='unresolved'
    invalid=copy.deepcopy(valid);invalid['annotations'][0]['status']='approved'
    with pytest.raises(ValueError,match='approval'):validate_annotations(invalid,fields,fields)
    invalid=copy.deepcopy(valid);invalid['annotations'][0]['citations']=['E999']
    with pytest.raises(ValueError,match='citations'):validate_annotations(invalid,fields,fields)
    invalid=copy.deepcopy(valid);invalid['annotations']*=2
    with pytest.raises(ValueError,match='duplicate'):validate_annotations(invalid,fields,fields)


def test_typed_field_results_render_as_unapproved_table(indexed,local_profile):
    from diamond_mcp.offline.fields import select_fields
    engine,actor,app,job,state=indexed
    class Annotating(ModelFixture):
        async def annotate_fields(self,question,evidence):
            self.calls.append(dict(input_tokens=100,output_tokens=40))
            return dict(annotations=[dict(ref=row['ref'],meaning='Definition needed',status='unresolved',citations=[row['ref']]) for row in select_fields(evidence)])
    run,folder=asyncio.run(Discovery(InProcessTools(engine,actor),Annotating(),local_profile,state,'p').run_report(app))
    assert len(run['stages'])==6 and run['semantic_fields']
    assert all(row['approved'] is False for row in run['semantic_fields'])
    report=(folder/'report.md').read_text(encoding='utf-8')
    assert 'Proposed meaning' in report and 'unresolved' in report and '**unapproved**' in report


def test_native_grammar_rejects_injected_or_out_of_package_references():
    from diamond_mcp.offline.grammar import compile_grammar
    from diamond_mcp.offline.runtime import SCHEMA
    from diamond_mcp.offline.fields import annotation_schema
    with pytest.raises(ValueError,match='generated evidence'):
        compile_grammar(SCHEMA,['E1" | malicious'])
    with pytest.raises(ValueError,match='reference budget'):
        compile_grammar(SCHEMA,['E'+str(i) for i in range(65)])
    schema=annotation_schema([dict(ref='E1')],[dict(ref='E1')])
    with pytest.raises(ValueError,match='outside the evidence'):
        compile_grammar(schema,['E2'])


def test_explicit_uncertainty_is_not_a_hypothesis():
    from diamond_mcp.offline.fields import validate_annotations
    fields=[dict(ref='E1',metadata=dict(name='X17'))]
    raw=dict(annotations=[dict(ref='E1',meaning='unresolved',status='hypothesis',citations=['E1'])])
    row=validate_annotations(raw,fields,fields)[0]
    assert row['status']=='unresolved' and row['origin']=='consistency_guard' and not row['approved']
    raw['annotations'][0]['status']=[]
    with pytest.raises(ValueError):validate_annotations(raw,fields,fields)


def test_distinct_explicit_definitions_require_review_without_merging_entities():
    from diamond_mcp.offline.fields import validate_annotations
    first=dict(ref='E1',metadata=dict(name='C_ID',entity='Customer',definition='Customer identifier'))
    other=dict(ref='E2',metadata=dict(name='C_ID',entity='Customer',definition='Contract identifier'))
    raw=dict(annotations=[dict(ref='E1',meaning='Customer identifier',status='hypothesis',citations=['E1'])])
    row=validate_annotations(raw,[first],[first,other])[0]
    assert row['meaning']=='ambiguous' and row['status']=='unresolved' and set(row['citations'])=={'E1','E2'}
    assert row['rejected_model_proposal']['meaning']=='Customer identifier'
    other['metadata']['entity']='Contract'
    assert validate_annotations(raw,[first],[first,other])[0]['status']=='hypothesis'


def test_candidate_meanings_retain_unresolved_and_do_not_change_default_schema():
    from diamond_mcp.offline.fields import meaning_candidates,annotation_schema
    from diamond_mcp.offline.grammar import compile_grammar
    values=meaning_candidates(['customer_identifier','contract_identifier'])
    assert values==['customer_identifier','contract_identifier','unresolved','ambiguous']
    with pytest.raises(ValueError):meaning_candidates(['x']*31)
    schema=annotation_schema([dict(ref='E1')],[dict(ref='E1')])
    assert 'enum' not in schema['properties']['annotations']['items']['properties']['meaning']
    schema['properties']['annotations']['items']['properties']['meaning']['enum']=values
    assert 'customer_identifier' in compile_grammar(schema,['E1'])
