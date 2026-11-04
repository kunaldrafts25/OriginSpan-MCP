"""Check a built image before push: nonroot, CLI, and real no-network MCP stdio."""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def verify(image, version, output):
    user = subprocess.check_output(['docker','image','inspect','--format','{{.Config.User}}',image], text=True).strip()
    if not user or user in {'root','0','0:0'}:
        raise AssertionError('Image must run as a nonroot user')
    subprocess.run(['docker','run','--rm','--network','none',image,'--help'],check=True,capture_output=True,text=True,timeout=30)
    params=StdioServerParameters(command='docker',args=['run','--rm','--network','none','-i',image])
    async with stdio_client(params) as (r,w):
        async with ClientSession(r,w) as session:
            info=(await session.initialize()).model_dump(by_alias=True)
            if info['serverInfo']['version'] != version:
                raise AssertionError('Image MCP version must match the release')
            tools=await session.list_tools()
            if 'discover_application' not in {t.name for t in tools.tools}:
                raise AssertionError('Discovery tool missing')
            result=await session.call_tool('get_session_context',{})
            if result.is_error:
                raise AssertionError('Authorized local context failed')
            report=dict(status='PASS',image=image,version=version,default_user=user,network='none',transport='actual MCP stdio',tools=len(tools.tools),context_call='PASS',model_inference='NOT_RUN; no model assets included')
    output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image',required=True)
    p.add_argument('--version',required=True)
    p.add_argument('--output',type=Path,default=Path('container-validation.json'))
    a=p.parse_args()
    asyncio.run(verify(a.image,a.version,a.output))
