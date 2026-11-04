"""Run frozen tasks locally. No downloads, source registration or online model calls."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from diamond_mcp.offline.profiles import load, atomic_json
from diamond_mcp.offline.runtime import LocalRuntime
from diamond_mcp.offline.transport import StdioTools
from diamond_mcp.offline.workflow import Discovery
from diamond_mcp.offline.resources import InferenceLock
from diamond_mcp.offline.evaluation import read_suite


async def main():
    p=argparse.ArgumentParser()
    p.add_argument('--suite',type=Path,required=True)
    p.add_argument('--source-map',type=Path,required=True,help='source ID -> {state,project,application,analysis}')
    p.add_argument('--profile',required=True)
    p.add_argument('--model-state',type=Path,required=True)
    p.add_argument('--split',choices=['calibration','held_out'],default='calibration')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    suite=read_suite(args.suite)
    mapping=json.loads(args.source_map.read_text(encoding='utf-8'))
    profile=load(args.model_state,args.profile)
    index=json.loads(args.output.read_text()) if args.output.is_file() else {}
    for source,scope in mapping.items():
        tasks=[t for t in suite['tasks'] if t['source']==source and t['split']==args.split]
        if not tasks:continue
        # Dedicated process per application prevents cross-project prompt/KV reuse.
        async with StdioTools(Path(scope['state']),scope['project']) as tools:
            async with LocalRuntime(profile) as runtime:
                for task in tasks:
                    if task['id'] in index:continue
                    run,folder=await Discovery(tools,runtime,profile,Path(scope['state']),scope['project']).run_report(
                        scope['application'],scope['analysis'],task['question'])
                    index[task['id']]=str((folder/'run.json').resolve())
                    atomic_json(args.output,index)
                    print(json.dumps(dict(task=task['id'],status=run['status'],run_id=run['id'])),flush=True)
    missing=[t['id'] for t in suite['tasks'] if t['split']==args.split and t['id'] not in index]
    print(json.dumps(dict(status='RUNS_RECORDED' if not missing else 'INCOMPLETE_INPUTS',missing=missing,
                          qualification='REQUIRES_OWNER_REVIEW_AND_SECURITY_RESOURCE_GATES')),flush=True)

if __name__=='__main__':
    # Runtime-per-source isolation is in main. The CLI discover lock prevents
    # concurrent runs; launch one evaluation process per workstation.
    # Hold the same OS lock as interactive discovery for the entire batch.
    model_state = argparse.ArgumentParser(add_help=False)
    model_state.add_argument('--model-state', type=Path, required=True)
    state_args, _ = model_state.parse_known_args()
    with InferenceLock(state_args.model_state):
        asyncio.run(main())
