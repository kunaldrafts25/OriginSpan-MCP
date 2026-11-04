"""Absolute-path launcher for IDEs whose working directory may be client source.

Production containers use Python isolated mode. This launcher keeps client CWD
and PYTHONPATH out of subsequent dependency/module resolution for local clients.
"""
from pathlib import Path
import os
import sys

ROOT=Path(__file__).resolve().parents[1]
untrusted=Path.cwd().resolve()
sys.path[:]=[p for p in sys.path if p and Path(p).resolve()!=untrusted]
for raw in os.environ.pop("PYTHONPATH","").split(os.pathsep):
    if raw:
        sys.path[:]=[p for p in sys.path if Path(p).resolve()!=Path(raw).resolve()]
sys.path.insert(0,str(ROOT/"src"))
from diamond_mcp.cli import main
from diamond_mcp.compat import default_state

if not any(key in os.environ for key in ("ORIGINSPAN_STATE", "DIAMOND_STATE")) and "--state" not in sys.argv:
    os.environ["ORIGINSPAN_STATE"] = str(default_state(ROOT))

if __name__=="__main__":main()
