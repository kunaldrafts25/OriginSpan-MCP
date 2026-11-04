"""Create a local ambiguity demo using only this standalone project's fixtures."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from diamond_mcp.engine import Config, Actor
from diamond_mcp.service import DiscoveryService as Engine

def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--state",type=Path,default=ROOT/".diamond")
    args=parser.parse_args()
    source=ROOT/"evaluations/fixtures/ambiguous"
    model=args.state/"models/multilingual-e5-small"
    config=Config(args.state.resolve(),(source,),model_path=str(model) if (model/"diamond-model.json").exists() else None)
    engine=Engine(config)
    actor=Actor("local-operator",frozenset({"local"}))
    existing=next((a for a in engine.applications(actor,"local") if a["name"]=="Ambiguous discovery demo"),None)
    app=existing["id"] if existing else engine.register(actor,"local","Ambiguous discovery demo",source)["id"]
    pending=next((j for j in engine.list_analyses(actor,"local",app)["analyses"] if j["status"] in {"QUEUED","RUNNING"}),None)
    if pending:
        engine.pipeline.run("demo-recovery",pending["id"])
        job=engine.job(actor,"local",app,pending["id"])
    else:
        job=engine.start(actor,"local",app,background=False)
    result=dict(application_id=app,analysis_id=job["id"],status=job["status"],neural=job.get("neural"))
    if job["status"]=="COMPLETED":
        result["query"]="when can an agreement be extended"
        result["retrieval"]=engine.search(actor,"local",app,job["id"],result["query"])
    else:
        result["error"]=job.get("error")
    (args.state/"demo.json").write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!="retrieval"},indent=2))
    raise SystemExit(0 if job["status"]=="COMPLETED" else 1)


if __name__=="__main__":main()
