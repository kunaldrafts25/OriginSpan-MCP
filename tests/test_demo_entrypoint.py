import json
import os
from pathlib import Path
import subprocess
import sys


def test_demo_helper_has_safe_spawn_entrypoint(tmp_path):
    root=Path(__file__).resolve().parents[1]
    env={**os.environ,"DIAMOND_NEURAL_ENABLED":"false"}
    result=subprocess.run([sys.executable,str(root/"scripts/setup_demo.py"),"--state",str(tmp_path/"state")],
        env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr
    assert json.loads((tmp_path/"state/demo.json").read_text())["status"]=="COMPLETED"
