"""Exact-label protocol probes; not a general business-accuracy benchmark."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from diamond_mcp.offline.profiles import load,atomic_json,fingerprint
from diamond_mcp.offline.resources import InferenceLock
from diamond_mcp.offline.runtime import LocalRuntime,SYSTEM,SCHEMA,GENERATION_POLICY,FIELD_SYSTEM,FIELD_TASK
from diamond_mcp.offline.fields import select_fields,validate_annotations
from diamond_mcp.offline.model_input import VERSION
from diamond_mcp.offline.workflow import validate_interpretation


async def run(args):
    content=args.suite.read_bytes()
    if len(content)>2_000_000:raise ValueError('Probe suite exceeds its budget')
    if hashlib.sha256(content).hexdigest()!=args.suite.with_suffix('.sha256').read_text().strip():
        raise ValueError('Frozen probe checksum changed')
    cases=json.loads(content)['cases']
    if not 1<=len(cases)<=100:raise ValueError('Use 1-100 bounded probes')
    profile=load(args.state,args.profile)
    results=[];started=time.monotonic()
    async with LocalRuntime(profile) as runtime:
        for case in cases:
            if case['expected'] not in case['choices']:raise ValueError('Expected label is outside the declared choices')
            question=('Identify the meaning of the field from the supplied evidence. Choose exactly one label from '+json.dumps(case['choices'])+
                '. Return exactly one claim whose text is that label, with the supporting citations. '+
                'Use unresolved for insufficient evidence, ambiguous for conflicting definitions. No follow-up requests; this is a closed evidence task.')
            t=time.monotonic()
            try:
                if args.mode=='field-annotations':
                    package=[dict(row,topic='fields' if i==0 else 'context_demographics') for i,row in enumerate(case['evidence'])]
                    task='For this closed protocol probe, use exactly one meaning label from '+json.dumps(case['choices'])+'. Use unresolved for missing context and ambiguous for conflicting definitions.'
                    response=await runtime.annotate_fields(task,package,candidates=case['choices'])
                    annotations=validate_annotations(response,select_fields(package),package,candidates=case['choices'])
                    annotation=next(row for row in annotations if row['ref']==case['evidence'][0]['ref'])
                    decision=annotation['meaning'];cited=annotation['citations']
                    passed=decision==case['expected'] and set(case['required_citations'])<=set(cited)
                    if case['expected'] in {'ambiguous','unresolved'}:passed=passed and annotation['status']=='unresolved'
                else:
                    response=await runtime.complete('demographics',question,case['evidence'])
                    validate_interpretation(response,{row['ref'] for row in case['evidence']})
                    claims=response['claims'];decision=claims[0]['text'] if len(claims)==1 else None
                    cited=claims[0]['citations'] if len(claims)==1 else []
                    passed=decision==case['expected'] and set(case['required_citations'])<=set(cited) and not response['requests']
                row=dict(id=case['id'],passed=passed,decision=decision,expected=case['expected'],response=response)
            except ValueError as exc:
                row=dict(id=case['id'],passed=False,error=str(exc)[:300])
            row['seconds']=round(time.monotonic()-t,3);results.append(row)
            summary=dict(suite_sha256=hashlib.sha256(content).hexdigest(),model=profile['model_id'],model_sha256=profile['model']['sha256'],
                revision=profile['revision'],mode=args.mode,candidate_labels_constrained=args.mode=='field-annotations',inference_contract=fingerprint(dict(system=SYSTEM,schema=SCHEMA,decoding=GENERATION_POLICY,input_version=VERSION,field_system=FIELD_SYSTEM,field_task=FIELD_TASK)),
                results=results,passed=sum(x['passed'] for x in results),total=len(cases),usage=runtime.calls,
                resources=runtime.resources,seconds=round(time.monotonic()-started,3),
                qualification='EXACT_LABEL_PROTOCOL_CHECK_ONLY_NOT_DISCOVERY_ACCURACY_CERTIFICATION',independent_domain_owner_review=False)
            atomic_json(args.output,summary)
            print(json.dumps({k:v for k,v in row.items() if k!='response'}),flush=True)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',type=Path,required=True)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--profile',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mode',choices=['field-annotations','labels'],default='field-annotations')
    args=parser.parse_args()
    with InferenceLock(args.state):result=asyncio.run(run(args))
    print(json.dumps(dict(passed=result['passed'],total=result['total'],qualification=result['qualification'])))
    if result['passed']!=result['total']:raise SystemExit(2)
