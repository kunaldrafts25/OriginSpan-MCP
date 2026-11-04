import json
from pathlib import Path
import pytest
from diamond_mcp.discovery_facts import extract_facts, deployment_facts, yaml_documents
from diamond_mcp.discovery_dossier import resolve_jpql
from diamond_mcp.engine import Config, Actor
from diamond_mcp.service import DiscoveryService
from diamond_mcp.catalog import jobs
from sqlalchemy import select


def test_framework_facts_keep_structure_and_discard_record_literals():
    source='''raise RuntimeError("NEVER EXECUTE")
class Store:
    def __init__(self):
        self.people = Table("people", metadata, Column("dob", Date))
    def save(self, person):
        statement = self.people.insert().values(person)
        statement = self.people.select().where(self.people.c.dob == requested)
        password = bcrypt.hashpw(secret, bcrypt.gensalt())
        cents = int(Decimal(amount) * 100)
        payload = {"dob": "PRIVATE_RECORD_VALUE"}
        payload["displayName"] = labels.get(person["id"])
        host = os.environ.get("HISTORY_API_ADDR")
'''
    facts=extract_facts(source,'store.py')
    assert 'PRIVATE_RECORD_VALUE' not in json.dumps(facts)
    assert any(f['kind']=='orm_operation' and f.get('table')=='people' and f['name']=='insert' for f in facts)
    assert any('self.people.c.dob = requested' in str(f.get('predicates')) for f in facts)
    assert {f['name'] for f in facts if f['kind']=='transformation'} >= {'password_hashing','numeric_scaling','lookup_enrichment'}
    assert any(f['kind']=='field_usage' and f['name']=='dob' for f in facts)


def test_multidocument_yaml_and_secret_config_exclusion():
    source='''kind: ConfigMap
metadata:
  name: apis
data:
  HISTORY_API_ADDR: history:8080
  PASSWORD: PRIVATE_PASSWORD
---
kind: Deployment
metadata:
  name: web
spec:
  template:
    spec:
      containers:
      - name: web
        envFrom:
        - configMapRef:
            name: apis
'''
    assert len(yaml_documents(source))==2
    facts=deployment_facts(source,'deploy.yaml')
    assert any(f['kind']=='configured_address' and f['target']=='history' for f in facts)
    assert any(f['kind']=='config_consumer' and f['config']=='apis' for f in facts)
    assert 'PRIVATE_PASSWORD' not in json.dumps(facts)


def test_explicit_jpa_mapping_across_intervening_annotations():
    source='''@Entity
@Table(name="PAYMENTS")
public class Payment {
 @Column(name="PAYMENT_ID", nullable=false)
 @JsonProperty("paymentId")
 private long paymentId;
 @Column(name="AMOUNT")
 private long amount;
}'''
    facts=extract_facts(source,'Payment.java')
    assert any(f['name']=='paymentId' and f.get('column')=='PAYMENT_ID' for f in facts)
    result=resolve_jpql('SELECT p FROM Payment p WHERE p.paymentId > :last ORDER BY p.paymentId',facts)
    assert result and result['resolution']=='EXPLICIT_JPA_MAPPING'
    assert 'PAYMENTS' in result['metadata_sql'] and 'PAYMENT_ID' in result['metadata_sql']
    assert 'PAYMENT_ID' in str(result['analysis']['predicate_dependencies']).upper()
    assert resolve_jpql('SELECT p FROM Payment p WHERE p.unknown = :x',facts) is None
    conflicting=facts+[dict(kind='orm_column',framework='jpa',entity='Payment',name='paymentId',table='OTHER',column='ID',file='other.java',line=2)]
    assert resolve_jpql('SELECT p FROM Payment p',conflicting) is None
    assert not extract_facts('// @Entity @Table(name="FAKE") class Fake {}','Comment.java')


def test_deployment_bindings_have_source_and_consumer_evidence(tmp_path,monkeypatch):
    from diamond_mcp.discovery_dossier import topology
    class Catalog:
        def unit_page(self,*args,**kwargs):
            return [dict(type='SERVICE_MANIFEST',name='src\\web')]
    class Engine:
        catalog=Catalog()
    facts=[dict(kind='environment_reference',name='HISTORY_API_ADDR',file='src/web/api.py',line=9),
           dict(kind='configured_address',name='HISTORY_API_ADDR',target='history',config='apis',file='config.yaml',line=5),
           dict(kind='config_consumer',name='web',config='apis',file='web.yaml',line=12),
           dict(kind='service_declaration',name='history',file='history.yaml',line=3)]
    base=dict(nodes=[dict(id='src/web'),dict(id='src/history')],edges=[],mermaid='flowchart LR')
    result=topology(Engine(),'p','a','j',facts,base)
    assert len(result['resolved_service_dependencies'])==1
    assert len(result['resolved_service_dependencies'][0]['evidence'])==3
    assert result['edges'][0]['source']=='src/web'
    conflicting=facts+[dict(kind='configured_address',name='HISTORY_API_ADDR',target='other',config='apis',file='variant.yaml',line=5)]
    assert topology(Engine(),'p','a','j',conflicting,base)['resolved_service_dependencies']==[]


def test_positional_jpql_metadata_and_invalid_yaml_are_visible(tmp_path):
    from diamond_mcp.pipeline import parse_file
    work=tmp_path/'work';work.mkdir()
    java=tmp_path/'query.java'
    java.write_text('''interface Repo {
 @Query("SELECT p FROM Payment p WHERE p.paymentId > ?1")
 List<Payment> find(long id);
}''')
    result=parse_file(java,'Repo.java',work)
    assert result['sql'][0]['metadata_expression'] is not None
    broken=tmp_path/'bad.yaml';broken.write_text('kind: [unterminated')
    result=parse_file(broken,'deploy.yaml',work)
    assert result['gaps']


def build(tmp_path,monkeypatch):
    monkeypatch.setenv('DIAMOND_EXTERNAL_WORKERS','true')
    source=tmp_path/'source';source.mkdir()
    (source/'Dockerfile').write_text('FROM python:3.13\n')
    (source/'README.md').write_text('# People application\nA registry for people and their declared date of birth.\n')
    (source/'schema.sql').write_text('CREATE TABLE people (\n  id INT,\n  X17 DATE\n);\n')
    (source/'dictionary.md').write_text('X17 means the birth date of the registered person.\n')
    (source/'store.py').write_text('''raise RuntimeError("MUST NOT EXECUTE")
class Store:
 def setup(self):
  self.people = Table("people", meta, Column("X17", Date))
 def find(self, values):
  query=self.people.select().where(self.people.c.X17 == requested)
  return values["X17"]
''')
    engine=DiscoveryService(Config(tmp_path/'state',(source,)))
    actor=Actor('test',frozenset({'p'}))
    app=engine.register(actor,'p','Registry',source)['id']
    job=engine.start(actor,'p',app)['id']
    for _ in range(30):
        engine.pipeline.step('worker',job,isolate=False)
        if engine.job(actor,'p',app,job)['status'] not in ('QUEUED','RUNNING'):
            break
    assert engine.job(actor,'p',app,job)['status']=='COMPLETED'
    return engine,actor,app,job


def test_dossier_fields_usage_line_precision_pagination_and_permissions(tmp_path,monkeypatch):
    engine,actor,app,job=build(tmp_path,monkeypatch)
    dossier=engine.complete_discovery(actor,'p',app,job)
    assert len(json.dumps(dossier).encode())<100000
    assert dossier['c360_handoff']['status']=='DRAFT_REQUIRES_REVIEW'
    page=engine.discovery_section(actor,'p',app,job,'fields',limit=1)
    assert page['next_offset']==1
    fields=engine.discovery_section(actor,'p',app,job,'fields',limit=50)['items']
    field=next(f for f in fields if f['name']=='X17' and f['kind']=='declared_schema_field')
    assert field['evidence']['line']==3
    assert field['usage_count']>=2
    assert field['interpretation']=='UNRESOLVED_UNLESS_SUPPORTED_BY_DEFINITIONS'
    docs=engine.discovery_section(actor,'p',app,job,'documentation')['items']
    assert any('birth date' in d['definition'] for d in docs)
    with pytest.raises(PermissionError):
        engine.complete_discovery(Actor('foreign',frozenset({'other'})),'p',app,job)
    with pytest.raises(ValueError,match='evidence ID'):
        engine.graph(actor,'p',app,job,focus='src/frontend')
    results=engine.search(actor,'p',app,job,'X17',excerpts=True)
    assert results['findings'] and all('excerpt' not in f for f in results['findings'])
    assert any('not permitted' in limitation for limitation in results['limitations'])


def test_old_snapshots_surface_reanalysis_requirement(tmp_path,monkeypatch):
    engine,actor,app,job=build(tmp_path,monkeypatch)
    from diamond_mcp.catalog import units
    with engine.store.engine.begin() as conn:
        conn.execute(units.delete().where(units.c.kind.startswith('discovery',autoescape=True)))
    dossier=engine.complete_discovery(actor,'p',app,job)
    assert dossier['coverage']['facts']==0
    assert any('reanalyzed' in value for value in dossier['limitations'])
