"""Readable MCP content for AI chat, separate from machine-readable evidence."""
from __future__ import annotations

import json
import re
from mcp.types import CallToolResult, TextContent

MAX_TEXT=18000


class Markdown:
    def __init__(self):
        self.lines=[]
        self.sources=[]
    def add(self,text=''):
        self.lines.append(str(text))
    def cite(self,source):
        if not isinstance(source,dict) or not source.get('file'):
            return ''
        key=(source['file'],source.get('line',1))
        if key not in self.sources:
            self.sources.append(key)
        return '[S'+str(self.sources.index(key)+1)+']'
    def table(self,headers,rows):
        self.add('| '+' | '.join(headers)+' |')
        self.add('| '+' | '.join('---' for _ in headers)+' |')
        for row in rows:
            self.add('| '+' | '.join(cell(value) for value in row)+' |')
        self.add()
    def finish(self):
        body='\n'.join(self.lines).strip()
        clipped=False
        for _ in range(12):
            used={int(match) for match in re.findall(r'\[S(\d+)\]',body)}
            source_lines=[]
            for index,(file,line) in enumerate(self.sources,1):
                if index in used:
                    shown=file if len(file)<=600 else file[:560]+'… (full path in structured evidence)'
                    source_lines.append(f'- [S{index}] `{shown}:{line}`')
            suffix='\n\n### Sources\n'+'\n'.join(source_lines) if source_lines else ''
            note='\n\nDisplay budget reached. Request a focused topic or a smaller page; do not infer omitted findings.' if clipped else ''
            closing='\n```' if body.count('```')%2 else ''
            if len(body+closing+note+suffix)<=MAX_TEXT:
                return body+closing+note+suffix
            clipped=True
            body=body[:max(0,MAX_TEXT-len(suffix)-len(note)-len(closing)-180)].rsplit('\n',1)[0]
        return 'Display budget reached. Request a focused topic or a smaller page.'


def cell(value):
    if value is None:
        return '—'
    if isinstance(value,list):
        value=', '.join(str(item) for item in value)
    return str(value).replace('|','\\|').replace('\n',' ').replace('`','')[:260]


def source_of(item):
    ev=item.get('evidence')
    if isinstance(ev,dict):
        return ev
    return item


def field_table(md,items):
    md.table(['Field','Declared in / type','Code usage','Evidence'],[
        [item['name'],str(item.get('entity') or item.get('form') or 'interface')+' / '+str(item.get('data_type') or item.get('input_type') or item.get('kind','')),
         ', '.join(label['label'] for label in item.get('source_labels',[]))+'; '+str(item.get('usage_count',0))+' occurrences' if 'usage_count' in item else item.get('label') or 'Declaration',
         md.cite(source_of(item))+' '+ ' '.join(md.cite(label['evidence']) for label in item.get('source_labels',[]))] for item in items])


def query_table(md,queries):
    md.table(['Query ID','Language / resolution','Tables','Evidence'],[
        [query['id'][:12],str(query.get('language','SQL'))+' / '+str(query.get('resolution') or query.get('status','')),
         query.get('tables',[]),md.cite(query)] for query in queries])
    md.add('For application lineage, pass a listed query ID to `get_sql_lineage(query_id=...)`. Supplied SQL is a hypothetical query analysis unless independently matched to source.')


def render_report(md,data):
    md.add('# '+data.get('application','Application')+' discovery')
    md.add('Topic: **'+data.get('topic','overview')+'**. '+data.get('scope_note','Static source evidence.'))
    md.add()
    for doc in data.get('purpose',[]):
        md.add(doc['definition']+' '+md.cite(doc['evidence']))
    architecture=data.get('architecture')
    if architecture:
        md.add('## Architecture')
        if architecture.get('mermaid'):
            md.add('```mermaid\n'+architecture['mermaid']+'\n```')
        links=architecture.get('dependencies',[])
        if links:
            md.table(['From','To','Source evidence'],[[edge['source'],edge['target'],
                ' '.join(md.cite(ev) for ev in edge.get('evidence',[]))] for edge in links])
        for note in architecture.get('notes',[]):
            md.add('- '+note)
    if data.get('apis'):
        md.add('## APIs')
        md.table(['Endpoint','Evidence'],[[item['name'],md.cite(source_of(item))] for item in data['apis']])
    if data.get('api_groups'):
        md.add('## APIs by service')
        for group in data['api_groups']:
            md.add('**'+group['service']+'**')
            probes=[]
            for route in group.get('route_evidence',[]):
                if re.fullmatch(r'(GET|HEAD) /(version|ready|healthy|healthz|readyz)',route['name']):
                    probes.append(route['name'])
                else:
                    md.add('- `'+route['name']+'` '+md.cite(route['evidence']))
            if probes:
                md.add('Operational probes: '+', '.join('`'+name+'`' for name in probes)+'.')
            md.add()
    if data.get('fields'):
        md.add('## Fields and demographic discovery context')
        field_table(md,data['fields'])
        md.add('These are source declarations. Use labels, definitions and consuming code to interpret business meaning; target C360 mappings remain unapproved.')
    if data.get('interface_gaps'):
        md.add('### Interface fields without a same-name schema declaration')
        md.table(['Field','Declared interface','Evidence'],[[item['name'],item.get('label') or item.get('form',''),md.cite(item)] for item in data['interface_gaps']])
        md.add('Absence of a same-name schema field does not prove the field is discarded. Trace explicit assignments before making that claim.')
    if data.get('definitions'):
        md.add('## Relevant definitions')
        for item in data['definitions']:
            md.add('- '+item.get('definition','')+' '+md.cite(item.get('evidence',{})))
    if data.get('transformations'):
        md.add('## Transformations and field flow')
        rows=[]
        for item in data['transformations']:
            description=item.get('flow')
            if not description and item['name']=='numeric_scaling':
                operand=next((value for value in item.get('inputs',[]) if value!='?'),'expression')
                description=('Multiply ' if item.get('operation')=='Mult' else 'Divide ')+operand+' by '+str(item.get('factor'))+' in '+str(item.get('function',''))
            if not description:
                description=str(item.get('operation') or item['name'])+'('+', '.join(item.get('inputs',[]))+') in '+str(item.get('function') or 'module scope')
            rows.append([item['name'].replace('_',' '),description,md.cite(item)])
        md.table(['Operation','Inputs → output / context','Evidence'],rows)
        if data.get('flow_page',{}).get('representative'):
            md.add('Representative operations are shown; '+str(data['flow_page']['total'])+' static flow facts are available in the focused flows topic.')
    if data.get('queries'):
        md.add('## SQL and ORM')
        query_table(md,data['queries'])
    if data.get('handoff'):
        md.add('## Draft C360 handoff')
        for item in data['handoff']:
            md.add('- '+str(item))
    if data.get('coverage'):
        c=data['coverage']
        md.add('## Coverage and limits')
        md.add(f"{c.get('indexed',0)} indexed files; {c.get('excluded',0)} unsupported/record artifacts excluded; {c.get('failed',0)} failed/oversized files; {c.get('dead_letters',0)} dead-letter tasks. Exclusions and parser notes are distinct from failed files.")
        for note in c.get('notes',[]):
            md.add('- '+note)
    for note in data.get('limitations',[]):
        md.add('- '+note)
    md.add()
    if data.get('next_actions'):
        md.add('### Focused follow-up')
        for action in data['next_actions']:
            md.add('- '+str(action))
    md.add('Compose the final chat answer as a readable report with the diagram, tables and citations. Do not paste transport JSON, internal IDs or parser-status labels into prose. Do not exhaust unrelated documentation/coverage pages for an overview.')


def render(operation,envelope):
    md=Markdown()
    if envelope.get('status')!='OK':
        md.add('OriginSpan: '+envelope.get('summary','The operation could not be completed.'))
        md.add('Code: '+envelope.get('code',envelope.get('status','ERROR')))
        if envelope.get('retryable'):
            md.add('This may be retried. Preserve the same authorized project/application scope.')
        return md.finish()
    data=envelope.get('data',{})
    if operation=='discover_application' or data.get('presentation')=='chat_report':
        render_report(md,data)
    elif operation=='list_applications':
        md.add('## Authorized applications')
        md.table(['Application','Application ID'],[[app['name'],app['id']] for app in data.get('applications',[])])
        md.add('Use `discover_application` with the authorized project and application name for a readable report. No local file searches or JSON parsing are needed.')
    elif operation=='get_session_context':
        md.add('## OriginSpan connection')
        md.add('Authorized projects: '+', '.join(data.get('projects',[])))
        md.add('Use an explicitly listed project. Start application discovery with `discover_application`.')
    elif operation=='list_analyses':
        md.add('## Application analyses')
        md.table(['Analysis ID','Status','Evidence'],[[item['id'],item['status'],item.get('evidence_count','')] for item in data.get('analyses',[])])
    elif operation in ('get_analysis','start_analysis'):
        md.add('## Analysis '+str(data.get('status','')))
        md.add('Analysis ID: `'+str(data.get('id',''))+'`')
        md.add('Evidence items: '+str(data.get('evidence_count',0)))
        if data.get('tasks'):
            md.add('Tasks: '+', '.join(str(k)+' '+str(v) for k,v in data['tasks'].items()))
    elif operation=='get_discovery_section':
        md.add('## '+str(data.get('section','Discovery')).replace('_',' ').title())
        items=data.get('items',[])
        if data.get('section')=='fields':
            field_table(md,items)
        elif data.get('section') in ('purpose','documentation'):
            for item in items:
                md.add(item.get('definition','')+' '+md.cite(item.get('evidence',{})))
        else:
            md.table(['Finding','Context','Evidence'],[[item.get('name') or item.get('question',''),
                item.get('table') or item.get('operation') or item.get('function',''),md.cite(source_of(item))] for item in items])
        md.add(f"Page: {len(items)} items, total {data.get('total',len(items))}. Next offset: {data.get('next_offset')}. Follow only pages relevant to the user's requested scope.")
    elif operation=='get_architecture':
        md.add('## Architecture')
        md.add('```mermaid\n'+data.get('configured_services_mermaid',data.get('mermaid','flowchart LR'))+'\n```')
        md.table(['From','To','Evidence'],[[edge['source'],edge['target'],
            ' '.join(md.cite(item) for item in edge.get('evidence',[]))] for edge in data.get('resolved_service_dependencies',[])])
    elif operation=='list_sql_queries':
        md.add('## Observed SQL / ORM queries')
        query_table(md,data.get('queries',[]))
        md.add('Next offset: '+str(data.get('next_offset')))
    elif operation=='get_sql_lineage':
        provenance=data.get('provenance',{})
        md.add('## '+('Source-query lineage' if provenance.get('kind')=='observed_source_query' else 'Supplied-query analysis — source occurrence not established'))
        if provenance.get('file'):
            md.add('Source: '+md.cite(provenance))
        md.table(['Output','Value sources','Expression'],[[item.get('output',''),item.get('sources',[]),item.get('expression','')] for item in data.get('value_lineage',[])])
        md.add('Predicate dependencies: '+cell(data.get('predicate_dependencies',[])))
        md.add('A valid SQL AST proves query structure, not that the application contains a caller-supplied query.')
    elif operation in ('search_findings','get_semantic_context','discover_demographic_fields','get_graph','get_change_impact'):
        md.add('## Evidence context')
        items=data.get('findings',data.get('evidence',data.get('nodes',[])))
        md.table(['Finding','Source context','Evidence','Drill-down reference'],[[item.get('name',''),item.get('description','')[:220],md.cite(source_of(item)),item.get('id','')] for item in items])
        if data.get('edges'):
            names={item.get('id'):item.get('name') for item in items}
            md.table(['From','Relationship','To'],[[names.get(edge.get('source'),edge.get('source')),
                edge.get('kind',edge.get('type','')),names.get(edge.get('target'),edge.get('target'))] for edge in data['edges'][:20]])
            if len(data['edges'])>20:
                md.add('Showing 20 relationships; narrow the focus for the remaining edges.')
        for suggestion in data.get('suggestions',[])[:5]:
            md.add('- '+('Reviewed interpretation' if suggestion.get('status')=='ACCEPTED' else 'Unapproved interpretation')+': '+str(suggestion.get('subject',''))+' — '+str(suggestion.get('meaning',''))[:400])
        md.add('Use the source context to interpret meaning. Source declarations do not require C360 approval; proposed C360 mappings do.')
        md.add('For a coherent field report, use `discover_application` with topic `fields`. Repeating a question to this tool does not advance a page offset.')
    elif operation=='get_evidence':
        md.add('## Source evidence: '+str(data.get('name','Finding')))
        md.add(str(data.get('description') or data.get('kind','Source metadata')))
        md.add('Location: '+md.cite(source_of(data)))
        md.add('Evidence reference: `'+str(data.get('id',''))+'`')
        if data.get('excerpt'):
            md.add('Authorized source excerpt:\n```text\n'+str(data['excerpt']).replace('```','')+'\n```')
    elif operation=='get_discovery_report':
        md.add('## Discovery status')
        md.add(str(data.get('summary','')))
        facts=data.get('observed_facts',{})
        md.add('Indexed source: '+str(facts.get('files',0))+' files, '+str(facts.get('bytes',0))+' bytes.')
        md.add('Unapproved hypotheses: '+str(len(data.get('hypotheses',[])))+'. Reviewed interpretations: '+str(len(data.get('reviewed_knowledge',[])))+'.')
        md.add('For the organized application explanation, use `discover_application`; audit counts are not the final discovery narrative.')
    elif operation=='validate_suggestions':
        md.add('## Suggestion reference checks')
        md.add('References valid: '+str(data.get('valid',False))+'. This does not establish business correctness.')
        for index,result in enumerate(data.get('results',[]),1):
            md.add('- Suggestion '+str(index)+': '+('references valid' if result.get('valid') else '; '.join(result.get('errors',[]))))
    elif operation=='get_discovery_coverage':
        md.add('## Coverage page')
        md.add(str(len(data.get('files',[])))+' file statuses; '+str(len(data.get('dead_letters',[])))+' returned dead letters.')
        issues=[item for item in data.get('files',[]) if item.get('state')!='INDEXED']
        md.table(['File','Status','Detail'],[[item.get('path',''),item.get('state',''),item.get('detail','')] for item in issues])
        md.add('Next offset: '+str(data.get('next_offset'))+'. Normal indexed files are omitted from prose; use the application report for aggregate coverage.')
    elif operation=='get_complete_discovery':
        md.add('## Application discovery overview')
        md.add(data.get('summary',''))
        for name,section in data.get('sections',{}).items():
            md.add('- '+name+': '+str(section.get('total',0))+' indexed items.')
        md.add('For the organized chat report, call `discover_application` with the application name and topic `overview`. Do not enumerate every documentation page for an overview.')
        diagram=data.get('architecture',{}).get('mermaid')
        if diagram:
            md.add('```mermaid\n'+diagram+'\n```')
    else:
        md.add('## OriginSpan '+operation.replace('_',' '))
        md.add(data.get('summary',data.get('status','Operation completed.')))
        for key in ('id','application_id','analysis_id','status','valid'):
            if key in data:
                md.add(key.replace('_',' ').title()+': '+str(data[key]))
        if data.get('ids'):
            md.add('Saved proposal references: '+', '.join(str(id_) for id_ in data['ids']))
    for note in data.get('limitations',[])[:3]:
        md.add('- '+str(note))
    return md.finish()


def tool_result(operation,envelope):
    """Keep original structured data for clients; humans/models get readable content."""
    return CallToolResult(content=[TextContent(type='text',text=render(operation,envelope))],
                          structured_content=envelope,is_error=envelope.get('status')!='OK')


def payload(result):
    """Programmatic clients prefer native structured output; legacy JSON is supported."""
    if getattr(result,'structured_content',None) is not None:
        return result.structured_content
    return json.loads(result.content[0].text)
