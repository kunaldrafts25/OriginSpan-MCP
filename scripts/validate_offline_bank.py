"""Real pinned-source discovery with Docker-enforced external network denial."""
import asyncio,json,os,socket,sys,time,subprocess
from pathlib import Path
sys.path.insert(0,'/repo/src')
from diamond_mcp.engine import Actor,Config
from diamond_mcp.service import DiscoveryService
from diamond_mcp.offline.profiles import install,load
from diamond_mcp.offline.runtime import LocalRuntime
from diamond_mcp.offline.transport import StdioTools
from diamond_mcp.offline.workflow import Discovery

async def main():
    started=time.monotonic()
    try:
        with socket.create_connection(('1.1.1.1',443),timeout=1):pass
    except OSError:denied=True
    else:raise AssertionError('External network is available')
    interfaces={p.name for p in Path('/sys/class/net').iterdir()}
    assert interfaces=={'lo'},'Validation requires only the loopback interface'
    os.environ['ORIGINSPAN_EXTERNAL_WORKERS']='true';os.environ['ORIGINSPAN_NEURAL_ENABLED']='false'
    state=Path('/tmp/bank-state');engine=DiscoveryService(Config(state))
    actor=Actor('offline-feasibility',frozenset({'demo'}))
    app=engine.register(actor,'demo','Bank of Anthos')['id']
    engine.import_archive(actor,'demo',app,Path('/repo/demo/bank-of-anthos.zip').read_bytes())
    job=engine.start(actor,'demo',app)['id'];engine.pipeline.run('offline-feasibility',job,isolate=True)
    analysis=engine.job(actor,'demo',app,job)
    assert analysis['status']=='COMPLETED'
    index_seconds=round(time.monotonic()-started,3)
    engine.stop_workers();engine.store.engine.dispose()
    print(json.dumps(dict(event='bank_index_completed',seconds=index_seconds,files=analysis['snapshot']['files'],evidence=analysis['evidence_count'])),flush=True)
    assets=Path('/repo/.runtime/offline-discovery')
    install(state,'bank-qwen',model=assets/'Qwen3.5-2B-Q4_K_M.gguf',runtime=assets/'llama-linux/llama-b11960/llama-server',
        model_id='unsloth/Qwen3.5-2B-GGUF',revision='f6d5376be1edb4d416d56da11e5397a961aca8ae',runtime_version='b11960',license_name='Apache-2.0')
    profile=load(state,'bank-qwen')
    destination=Path('/results/feasibility-bank');destination.mkdir(exist_ok=True)
    # This diagnostic contains only public-sample runtime messages, never keys
    # or private source excerpts. Production runtime logging remains disabled.
    log=(destination/'runtime.log').open('wb');original=subprocess.Popen
    def popen(command,*args,**kwargs):
        if str(command[0]).endswith('llama-server'):kwargs['stderr']=log
        return original(command,*args,**kwargs)
    subprocess.Popen=popen
    class ProgressRuntime(LocalRuntime):
        async def annotate_fields(self,question,evidence):
            print(json.dumps(dict(event='field_annotation_started')),flush=True)
            value=await super().annotate_fields(question,evidence)
            print(json.dumps(dict(event='field_annotation_completed',usage=self.calls[-1])),flush=True)
            return value
        async def complete(self,stage,question,evidence):
            print(json.dumps(dict(event='interpretation_started',stage=stage)),flush=True)
            value=await super().complete(stage,question,evidence)
            print(json.dumps(dict(event='interpretation_completed',stage=stage,usage=self.calls[-1])),flush=True)
            return value
    async with StdioTools(state,'demo') as tools:
        async with ProgressRuntime(profile) as runtime:
            run,folder=await Discovery(tools,runtime,profile,state,'demo').run_report(app,job)
    log.close()
    for name in ('run.json','report.md'):(destination/name).write_text((folder/name).read_text(),encoding='utf-8')
    citations=[ref for stage in run['stages'].values() for claim in stage['claims'] for ref in claim['citations']]
    citations += [ref for row in run.get('semantic_fields',[]) for ref in row['citations']]
    for section in run.get('observed_sections',{}).values():
        citations += [row['citation'] for row in section.get('edges',[])+section.get('queries',[])]
    valid=bool(citations) and all(ref in run['evidence'] for ref in citations)
    calls=sum(x.get('input_tokens') is not None and x.get('status')!='FAILED' for x in run['usage'])
    def total(key):
        return sum(x[key] for x in run['usage']) if run['usage'] and all(x.get(key) is not None for x in run['usage']) else None
    proof=dict(status='PASS' if run['status'].startswith('COMPLETED') and len(run['stages'])==6 and calls>0 and valid else 'FAIL',
        network_mode='none',external_connection_blocked=denied,real_local_model=True,mcp_transport='stdio',
        repository='GoogleCloudPlatform/bank-of-anthos',source_revision='db35fea9fd090150e2398106aadb475576f80d94',
        files=analysis['snapshot']['files'],evidence_count=analysis['evidence_count'],index_seconds=index_seconds,
        controller_seconds=run['seconds'],total_seconds=round(time.monotonic()-started,3),
        stages=list(run['stages']),observed_sections=list(run.get('observed_sections',{})),counts=run['counts'],
        field_inventory_records=len(run['facts'].get('section_fields',{}).get('items',[])),
        model_evidence_counts={key:len(value) for key,value in run.get('model_evidence',{}).items()},
        semantic_summary_only=True,neural_embeddings_enabled=False,
        semantic_fields=len(run.get('semantic_fields',[])),
        model_field_annotations=sum(row.get('origin')=='local_model' for row in run.get('semantic_fields',[])),
        unresolved_field_annotations=sum(row.get('status')=='unresolved' for row in run.get('semantic_fields',[])),
        reported_input_tokens=total('input_tokens'),
        reported_output_tokens=total('output_tokens'),
        reasoning_tokens=None,resources=run['runtime_resources'],citation_references_valid=valid,
        model=profile['model_id'],model_sha256=profile['model']['sha256'],weights_changed=False,
        qualification='WORKFLOW_FEASIBILITY_ONLY_NOT_BUSINESS_ACCURACY_CERTIFICATION',gaps=run['gaps'])
    for name in ('memory.peak','memory.events'):
        path=Path('/sys/fs/cgroup')/name
        if path.is_file():proof['container_'+name.replace('.','_')]=path.read_text().strip()
    (destination/'proof.json').write_text(json.dumps(proof,indent=2)+'\n')
    print(json.dumps(proof,indent=2),flush=True)
    assert proof['status']=='PASS'

if __name__=='__main__':asyncio.run(main())
