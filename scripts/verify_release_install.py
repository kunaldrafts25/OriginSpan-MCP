"""Execute a clean no-index Windows install, public sample indexing and real MCP stdio."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

def verify(archive, output):
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 13):
        raise RuntimeError('Windows x64/Python 3.13 validation required')
    env = {k:v for k,v in os.environ.items() if not k.startswith(('ORIGINSPAN_', 'DIAMOND_'))}
    env.update(PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1', ORIGINSPAN_ALLOW_GITHUB='false', ORIGINSPAN_NEURAL_ENABLED='false')
    with tempfile.TemporaryDirectory(prefix='originspan-release-') as tmp:
        base = Path(tmp)
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                path = (base / name).resolve()
                if not path.is_relative_to(base.resolve()):
                    raise ValueError('Unsafe archive member')
            z.extractall(base)
        package, = [p for p in base.iterdir() if p.is_dir()]
        destination = base / 'installation with spaces'
        def run(command):
            result = subprocess.run([str(v) for v in command], env=env, text=True, capture_output=True, timeout=240)
            if result.returncode:
                raise RuntimeError(f'{Path(str(command[0])).name} failed: {result.stdout[-3000:]}\n{result.stderr[-3000:]}')
            return result.stdout
        run(['powershell', '-NoProfile', '-NonInteractive', '-File', package/'Install-OriginSpan.ps1', '-Destination', destination, '-PythonExecutable', sys.executable])
        python = destination / '.venv/Scripts/python.exe'
        state = base / 'state'
        cli = [python, '-m', 'diamond_mcp', '--state', state, '--project', 'release-check']
        app = json.loads(run(cli + ['register', 'PublicBankSample']))['id']
        json.loads(run(cli + ['import-zip', app, package/'demo/bank-of-anthos.zip']))
        analysis = json.loads(run(cli + ['analyze', app]))
        if analysis['status'] != 'COMPLETED' or analysis.get('evidence_count', 0) < 100:
            raise AssertionError('Public sample indexing did not complete')
        check = base / 'check_mcp.py'
        check.write_text('''import asyncio,sys,json
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
    params=StdioServerParameters(command=sys.executable,args=['-m','diamond_mcp','--state',sys.argv[1],'--project','release-check','serve'])
    async with stdio_client(params) as (r,w):
        async with ClientSession(r,w) as session:
            await session.initialize()
            tools=await session.list_tools()
            names={t.name for t in tools.tools}
            assert 'discover_application' in names and 'get_session_context' in names
            result=await session.call_tool('get_session_context',{})
            assert not result.is_error
            print(json.dumps({'transport':'stdio','tools':len(names),'context_call':'PASS'}))
asyncio.run(main())
''', encoding='utf-8')
        mcp = json.loads(run([python, check, state]))
        receipt = json.loads((destination/'install-receipt.json').read_text(encoding='utf-8-sig'))
        report = dict(status='PASS', version=receipt['version'], source_commit=receipt['source_commit'],
                      install='fresh isolated venv; bundled wheels; pip --no-index --require-hashes',
                      public_source_files=analysis.get('snapshot', {}).get('files'), evidence=analysis['evidence_count'],
                      mcp=mcp, local_model_inference='NOT_RUN; model assets distributed separately',
                      network='pip index disabled; OS network isolation not asserted by this packaging test')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(report, indent=2))

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a=p.parse_args()
    verify(a.archive.resolve(), a.output.resolve())
