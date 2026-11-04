"""Bounded, evidence-first discovery packages for the customer's own AI."""
from __future__ import annotations

from collections import defaultdict
import json
import re
from sqlalchemy import select, func, or_
from .catalog import chunks, units, files
from .sources import redact

SECTIONS = ('purpose','apis','fields','orm','transformations','documentation','questions')


def location(item):
    return {'file': item['file'], 'line': item.get('line',1)}


def facts_for(engine, project, app, analysis, ceiling=20000, kinds=None):
    with engine.store.engine.connect() as conn:
        scope=engine.catalog.scope(units,project,app,analysis)
        predicate=or_(units.c.kind=='discovery', units.c.kind.in_(['discovery_'+kind for kind in kinds]) if kinds
                      else units.c.kind.startswith('discovery_',autoescape=True))
        count=conn.execute(select(func.count()).select_from(units).where(scope,predicate)).scalar()
        query=select(units.c.payload).where(scope,predicate).order_by(units.c.file,units.c.id).limit(ceiling)
        facts=[json.loads(raw) for raw in conn.execute(query).scalars()]
    return facts,count


def resolve_jpql(expression, facts):
    """Translate only single-entity queries with consistent explicit column maps."""
    import sqlglot
    from sqlglot import exp
    from .sql_intelligence import analyze_sql
    if not expression:
        return None
    mapping=defaultdict(dict)
    ambiguous=set()
    for fact in facts:
        if fact.get('kind')=='orm_column' and fact.get('framework')=='jpa':
            value=(fact['table'],fact['column'])
            old=mapping[fact['entity']].get(fact['name'])
            if old and old!=value:
                ambiguous.add(fact['entity'])
            mapping[fact['entity']][fact['name']]=value
    try:
        tree=sqlglot.parse_one(expression)
        tables=list(tree.find_all(exp.Table))
        if not isinstance(tree,exp.Select) or len(tables)!=1 or list(tree.find_all(exp.Join)) or list(tree.find_all(exp.Subquery)):
            return None
        table=tables[0]
        entity=table.name
        bindings=mapping.get(entity,{})
        physical={value[0] for value in bindings.values()}
        if not bindings or entity in ambiguous or len(physical)!=1:
            return None
        alias=table.alias_or_name
        table_name=next(iter(physical))
        for column in list(tree.find_all(exp.Column)):
            if column.name==alias and not column.table:
                column.replace(exp.Column(this=exp.Star(),table=alias))
                continue
            if column.table and column.table!=alias:
                return None
            if column.name not in bindings:
                return None
            column.set('this',exp.to_identifier(bindings[column.name][1]))
        table.set('this',exp.to_identifier(table_name))
        schema={table_name:{value[1]:'UNKNOWN' for value in bindings.values()}}
        sql=tree.sql(dialect='postgres')
        result=analyze_sql(sql,schema=schema)
        result['limitations'].append('Single-entity JPQL translated from explicit static JPA mappings; no runtime ORM behavior verified')
        return {'metadata_sql':sql,'analysis':result,'resolution':'EXPLICIT_JPA_MAPPING',
                'mapping_evidence':[location(f) for f in facts if f.get('entity')==entity and f['kind']=='orm_column'][:20]}
    except (ValueError, sqlglot.errors.SqlglotError):
        return None


def topology(engine, project, app, analysis, facts, architecture):
    """Connect env references only where deployment config and source ownership agree."""
    manifests=engine.catalog.unit_page(project,app,analysis,'architecture',limit=10000)
    roots=sorted({u['name'].replace('\\','/') for u in manifests if u['type']=='SERVICE_MANIFEST'},key=len,reverse=True)
    def owner(file):
        root=next((r for r in roots if r!='.' and file.startswith(r+'/')),None)
        return root.split('/')[-1] if root else None
    addresses=defaultdict(list)
    consumers=defaultdict(set)
    consumer_evidence=defaultdict(list)
    declarations={f['name'] for f in facts if f['kind']=='service_declaration'}
    for fact in facts:
        if fact['kind']=='configured_address':
            addresses[fact['name']].append(fact)
        elif fact['kind']=='config_consumer':
            consumers[fact['name']].add(fact['config'])
            consumer_evidence[(fact['name'],fact['config'])].append(location(fact))
    links={}
    for fact in facts:
        if fact['kind']!='environment_reference':
            continue
        source=owner(fact['file'])
        if not source:
            continue
        candidates=[candidate for candidate in addresses[fact['name']] if candidate['config'] in consumers[source]]
        targets={candidate['target'].split('.')[0] for candidate in candidates}
        # Variant deployments with conflicting ConfigMaps cannot prove one destination.
        if len(targets)!=1:
            continue
        target=next(iter(targets))
        if target not in declarations or target==source:
            continue
        candidate=candidates[0]
        key=(source,target)
        links[key]=dict(source=source,target=target,kind='configured_service_call',status='STATIC_CONFIG_BINDING',
                        variable=fact['name'],evidence=[location(fact),location(candidate)]+consumer_evidence[(source,candidate['config'])][:1])
    result=dict(architecture)
    result['resolved_service_dependencies']=list(links.values())
    if links:
        # Preserve service-root IDs used by the viewer. A leaf name alone is ambiguous
        # when multiple services share it, so only unique root names are bound.
        groups=defaultdict(list)
        for node in architecture.get('nodes',[]):
            groups[node['id'].replace('\\','/').split('/')[-1]].append(node['id'])
        merged=list(architecture.get('edges',[]))
        for link in links.values():
            if len(groups[link['source']])==1 and len(groups[link['target']])==1:
                merged.append({**link,'source':groups[link['source']][0],'target':groups[link['target']][0],
                    'supporting_evidence':link['evidence'],'evidence':link['evidence'][0]})
        result['edges']=merged
        # This diagram contains only corroborated configured edges; full static graph is retained separately.
        names=sorted({name for pair in links for name in pair})
        identifiers={name:'s'+str(i) for i,name in enumerate(names)}
        diagram=['flowchart LR']
        for name,id_ in identifiers.items():
            diagram.append(f'  {id_}["{re.sub(r"[^\w .:/-]", "", name)[:120]}"]')
        for source,target in links:
            diagram.append(f'  {identifiers[source]} --> {identifiers[target]}')
        result['configured_services_mermaid']='\n'.join(diagram)
        all_names=sorted({n['id'] for n in architecture.get('nodes',[])} |
                         {e[k] for e in merged for k in ('source','target')})
        ids={name:'n'+str(i) for i,name in enumerate(all_names)}
        combined=['flowchart LR']
        for name,id_ in ids.items():
            label=re.sub(r'[^\w .:/-]','',name)[:150]
            combined.append(f'  {id_}["{label}"]')
        for edge in merged[:500]:
            arrow='-.->' if edge.get('status')=='CANDIDATE' else '-->'
            combined.append(f"  {ids[edge['source']]} {arrow} {ids[edge['target']]}")
        result['mermaid']='\n'.join(combined)
    return result


def section(engine,actor,project,app,analysis,name,limit=12,offset=0,_facts=None):
    engine.ready(actor,project,app,analysis)
    if name not in SECTIONS or not 1<=limit<=50 or offset<0:
        raise ValueError('Choose a supported discovery section, limit 1..50 and nonnegative offset')
    if name=='purpose':
        from .chat_discovery import purpose
        return dict(section=name,**purpose(engine,project,app,analysis,limit,offset),limitations=['Primary application documentation; not the entire documentation corpus'])
    facts,fact_total=_facts if _facts is not None else facts_for(engine,project,app,analysis)
    if name=='fields':
        with engine.store.engine.connect() as conn:
            scope=engine.catalog.scope(chunks,project,app,analysis)
            predicate=chunks.c.kind.in_(['declared_schema_field','contract_field','catalog_field','orm_column'])
            total=conn.execute(select(func.count()).select_from(chunks).where(scope,predicate)).scalar()
            query=select(chunks.c.payload).where(scope,predicate).order_by(chunks.c.file,chunks.c.name,chunks.c.id).limit(limit).offset(offset)
            rows=[json.loads(raw) for raw in conn.execute(query).scalars()]
        values=[]
        for row in rows:
            from .corpus import normalize
            with engine.store.engine.connect() as conn:
                usage_scope=engine.catalog.scope(chunks,project,app,analysis)
                matches=[usage_scope,chunks.c.kind=='field_usage',chunks.c.alias==normalize(row['name'])]
                usage_count=conn.execute(select(func.count()).select_from(chunks).where(*matches)).scalar()
                uses=[json.loads(raw) for raw in conn.execute(select(chunks.c.payload).where(*matches)
                     .order_by(chunks.c.file,chunks.c.id).limit(6)).scalars()]
            if not uses:
                uses=[f for f in facts if f['kind']=='field_usage' and f['name'].casefold()==row['name'].casefold()]
                usage_count=len(uses)
            values.append(dict(id=row['id'],name=row['name'],entity=row.get('entity'),kind=row['kind'],
                data_type=row.get('data_type'),evidence=location(row),description=row.get('description','')[:400],
                interpretation='UNRESOLVED_UNLESS_SUPPORTED_BY_DEFINITIONS',usage_count=usage_count,
                usage_candidates=[{**location(u),'function':u.get('function'),'role':u.get('role')} for u in uses[:6]],
                usage_binding='Same identifier is a candidate association, not proved entity binding'))
    elif name=='apis':
        with engine.store.engine.connect() as conn:
            scope=engine.catalog.scope(chunks,project,app,analysis)
            predicate=chunks.c.kind=='api_endpoint'
            total=conn.execute(select(func.count()).select_from(chunks).where(scope,predicate)).scalar()
            values=[json.loads(raw) for raw in conn.execute(select(chunks.c.payload).where(scope,predicate)
                    .order_by(chunks.c.file,chunks.c.id).limit(limit).offset(offset)).scalars()]
        values=[dict(id=value['id'],name=value['name'],evidence=location(value),kind=value['kind']) for value in values]
    elif name in ('purpose','documentation'):
        with engine.store.engine.connect() as conn:
            scope=engine.catalog.scope(chunks,project,app,analysis)
            predicate=chunks.c.kind=='dictionary_context'
            total=conn.execute(select(func.count()).select_from(chunks).where(scope,predicate)).scalar()
            query=select(chunks.c.payload).where(scope,predicate).order_by(func.length(chunks.c.file),chunks.c.file,chunks.c.id).limit(limit).offset(offset)
            rows=[json.loads(raw) for raw in conn.execute(query).scalars()]
        values=[dict(id=row['id'],title=row['name'],evidence=location(row),definition=redact(row.get('description',''))[:1400],
                     status='DOCUMENTED_CLAIM_NOT_RUNTIME_PROOF') for row in rows]
    elif name=='questions':
        values=[dict(question='Which application fields map to approved C360 concepts?',status='HUMAN_REVIEW_REQUIRED'),
                dict(question='Which unfamiliar abbreviations need dictionary or owner confirmation?',status='UNRESOLVED'),
                dict(question='Do deployment variants or dynamic behavior change these source relationships?',status='RUNTIME_NOT_OBSERVED')]
        total=len(values);values=values[offset:offset+limit]
    else:
        kinds={'orm_table','orm_column','orm_entity','orm_operation'} if name=='orm' else {'transformation'}
        with engine.store.engine.connect() as conn:
            scope=engine.catalog.scope(units,project,app,analysis)
            predicate=units.c.kind.in_(['discovery_'+kind for kind in kinds])
            total=conn.execute(select(func.count()).select_from(units).where(scope,predicate)).scalar()
            values=[json.loads(raw) for raw in conn.execute(select(units.c.payload).where(scope,predicate)
                    .order_by(units.c.file,units.c.id).limit(limit).offset(offset)).scalars()]
        if not total:
            selected=[f for f in facts if f['kind'] in kinds]
            total=len(selected)
            values=selected[offset:offset+limit]
    return dict(section=name,items=values,total=total,next_offset=offset+limit if offset+limit<total else None,
                fact_scan=dict(total=fact_total,scanned=len(facts),truncated=fact_total>len(facts)),
                limitations=['Static source facts and candidate associations; no records or source excerpts returned'])


def complete(engine,actor,project,app,analysis,section_limit=8):
    job=engine.ready(actor,project,app,analysis)
    if not 1<=section_limit<=12:
        raise ValueError('section_limit must be 1..12; expand individual sections for more detail')
    facts,total=facts_for(engine,project,app,analysis)
    full_architecture=engine.architecture(actor,project,app,analysis,limit=30)
    selected_edges=[e for e in full_architecture['edges'] if e['kind']!='configured_candidate'][:30]
    architecture={**full_architecture,'edges':selected_edges,
        'nodes':[{**node,'evidence':node.get('evidence',[])[:1]} for node in full_architecture['nodes']],
        'mermaid':full_architecture.get('configured_services_mermaid',full_architecture['mermaid'])[:8000],
        'omitted_edges':len(full_architecture['edges'])-len(selected_edges),
        'expand_tool':'get_architecture'}
    architecture.pop('configured_services_mermaid',None)
    sections={name:section(engine,actor,project,app,analysis,name,min(section_limit,3) if name in ('purpose','documentation') else section_limit,
                           _facts=(facts,total)) for name in SECTIONS}
    for name in ('purpose','documentation'):
        for item in sections[name]['items']:
            item['definition']=item['definition'][:900]
    sql=engine.sql_inventory(actor,project,app,analysis,limit=section_limit)
    sql['queries']=[{**query,'mapping_evidence':query.get('mapping_evidence',[])[:2],
                    'value_lineage':query.get('value_lineage',[])[:2]} for query in sql['queries']]
    with engine.store.engine.connect() as conn:
        states=dict(conn.execute(select(files.c.state,func.count())
                  .where(engine.catalog.scope(files,project,app,analysis))
                  .group_by(files.c.state)).all())
    result = dict(summary='Evidence dossier for host-AI discovery; interpretations and C360 mappings remain reviewable',
        application_id=app,analysis_id=analysis,analysis_status=job['status'],extraction_version=job.get('index_version'),
        snapshot=job.get('snapshot'),evidence_count=job.get('evidence_count'),neural=job.get('neural'),
        architecture=architecture,sections=sections,sql=sql,coverage=dict(file_states=states,facts=total,facts_scanned=len(facts)),
        c360_handoff=dict(status='DRAFT_REQUIRES_REVIEW',source_application=app,source_analysis=analysis,
            candidate_fields_section='fields',mappings=[],unresolved_questions=sections['questions']['items'],
            next_step='Host AI proposes cited mappings to the supplied target C360 schema; separate human review approves them. No target writes.'),
        continuation=dict(tool='get_discovery_section',sections=list(SECTIONS),
            sql_tool='list_sql_queries',coverage_tool='get_discovery_coverage',
            instruction='Follow next_offset until the requested scope is covered. A single user prompt may require multiple tool calls.'),
        limitations=['Source analysis cannot guarantee complete runtime discovery or business meaning',
                     'No individual records inspected; no protected traits inferred',
                     'All section limits and snapshot/version boundaries apply',
                     'Imported snapshots produced before the discovery upgrade must be reanalyzed to obtain framework facts'])
    size=len(json.dumps(result).encode())
    if size>60000 and section_limit>1:
        return complete(engine,actor,project,app,analysis,max(1,section_limit//2))
    result['response_budget']=dict(target_bytes=60000,serialized_bytes_before_budget=size,
                                  effective_section_limit=section_limit,
                                  note='Overview only. Follow all section continuations for detailed discovery.')
    return result
