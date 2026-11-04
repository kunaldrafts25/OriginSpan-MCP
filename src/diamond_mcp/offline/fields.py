"""Unapproved semantic annotations anchored to discovered field evidence."""
from __future__ import annotations

KINDS=frozenset({'declared_schema_field','form_field','field_usage','literal_key_candidate'})
VERSION='field-consistency-2'


def meaning_candidates(values):
    if values is None:return None
    if not isinstance(values,(list,tuple)) or not 1<=len(values)<=30 or any(not isinstance(v,str) or not 1<=len(v)<=120 for v in values):
        raise ValueError('Supply 1-30 bounded candidate meanings')
    return list(dict.fromkeys([*values,'unresolved','ambiguous']))


def select_fields(package):
    return [row for row in package if row['metadata'].get('name') and
            (row.get('topic')=='fields' or row['metadata'].get('kind') in KINDS)]


def annotation_schema(fields, evidence):
    return dict(type='object',additionalProperties=False,required=['annotations'],properties=dict(
        annotations=dict(type='array',maxItems=len(fields),items=dict(type='object',additionalProperties=False,
            required=['ref','meaning','status','citations'],properties=dict(
                ref=dict(type='string',enum=[row['ref'] for row in fields]),
                meaning=dict(type='string',maxLength=120),
                status=dict(type='string',enum=['hypothesis','unresolved']),
                citations=dict(type='array',minItems=1,maxItems=8,items=dict(type='string',enum=[row['ref'] for row in evidence])))))))


def validate_annotations(value, fields, package, candidates=None):
    if not isinstance(value,dict) or set(value)!={'annotations'} or not isinstance(value['annotations'],list):
        raise ValueError('Expected field annotations only')
    known={row['ref']:row for row in fields};allowed={row['ref'] for row in package};found={}
    for row in value['annotations']:
        if not isinstance(row,dict) or set(row)!={'ref','meaning','status','citations'}:
            raise ValueError('Invalid annotation keys')
        ref=row['ref']
        if not isinstance(ref,str) or ref not in known or ref in found:raise ValueError('Unknown or duplicate field reference')
        if not isinstance(row['status'],str) or row['status'] not in {'hypothesis','unresolved'} or not isinstance(row['meaning'],str) or len(row['meaning'])>120 or (not row['meaning'].strip() and row['status']!='unresolved'):
            raise ValueError('Invalid meaning or approval status')
        cites=row['citations']
        if not isinstance(cites,list) or not 1<=len(cites)<=8 or any(not isinstance(c,str) or c not in allowed for c in cites) or ref not in cites:
            raise ValueError('Field annotation lacks valid source citations')
        found[ref]=dict(row,meaning=row['meaning'].strip() or 'Meaning unresolved',citations=list(dict.fromkeys(cites)),approved=False,origin='local_model')
        if candidates is not None and row['meaning'] not in meaning_candidates(candidates):raise ValueError('Meaning is outside the supplied candidates')
    result=[]
    for ref,source in known.items():
        annotation=found.get(ref,dict(ref=ref,meaning='No annotation returned by the local model',status='unresolved',citations=[ref],approved=False,origin='model_omission'))
        definitions={}
        for item in package:
            metadata=item.get('metadata',{})
            same_name=str(metadata.get('name','')).casefold()==str(source['metadata']['name']).casefold()
            entity=source['metadata'].get('entity');other=metadata.get('entity')
            if same_name and (not entity or not other or entity==other) and isinstance(metadata.get('definition'),str) and metadata['definition'].strip():
                key=' '.join(metadata['definition'].casefold().split()).rstrip(' .;')
                definitions.setdefault(key,[]).append(item['ref'])
        if len(definitions)>1:
            refs=[ref for group in definitions.values() for ref in group]
            annotation=dict(annotation,rejected_model_proposal=dict(meaning=annotation['meaning'],status=annotation['status']),
                meaning='ambiguous',status='unresolved',origin='consistency_guard',competing_definition_refs=refs,
                uncertainty_reason='Distinct unreviewed definitions share this field scope; they may be compatible, but require review.',
                citations=list(dict.fromkeys([ref,*annotation['citations'],*refs]))[:8])
        elif annotation['meaning'].strip().casefold() in {'unresolved','unknown','undetermined','ambiguous','insufficient evidence'} and annotation['status']!='unresolved':
            annotation=dict(annotation,status='unresolved',origin='consistency_guard',uncertainty_reason='The model explicitly returned an uncertainty label.')
        result.append(dict(annotation,name=source['metadata']['name'],entity=source['metadata'].get('entity','')))
    return result
