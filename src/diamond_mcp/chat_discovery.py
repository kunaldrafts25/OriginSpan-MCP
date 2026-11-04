"""Topic-focused application evidence for conversational clients, not viewer payloads."""
from __future__ import annotations
from collections import defaultdict
import json
import re
from sqlalchemy import select,func
from .catalog import chunks,units,files,tasks,jobs
from .corpus import normalize
from .discovery_dossier import facts_for,location

TOPICS=('overview','architecture','fields','sql','flows','handoff','coverage')


def purpose(engine,project,app,analysis,limit=3,offset=0):
    with engine.store.engine.connect() as conn:
        scope=engine.catalog.scope(chunks,project,app,analysis)
        root=conn.execute(select(chunks.c.file).where(scope,chunks.c.kind=='dictionary_context',
            func.lower(chunks.c.file).like('%readme.md')).order_by(func.length(chunks.c.file),chunks.c.file).limit(1)).scalar()
        if not root:
            return {'items':[],'total':0,'next_offset':None}
        # A root document has a bounded extraction size; don't page through every document.
        rows=[json.loads(raw) for raw in conn.execute(select(chunks.c.payload).where(scope,
            chunks.c.kind=='dictionary_context',chunks.c.file==root).limit(100)).scalars()]
    rows.sort(key=lambda row:row['line'])
    # A chat overview needs opening prose, not README badge URLs, comments or a
    # partially cut Markdown table. Full documentation remains separately searchable.
    first=rows[0] if rows else None
    text=first.get('description','') if first else ''
    text=re.sub(r'<!--[\s\S]*?-->','',text)
    text=re.sub(r'!\[[^\]]*\]\([^)]*\)','',text)
    text=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',text)
    candidates=[]
    for paragraph in re.split(r'\n\s*\n',text):
        if '```' in paragraph:
            continue
        prose=' '.join(line for line in paragraph.splitlines() if not line.lstrip().startswith(('#','|')))
        value=' '.join(prose.split())
        if len(value.split())<4 or value.startswith(('[!','<')) or 'shields.io' in value:
            continue
        if len(value)>800:
            value=value[:780].rsplit(' ',1)[0]+'…'
        candidates.append(dict(title=first['name'],definition=value,evidence=location(first)))
    candidates=candidates[:3]
    return dict(items=candidates[offset:offset+limit],total=len(candidates),
                next_offset=offset+limit if offset+limit<len(candidates) else None)


def coverage_summary(engine,project,app,analysis):
    with engine.store.engine.connect() as conn:
        scope=engine.catalog.scope(files,project,app,analysis)
        states=dict(conn.execute(select(files.c.state,func.count()).where(scope).group_by(files.c.state)).all())
        dead=conn.execute(select(func.count()).select_from(tasks).where(engine.catalog.scope(tasks,project,app,analysis),tasks.c.state=='DEAD')).scalar()
        total_notes=conn.execute(select(func.count()).select_from(units).where(engine.catalog.scope(units,project,app,analysis),units.c.kind=='coverage')).scalar()
        notes=set()
        query=select(units.c.payload).where(engine.catalog.scope(units,project,app,analysis),units.c.kind=='coverage').limit(10000)
        for raw in conn.execution_options(stream_results=True,yield_per=256).execute(query).scalars():
            for note in json.loads(raw).get('limitations',[]):
                if len(notes)<8:
                    notes.add(note)
    excluded=sum(states.get(state,0) for state in ('UNSUPPORTED','RECORD_DATA_EXCLUDED','EXCLUDED'))
    return dict(indexed=states.get('INDEXED',0),excluded=excluded,
                failed=sum(n for state,n in states.items() if state not in ('INDEXED','UNSUPPORTED','RECORD_DATA_EXCLUDED','EXCLUDED')),
                dead_letters=dead,file_states=states,notes=sorted(notes),notes_scan_truncated=total_notes>10000)


def field_page(engine,project,app,analysis,limit,offset):
    with engine.store.engine.connect() as conn:
        scope=engine.catalog.scope(chunks,project,app,analysis)
        kinds=['declared_schema_field','contract_field','catalog_field']
        count=conn.execute(select(func.count()).select_from(chunks).where(scope,chunks.c.kind.in_(kinds))).scalar()
        if not count:
            kinds=['orm_column']
            count=conn.execute(select(func.count()).select_from(chunks).where(scope,chunks.c.kind.in_(kinds))).scalar()
        rows=[json.loads(raw) for raw in conn.execute(select(chunks.c.payload).where(scope,chunks.c.kind.in_(kinds))
            .order_by(chunks.c.entity,chunks.c.name,chunks.c.id).limit(limit).offset(offset)).scalars()]
        for row in rows:
            predicate=[scope,chunks.c.kind=='field_usage',chunks.c.alias==normalize(row['name'])]
            row['usage_count']=conn.execute(select(func.count()).select_from(chunks).where(*predicate)).scalar()
            row['usage_candidates']=[json.loads(raw) for raw in conn.execute(select(chunks.c.payload).where(*predicate).limit(2)).scalars()]
    return [dict(id=row['id'],name=row['name'],entity=row.get('entity'),data_type=row.get('data_type'),
                 kind=row['kind'],evidence=location(row),usage_count=row['usage_count'],
                 usage_candidates=[location(use) for use in row['usage_candidates']]) for row in rows],count


def select_application(engine,actor,project,name):
    actor.require(project,'read')
    apps=engine.applications(actor,project)
    matches=[app for app in apps if app['name'].casefold()==name.strip().casefold()]
    if len(matches)!=1:
        raise ValueError('Choose one exact authorized application name from list_applications; the name was missing or ambiguous')
    app=matches[0]
    engine.app(actor,project,app['id'])
    with engine.store.engine.connect() as conn:
        latest=conn.execute(select(jobs.c.id).where(jobs.c.project==project,jobs.c.app==app['id'],
            jobs.c.state=='COMPLETED').order_by(jobs.c.created.desc()).limit(1)).scalar()
    if not latest:
        raise ValueError('No completed analysis is available. Import approved sources, start_analysis and poll get_analysis first')
    return app,latest


def report(engine,actor,project,app,analysis,topic='overview',limit=12,offset=0,question=''):
    job=engine.ready(actor,project,app,analysis)
    application=engine.app(actor,project,app)
    if topic not in TOPICS or not 1<=limit<=25 or offset<0 or len(question)>2000:
        raise ValueError('Choose a supported topic, limit 1..25, nonnegative offset and a question up to 2000 characters')
    result=dict(presentation='chat_report',application=application['name'],application_id=app,analysis_id=analysis,
        topic=topic,scope_note='Source/configuration evidence; no live records or runtime execution.',
        next_actions=[],limitations=[])
    def next_action(name,at):
        result['next_actions'].append(f'discover_application(project_id="{project}", application_name="{application["name"]}", topic="{name}", offset={at}, limit={limit})')
    if topic=='overview':
        result['purpose']=purpose(engine,project,app,analysis,limit=2)['items']
    facts,fact_total=facts_for(engine,project,app,analysis)
    if fact_total>len(facts):
        result['limitations'].append('Overview fact scan is bounded; use targeted sections for the remainder.')
    if topic in ('overview','architecture'):
        architecture=engine.architecture(actor,project,app,analysis,limit=100)
        dependencies=architecture.get('resolved_service_dependencies',[])
        roots=sorted((node['id'] for node in architecture.get('nodes',[]) if node.get('kind')=='service'),key=len,reverse=True)
        def owner(file):
            root=next((root for root in roots if root!='.' and file.startswith(root+'/')),None)
            return root.split('/')[-1] if root else None
        table_links={}
        for fact in facts:
            if fact['kind']=='orm_operation' and fact.get('table') and owner(fact['file']):
                pair=(owner(fact['file']),fact['table'])
                table_links[pair]=dict(source=pair[0],target='table:'+pair[1],evidence=[location(fact)])
        query_rows=engine.catalog.unit_page(project,app,analysis,'sql',limit=1000)
        for query in query_rows:
            for table in query.get('analysis',{}).get('tables',[]):
                source=owner(query['file'])
                if source:
                    table_links[(source,table)]=dict(source=source,target='table:'+table,evidence=[location(query)])
        links=dependencies+list(table_links.values())
        names=sorted({edge[k] for edge in links for k in ('source','target')})
        identifiers={name:'n'+str(index) for index,name in enumerate(names)}
        diagram=['flowchart LR']
        for name,id_ in identifiers.items():
            diagram.append(f'  {id_}["{re.sub(r"[^\w .:/-]", "", name)[:100]}"]')
        for edge in dependencies:
            diagram.append(f"  {identifiers[edge['source']]} --> {identifiers[edge['target']]}")
        for edge in table_links.values():
            diagram.append(f"  {identifiers[edge['source']]} -. schema reference .-> {identifiers[edge['target']]}")
        result['architecture']=dict(mermaid='\n'.join(diagram),dependencies=links[:25],
            resolved_service_dependencies=dependencies,
            notes=['Solid links have source and deployment configuration evidence. Dashed table links are static schema references, not observed database traffic.'])
        endpoints=engine.discovery_section(actor,project,app,analysis,'apis',limit=50)['items']
        grouped=defaultdict(list)
        for endpoint in endpoints:
            group=owner(endpoint['evidence']['file']) or 'application'
            grouped[group].append(endpoint)
        result['api_groups']=[dict(service=name,routes=[item['name'] for item in items],
            route_evidence=items,evidence=items[0]['evidence']) for name,items in grouped.items()]
        if len(endpoints)==50:
            result['limitations'].append('API overview is bounded at 50 endpoints; use the apis section for the remaining inventory.')
    if topic in ('overview','fields','handoff'):
        # Include all small schemas in an overview; never silently suppress unknown names.
        field_limit=40 if topic=='overview' else limit
        result['fields'],total=field_page(engine,project,app,analysis,field_limit,offset)
        result['field_page']=dict(total=total,offset=offset,returned=len(result['fields']),
            next_offset=offset+field_limit if offset+field_limit<total else None)
        if result['field_page']['next_offset'] is not None:
            next_action('fields',result['field_page']['next_offset'])
        forms=[f for f in facts if f['kind']=='form_field']
        for field in result['fields']:
            field['source_labels']=[dict(label=form['label'],binding=form.get('label_binding'),evidence=location(form)) for form in forms
                if form.get('label') and normalize(form['name'])==normalize(field['name'])][:2]
        form_aliases={normalize(f['name']) for f in forms[:128]}
        declared=set()
        with engine.store.engine.connect() as conn:
            declared=set(conn.execute(select(chunks.c.alias).where(engine.catalog.scope(chunks,project,app,analysis),
                chunks.c.kind.in_(['declared_schema_field','orm_column']),chunks.c.alias.in_(form_aliases))).scalars())
        result['interface_gaps']=[f for f in forms if normalize(f['name']) not in declared][:12]
        if question.strip():
            context=engine.context(actor,project,app,analysis,question,limit=6)
            result['definitions']=[dict(definition=item.get('description','')[:500],evidence=location(item))
                for item in context.get('evidence',[]) if item.get('kind') in ('dictionary_context','contract_field','catalog_field')]
    if topic in ('overview','flows'):
        kinds={'transformation','field_assignment'}
        all_flows=[fact for fact in facts if fact['kind'] in kinds]
        # Deduplicate repeated library operations at the same site.
        distinct={(f['name'],f['file'],f['line']):f for f in all_flows}
        flows=list(distinct.values())
        all_flow_total=len(flows)
        if topic=='overview':
            operations={}
            def support_file(flow):
                parts=flow['file'].lower().split('/')
                return any(part in ('tests','test','fixtures','examples','.github','loadgenerator') or part.startswith('test_') or part.endswith('_test.py') for part in parts)
            for flow in sorted(flows,key=lambda f:(support_file(f),str(f.get('operation','')).endswith('gensalt'))):
                if flow['kind']=='transformation':
                    if flow['name']=='numeric_scaling' and all(value=='?' for value in flow.get('inputs',[])):
                        continue
                    operations.setdefault(flow['name'],flow)
            assignments=sorted((f for f in flows if f['kind']=='field_assignment'),
                key=lambda f:(support_file(f),f.get('operation')!='concatenation'))
            flows=list(operations.values())+assignments[:3]
        result['transformations']=flows[offset:offset+limit]
        result['flow_page']=dict(total=all_flow_total,offset=offset,representative=topic=='overview',
            returned=len(result['transformations']),next_offset=offset+limit if topic!='overview' and offset+limit<len(flows) else None)
        if topic=='overview' and all_flow_total>len(result['transformations']):
            next_action('flows',0)
        elif result['flow_page']['next_offset'] is not None:
            next_action('flows',result['flow_page']['next_offset'])
    if topic in ('overview','sql'):
        queries=engine.sql_inventory(actor,project,app,analysis,limit=min(limit,8),offset=offset)
        result['queries']=[{key:query.get(key) for key in ('id','file','line','language','status','resolution','tables')} for query in queries['queries']]
        if queries.get('next_offset') is not None:
            next_action('sql',queries['next_offset'])
    if topic in ('overview','handoff'):
        entities=sorted({field.get('entity') for field in result.get('fields',[]) if field.get('entity')})
        result['handoff']=['Source entities in this page: '+', '.join(entities),
            'Use cited source fields and relationships as candidate inputs; no target schema or approved mapping is assumed.',
            'Confirm the approved C360 schema, identifiers, field eligibility, deduplication and transformation rules before approval.']
        result['c360_handoff']={'status':'DRAFT_REQUIRES_REVIEW','mappings':[],
            'source_application':app,'source_analysis':analysis}
    if topic in ('overview','coverage'):
        result['coverage']=coverage_summary(engine,project,app,analysis)
        result['coverage']['notes']=result['coverage']['notes'][:3 if topic=='overview' else 8]
    result['navigation']={'topics':list(TOPICS),'instruction':'Overview is sufficient for a broad explanation; expand only topics requiring detail. Do not repeat identical purpose/documentation or normal-file coverage pages.'}
    result['limitations'].append('Field usage by matching identifier is a candidate association. Source labels and declarations are facts; inferred business meanings and C360 mappings need separate support.')
    return result
