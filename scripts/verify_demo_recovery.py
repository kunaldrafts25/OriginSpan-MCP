"""Prove public-demo recovery after two empty-filesystem boots, without outbound networking."""
import json
from pathlib import Path
import secrets
import subprocess
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
PROBE=r'''
import asyncio,json,os,time
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
async def main():
 started=time.monotonic()
 async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+os.environ['ORIGINSPAN_DEMO_TOKEN']},trust_env=False,timeout=30) as http:
  for _ in range(120):
   try:
    r=await http.get('http://127.0.0.1:10000/readyz')
    if r.status_code==200:break
   except httpx2.HTTPError:pass
   await asyncio.sleep(.25)
  else:raise RuntimeError('NOT_READY')
  async with streamable_http_client('http://127.0.0.1:10000/mcp',http_client=http) as (read,write):
   async with ClientSession(read,write) as session:
    info=await session.initialize()
    async def call(tool_name,**args):
     r=await session.call_tool(tool_name,{'project_id':'demo',**args})
     value=r.structured_content
     assert value['status']=='OK',(tool_name,value.get('code'))
     return value['data']
    apps=(await call('list_applications'))['applications']
    assert len(apps)==1 and apps[0]['name']=='Bank of Anthos'
    assert apps[0]['owner']=='bundled-public-demo'
    app=apps[0]['id']
    job=(await call('list_analyses',application_id=app))['analyses'][0]
    assert job['status']=='COMPLETED' and job['snapshot']['files']==321
    report=await call('discover_application',application_name='Bank of Anthos')
    assert any(field['name']=='country' for field in report['interface_gaps'])
    queries=await call('list_sql_queries',application_id=app,analysis_id=job['id'],limit=100)
    assert sum(q.get('resolution')=='EXPLICIT_JPA_MAPPING' for q in queries['queries'])==8
    # Prove this boot can mutate its own catalog, and the next cold boot resets it.
    await call('register_application',name='Temporary restart test')
    print(json.dumps(dict(version=info.server_info.version,application_id=app,analysis_id=job['id'],
      files=job['snapshot']['files'],evidence_count=job['evidence_count'],probe_seconds=time.monotonic()-started,
      seed_owner=apps[0]['owner'],source_uploaded=False,indexing_required=False)))
asyncio.run(main())
'''


def main():
    name='originspan-seed-check-'+uuid.uuid4().hex[:8]
    token=secrets.token_urlsafe(32)
    command=['docker','run','-d','--name',name,'--network','none','--memory','512m','--memory-swap','512m',
             '--cpus','1','--tmpfs','/tmp:rw,size=256m,mode=1777']
    for key,value in dict(ORIGINSPAN_STORAGE_MODE='sqlite-demo',ORIGINSPAN_AUTH_MODE='demo-token',
        ORIGINSPAN_PROJECT='demo',ORIGINSPAN_STATE='/tmp/originspan',ORIGINSPAN_DEMO_TOKEN=token,
        ORIGINSPAN_RESOURCE_URL='http://127.0.0.1:10000/mcp',ORIGINSPAN_MIN_FREE_BYTES='33554432',PORT='10000').items():
        command+=['-e',key+'='+value]
    command+=['originspan-mcp:recovery-0.5.1']
    results=[]
    try:
        subprocess.run(command,check=True,stdout=subprocess.DEVNULL)
        for boot in range(2):
            if boot:
                subprocess.run(['docker','restart',name],check=True,stdout=subprocess.DEVNULL)
            result=subprocess.run(['docker','exec','-i',name,'python','-'],input=PROBE,text=True,capture_output=True)
            if result.returncode:
                # The script never prints headers, token or source bodies.
                raise RuntimeError('Public demo probe failed on boot '+str(boot+1)+': '+result.stderr[-1200:])
            value=json.loads(result.stdout.strip().splitlines()[-1])
            results.append(value)
            print(json.dumps({'boot':boot+1,**value}),flush=True)
        assert results[0]['application_id']==results[1]['application_id']
        assert results[0]['analysis_id']==results[1]['analysis_id']
        state=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
        assert not state['State']['OOMKilled']
        report=dict(status='PASS',network='none; loopback-only MCP probe',memory_limit='512 MiB, swap disabled',
            boots=results,checks=['completed public demo available on first boot','no upload or runtime indexing',
                'empty tmpfs restored after restart','stable bundled application and analysis IDs',
                'HTML country metadata','eight supported JPQL resolutions','no OOM'],
            limitation='Public sample recovery, not persistence of private uploads or AI proposals')
        destination=ROOT/'docs/validation-results/demo-recovery'
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'cold-boots.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2),flush=True)
    finally:
        subprocess.run(['docker','stop',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


if __name__=='__main__':
    main()
