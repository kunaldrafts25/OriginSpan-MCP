import asyncio
import json
import pytest
from diamond_mcp.engine import Config,Actor
from diamond_mcp.service import DiscoveryService
from diamond_mcp.server import create_server
from diamond_mcp.chat_output import payload,render
from diamond_mcp.discovery_facts import extract_facts


@pytest.fixture
def sample(tmp_path,monkeypatch):
    monkeypatch.setenv('DIAMOND_EXTERNAL_WORKERS','true')
    source=tmp_path/'source';source.mkdir()
    (source/'README.md').write_text('# Registry\nA person registry supporting enrollment and account management.\n')
    (source/'records.csv').write_text('PRIVATE_RECORD_VALUE\n')
    (source/'deployment.md').write_text(('## Deployment\nUnrelated infrastructure instructions.\n'*800))
    (source/'schema.sql').write_text('CREATE TABLE people (\n id INT,\n birthday DATE,\n name TEXT\n);')
    (source/'signup.html').write_text('''<form action="/signup">
<label for="country">Country</label><select id="country" name="country"><option value="PRIVATE_RECORD_VALUE">PRIVATE_OPTION</option></select>
<label for="dob">Birthday</label><input id="dob" name="birthday" type="date" value="PRIVATE_RECORD_VALUE">
</form>''')
    (source/'api.py').write_text('''raise RuntimeError("NEVER EXECUTE")
class Store:
 def setup(self):
  self.people = Table("people", metadata, Column("birthday", Date))
 def read(self, person):
  statement=self.people.select().where(self.people.c.birthday == requested)
  claim={"name":person["firstname"]+" "+person["lastname"]}
  return claim
''')
    (source/'Payment.java').write_text('''@Entity @Table(name="PAYMENTS")
class Payment {
 @Column(name="PAYMENT_ID") private long paymentId;
}''')
    (source/'Repo.java').write_text('''interface Repo {
 @Query("SELECT p FROM Payment p WHERE p.paymentId > ?1")
 List<Payment> find(long after);
}''')
    engine=DiscoveryService(Config(tmp_path/'state',(source,)))
    actor=Actor('test',frozenset({'p'}))
    app=engine.register(actor,'p','Registry',source)['id']
    job=engine.start(actor,'p',app)['id']
    for _ in range(40):
        engine.pipeline.step('test',job,isolate=False)
        if engine.job(actor,'p',app,job)['status'] not in ('QUEUED','RUNNING'):
            break
    assert engine.job(actor,'p',app,job)['status']=='COMPLETED'
    return engine,actor,app,job


def test_chat_protocol_is_markdown_with_native_structured_data(sample,monkeypatch):
    engine,actor,app,analysis=sample
    server=create_server(engine,local_actor=actor)
    async def scenario():
        context=await server.call_tool('get_session_context',{})
        assert payload(context)['data']['projects']==['p']
        result=await server.call_tool('discover_application',dict(project_id='p',application_name='Registry'))
        assert not result.is_error
        text=result.content[0].text
        assert text.startswith('# Registry discovery') and not text.startswith('{')
        assert len(text)<=18000
        assert 'ConvertFrom-Json' not in text and 'json.loads' not in text
        assert '## Fields' in text and '## Draft C360' in text and '### Sources' in text
        assert 'PRIVATE_RECORD_VALUE' not in text and 'PRIVATE_OPTION' not in text
        data=payload(result)['data']
        assert any(field['name']=='country' for field in data['interface_gaps'])
        assert data['coverage']['indexed']==7
        assert data['coverage']['failed']==0 and data['coverage']['excluded']==1
        assert data['analysis_id']==analysis
        found=await server.call_tool('search_findings',dict(project_id='p',application_id=app,analysis_id=analysis,query='birthday'))
        evidence_id=payload(found)['data']['findings'][0]['id']
        assert evidence_id in found.content[0].text
        evidence=await server.call_tool('get_evidence',dict(project_id='p',application_id=app,analysis_id=analysis,evidence_id=evidence_id))
        assert payload(evidence)['data']['name'] in evidence.content[0].text
        assert '### Sources' in evidence.content[0].text
        # Prior public tool also becomes chat-readable without changing the viewer.
        complete=await server.call_tool('get_complete_discovery',dict(project_id='p',application_id=app,analysis_id=analysis))
        assert complete.content[0].text.startswith('# Registry discovery')
        assert payload(complete)['data']['c360_handoff']['status']=='DRAFT_REQUIRES_REVIEW'
        monkeypatch.setenv('ORIGINSPAN_MCP_OUTPUT','json')
        legacy=await server.call_tool('list_applications',dict(project_id='p'))
        assert json.loads(legacy.content[0].text)['status']=='OK'
    asyncio.run(scenario())


def test_purpose_does_not_repeat_all_documentation(sample):
    engine,actor,app,analysis=sample
    purpose=engine.discovery_section(actor,'p',app,analysis,'purpose',limit=50)
    documentation=engine.discovery_section(actor,'p',app,analysis,'documentation',limit=50)
    assert purpose['total']==1 and purpose['next_offset'] is None
    assert documentation['total']>purpose['total']
    assert all(item['evidence']['file']=='README.md' for item in purpose['items'])


def test_source_query_and_hypothetical_lineage_are_distinct(sample):
    engine,actor,app,analysis=sample
    query=next(query for query in engine.sql_inventory(actor,'p',app,analysis)['queries'] if query['language']=='JPQL')
    observed=engine.source_query_lineage(actor,'p',app,analysis,query['id'][:12])
    assert observed['provenance']['kind']=='observed_source_query'
    assert observed['provenance']['file']=='Repo.java'
    with pytest.raises(ValueError,match='missing or ambiguous'):
        engine.source_query_lineage(actor,'p',app,analysis,'f'*64)
    server=create_server(engine,local_actor=actor)
    async def scenario():
        supplied=await server.call_tool('get_sql_lineage',dict(project_id='p',application_id=app,
            analysis_id=analysis,sql='SELECT birthday FROM people'))
        assert payload(supplied)['data']['provenance']['source_presence']=='NOT_ESTABLISHED'
        assert 'source occurrence not established' in supplied.content[0].text
        bound=await server.call_tool('get_sql_lineage',dict(project_id='p',application_id=app,
            analysis_id=analysis,query_id=query['id'][:12]))
        assert 'Source-query lineage' in bound.content[0].text
        denied=await server.call_tool('discover_application',dict(project_id='foreign',application_name='Registry'))
        assert denied.is_error and payload(denied)['status']=='DENIED'
    asyncio.run(scenario())


def test_form_metadata_and_concatenation_never_include_record_values():
    facts=extract_facts('<label for="x">Residence</label><input name="X17" id="x" value="DO_NOT_READ">','form.html')
    assert facts[0]['name']=='X17' and facts[0]['label']=='Residence'
    assert 'DO_NOT_READ' not in json.dumps(facts)
    flows=extract_facts('claims={"name":person["firstname"]+" "+person["lastname"]}','api.py')
    assert any(f['kind']=='field_assignment' and f['inputs']==['firstname','lastname'] and f['operation']=='concatenation' for f in flows)
    arithmetic=extract_facts('expires=now+settings["duration"]','api.py')
    assert not any(f.get('operation')=='concatenation' for f in arithmetic)


def test_markdown_budget_keeps_code_fences_balanced():
    data=dict(presentation='chat_report',application='Example',topic='overview',
              architecture={'mermaid':'flowchart LR\n'+' n0 --> n1\n'*3000})
    text=render('discover_application',{'status':'OK','data':data})
    assert len(text)<=18000 and text.count('```')%2==0
    assert 'Display budget reached' in text
