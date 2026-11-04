"""Evaluate frozen banking tasks, not host-model answer quality or competitor speed."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from diamond_mcp.service import DiscoveryService, Config, Actor
from diamond_mcp.discovery_dossier import facts_for


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--state',required=True,type=Path)
    parser.add_argument('--baseline',type=Path,default=ROOT/'artifacts/production-banking.json')
    args=parser.parse_args()
    baseline=json.loads(args.baseline.read_text())
    actor=Actor('discovery-evaluation',frozenset({'bank'}))
    engine=DiscoveryService(Config(args.state))
    app,analysis=baseline['application_id'],baseline['analysis_id']
    started=time.perf_counter()
    dossier=engine.complete_discovery(actor,'bank',app,analysis)
    dossier_seconds=time.perf_counter()-started
    facts,total=facts_for(engine,'bank',app,analysis)
    sql=engine.sql_inventory(actor,'bank',app,analysis,limit=100)
    fields=engine.discovery_section(actor,'bank',app,analysis,'fields',limit=50)['items']
    orm=[f for f in facts if f['kind']=='orm_operation']
    transforms={f['name'] for f in facts if f['kind']=='transformation'}
    dependencies=dossier['architecture']['resolved_service_dependencies']
    links={(edge['source'],edge['target']) for edge in dependencies}
    expected={('frontend',name) for name in ('userservice','contacts','ledgerwriter','balancereader','transactionhistory')}
    coverage=engine.coverage(actor,'bank',app,analysis,limit=100)
    gaps=[]
    offset=0
    while True:
        page=engine.coverage(actor,'bank',app,analysis,limit=100,offset=offset)
        gaps.extend((entry['file'],gap) for entry in page.get('extractor_coverage',[]) for gap in entry.get('limitations',[]))
        if page.get('next_offset') is None:
            break
        offset=page['next_offset']
    birthday=next((f for f in fields if f['name']=='birthday' and f['kind']=='declared_schema_field'),None)
    checks={
        'preserves_39_routes_3_tables_23_columns':all(baseline[k] for k in ('routes_match','tables_match','columns_match')),
        'five_frontend_dependencies':expected<=links,
        'users_insert_and_select':{'insert','select'}<={f['name'] for f in orm if f.get('table')=='users'},
        'contacts_insert_and_select':{'insert','select'}<={f['name'] for f in orm if f.get('table')=='contacts'},
        'explicit_jpa_id_mapping':any(f['kind']=='orm_column' and f.get('entity')=='Transaction' and f['name']=='transactionId' and f.get('column')=='TRANSACTION_ID' for f in facts),
        'eight_supported_jpql_queries_resolved':len([q for q in sql['queries'] if q.get('resolution')=='EXPLICIT_JPA_MAPPING'])==8,
        'birthday_has_precise_line_and_usage':bool(birthday and birthday['evidence']['line']>20 and birthday['usage_count']>0),
        'hashing_token_scaling_and_lookup':{'password_hashing','token_construction','numeric_scaling','lookup_enrichment'}<=transforms,
        'no_kubernetes_single_document_parse_errors':not any('metadata parse failed' in gap or 'Deployment metadata could not be parsed' in gap for file,gap in gaps if file.endswith(('.yaml','.yml'))),
        'compact_dossier_under_100kb':len(json.dumps(dossier).encode())<100000,
        'c360_handoff_requires_review':dossier['c360_handoff']['status']=='DRAFT_REQUIRES_REVIEW',
    }
    report=dict(status='PASS' if all(checks.values()) else 'FAIL',checks=checks,
        check_count=len(checks),passed=sum(checks.values()),source_revision=baseline['reviewed_revision'],
        index_seconds=baseline['index_seconds'],dossier_seconds=dossier_seconds,
        dossier_bytes=len(json.dumps(dossier).encode()),facts=total,
        service_dependencies=dependencies,orm_operations=orm,transformation_kinds=sorted(transforms),
        jpql_queries=[dict(file=q['file'],line=q['line'],resolution=q.get('resolution')) for q in sql['queries'] if q['language']=='JPQL'],
        birthday=birthday,limitations=['Local Windows execution, not a Render capacity benchmark',
            'Structural regression checks, not a Copilot answer evaluation or competitor comparison'])
    destination=ROOT/'docs/validation-results/discovery-upgrade'
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'banking.json').write_text(json.dumps(report,indent=2))
    (destination/'banking-dossier.json').write_text(json.dumps(dossier,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('orm_operations','service_dependencies','birthday')},indent=2))
    raise SystemExit(0 if report['status']=='PASS' else 1)


if __name__=='__main__':
    main()
