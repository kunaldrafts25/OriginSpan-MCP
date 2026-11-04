from pathlib import Path
import sys
import json
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from diamond_mcp.service import DiscoveryService,Config,Actor

def main():
    root=ROOT/".runtime"/uuid.uuid4().hex
    source=root/"input"
    source.mkdir(parents=True)
    (source/"model.py").write_text('class Customer:\n def lookup(self, C_ID):\n  sql="SELECT c.birth_date FROM customer c WHERE c.id = :id"\n  return C_ID\n')
    (source/"schema.sql").write_text('CREATE TABLE customer (id INT PRIMARY KEY, birth_date DATE);')
    e=DiscoveryService(Config(root/"state",(source,)))
    a=Actor("test",frozenset({"p"}))
    app=e.register(a,"p","Smoke",source)["id"]
    job=e.start(a,"p",app,background=False)
    print(json.dumps(job,indent=2))
    print(json.dumps(e.search(a,"p",app,job["id"],"birth_date"),indent=2))
    print(json.dumps(e.sql_inventory(a,"p",app,job["id"]),indent=2))
    raise SystemExit(0 if job["status"]=="COMPLETED" else 1)

if __name__=="__main__":main()
