"""Exercise the Render image at 512 MiB with approved public banking metadata."""
from diamond_mcp.chat_output import payload
import asyncio
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:18784'


async def main():
    archive=Path(sys.argv[1]).read_bytes()
    token=secrets.token_urlsafe(32)
    container='originspan-discovery-check-'+uuid.uuid4().hex[:8]
    report={'image':'originspan-mcp:chat-0.5','memory_limit_bytes':512*1024**2,
            'source_zip_bytes':len(archive),'checks':{},'limitations':['Local Linux container, not the live Render deployment',
                'No host-AI answer comparison; no large-project or terabyte capacity claim']}
    command=['docker','run','-d','--name',container,'-p','127.0.0.1:18784:10000',
             '--memory','512m','--memory-swap','512m','--cpus','1','--tmpfs','/tmp:rw,size=256m,mode=1777']
    settings={'ORIGINSPAN_AUTH_MODE':'demo-token','ORIGINSPAN_STORAGE_MODE':'sqlite-demo',
              'ORIGINSPAN_DEMO_TOKEN':token,'ORIGINSPAN_PROJECT':'demo','ORIGINSPAN_STATE':'/tmp/originspan',
              'ORIGINSPAN_NEURAL_ENABLED':'false','ORIGINSPAN_PGVECTOR':'false','ORIGINSPAN_MIN_FREE_BYTES':'33554432',
              'ORIGINSPAN_RESOURCE_URL':BASE+'/mcp','PORT':'10000'}
    for key,value in settings.items():
        command+=['-e',key+'='+value]
    command+=['originspan-mcp:chat-0.5']
    try:
        subprocess.run(command,check=True,stdout=subprocess.DEVNULL)
        async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+token},trust_env=False,timeout=90) as http:
            for attempt in range(60):
                try:
                    response=await http.get(BASE+'/readyz')
                    if response.status_code==200:
                        break
                except httpx2.HTTPError:
                    pass
                await asyncio.sleep(1)
            else:
                raise RuntimeError('CONTAINER_NOT_READY')
            report['checks']['readiness']=True
            anonymous=await http.get(BASE+'/api/demo/applications',headers={'Authorization':'Bearer invalid'})
            assert anonymous.status_code==403
            report['checks']['unauthorized_denied']=True
            async with streamable_http_client(BASE+'/mcp',http_client=http) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    report['tool_count']=len((await session.list_tools()).tools)
                    async def call(tool,**kwargs):
                        result=await session.call_tool(tool,{'project_id':'demo',**kwargs})
                        value=payload(result)
                        assert value.get('status')=='OK', (tool,value.get('code'))
                        return value['data']
                    app=(await call('register_application',name='Bank of Anthos container check'))['id']
                    upload=await http.post(BASE+'/api/demo/upload',params={'application':app},content=archive)
                    upload.raise_for_status()
                    job=(await call('start_analysis',application_id=app))['id']
                    started=time.monotonic()
                    for poll in range(240):
                        result=await call('get_analysis',application_id=app,analysis_id=job)
                        if result['status'] not in ('QUEUED','RUNNING'):
                            break
                        if poll%10==0:
                            print(json.dumps({'event':'banking_analysis_progress','tasks':result.get('tasks')}),flush=True)
                        await asyncio.sleep(2)
                    assert result['status']=='COMPLETED',result['status']
                    report['analysis_seconds']=time.monotonic()-started
                    report['snapshot']=result['snapshot']
                    report['evidence_count']=result['evidence_count']
                    report['checks']['banking_analysis_completed']=True
                    started=time.monotonic()
                    dossier=await call('get_complete_discovery',application_id=app,analysis_id=job)
                    report['dossier_seconds']=time.monotonic()-started
                    report['dossier_bytes']=len(json.dumps(dossier).encode())
                    assert len(dossier['architecture']['resolved_service_dependencies'])>=5
                    report['checks']['configured_service_dependencies']=True
                    chat=await session.call_tool('discover_application',{'project_id':'demo','application_name':'Bank of Anthos container check'})
                    assert not chat.is_error
                    assert chat.content[0].text.startswith('# Bank of Anthos container check discovery')
                    assert '```mermaid' in chat.content[0].text
                    assert 'Display budget reached' not in chat.content[0].text
                    report['chat_text_bytes']=len(chat.content[0].text.encode())
                    report['checks']['readable_markdown_and_diagram']=True
                    assert any(f['name']=='country' for f in payload(chat)['data']['interface_gaps'])
                    report['checks']['interface_country_field']=True
                    assert dossier['c360_handoff']['status']=='DRAFT_REQUIRES_REVIEW'
                    report['checks']['review_required_for_c360']=True
                    fields=await call('get_discovery_section',application_id=app,analysis_id=job,section='fields',limit=50)
                    assert any(field['name']=='birthday' and field['usage_count']>0 for field in fields['items'])
                    report['checks']['field_usage_trails']=True
                    queries=await call('list_sql_queries',application_id=app,analysis_id=job,limit=100)
                    report['resolved_jpql_queries']=sum(q.get('resolution')=='EXPLICIT_JPA_MAPPING' for q in queries['queries'])
                    assert report['resolved_jpql_queries']==8
                    report['checks']['explicit_jpql_resolution']=True
                    denied=await session.call_tool('get_complete_discovery',{'project_id':'foreign','application_id':app,'analysis_id':job})
                    assert payload(denied)['status']=='DENIED'
                    report['checks']['cross_project_denied']=True
            review=await http.post(BASE+'/api/demo/review',params={'application':app},json={'id':'none','decision':'ACCEPTED'})
            assert review.status_code==403
            report['checks']['self_approval_denied']=True
        state=json.loads(subprocess.check_output(['docker','inspect',container],text=True))[0]['State']
        report['oom_killed']=state['OOMKilled']
        assert not report['oom_killed']
        report['status']='PASS'
    except BaseException as exc:
        report['status']='FAIL'
        report['error_type']=type(exc).__name__
        raise
    finally:
        destination=ROOT/'docs/validation-results/chat-upgrade'
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'render-container.json').write_text(json.dumps(report,indent=2))
        subprocess.run(['docker','stop',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':
    asyncio.run(main())
