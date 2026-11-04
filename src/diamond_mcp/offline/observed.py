"""Source facts that do not require generative restatement."""
from __future__ import annotations


def observed_section(stage, run, evidence):
    if stage == 'architecture':
        edges=[]
        for ref, row in evidence.items():
            data=row['metadata']
            if row['topic']=='architecture' and data.get('source') and data.get('target'):
                edges.append(dict(source=data['source'],target=data['target'],kind=data.get('kind','recorded_relationship'),
                                  status=data.get('status','static_evidence'),citation=ref))
        return dict(kind='architecture',edges=edges,
                    limitations=['Recorded static relationships do not establish runtime health or traffic.'] +
                    ([] if edges else ['No source-located service-to-service edges were recorded.']))
    if stage == 'sql':
        queries=[]
        for key, value in run['facts'].items():
            if not key.startswith('lineage_'):continue
            origin=value.get('provenance',{})
            if origin.get('kind')!='observed_source_query':continue
            ref=next((ref for ref,row in evidence.items() if row['topic']=='lineage' and
                      row['metadata'].get('query_id')==origin.get('query_id')),None)
            if ref is None:continue
            queries.append(dict(citation=ref,outputs=value.get('lineage',[]),conditions=value.get('conditions',[]),
                                limitations=value.get('limitations',[])))
        return dict(kind='sql',queries=queries,
                    limitations=['Value sources and selection dependencies are reported separately. Only the collected source queries are covered.'] +
                    ([] if queries else ['No source-bound SQL lineage was available.']))
    if stage == 'handoff':
        return dict(kind='handoff',source_fields_collected=len(run['facts'].get('section_fields',{}).get('items',[])),
                    limitations=['No approved target schema or mapping rules have been selected for this workflow. '
                                 'Use the source-field inventory for human mapping review; no target fields or approvals are invented.'])
    return None


def render_observed(value, safe):
    out=['**Observed source facts; no model restatement.**']
    if value['kind']=='architecture':
        for edge in value['edges']:
            out.append('- '+safe(edge['source'])+' → '+safe(edge['target'])+' ('+safe(edge['kind'])+', '+safe(edge['status'])+'). ['+edge['citation']+']')
    elif value['kind']=='sql':
        for query in value['queries']:
            out += ['', 'Source query ['+query['citation']+']', '', '| Output | Value sources | Transformation |', '| --- | --- | --- |']
            for row in query['outputs']:
                out.append('| '+' | '.join(safe(x) for x in (row.get('output',''),', '.join(row.get('sources',[])),row.get('transformation','')))+' |')
            out += ['', '**Join/filter/order dependencies**']
            for condition in query['conditions']:
                out.append('- '+safe(condition.get('kind','selection'))+': `'+safe(condition.get('expression',''))+'`; fields: '+safe(', '.join(condition.get('dependent_fields',[])))+'.')
            if not query['conditions']:out.append('- No selection dependencies were recorded for this query.')
            out += ['- Limitation: '+safe(x) for x in query['limitations']]
    else:
        out.append('- Source field records collected: '+str(value['source_fields_collected'])+'.')
    out += ['- '+safe(x) for x in value['limitations']]
    return out
