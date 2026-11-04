"""Explicit fetch of pinned public code. No repository code is executed."""
from pathlib import Path
import io
import json
import zipfile
import urllib.request
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from diamond_mcp.sources import accepted, MAX_TOTAL


def main():
    manifest = json.loads((ROOT / "evaluations/manifest.json").read_text())
    for source in manifest["sources"]:
        target = ROOT / ".diamond" / "benchmarks" / source["id"]
        if target.exists():
            print(source["id"], "already present; retained")
            continue
        url = f"https://codeload.github.com/{source['repository']}/zip/{source['commit']}"
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=90) as response:
                    body = response.read(MAX_TOTAL + 1)
                break
            except (OSError, __import__('http.client', fromlist=['IncompleteRead']).IncompleteRead):
                if attempt == 2:
                    raise
                time.sleep(1)
        if len(body) > MAX_TOTAL:
            raise ValueError("Public archive too large")
        target.mkdir(parents=True)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            if sum(m.file_size for m in archive.infolist()) > 250_000_000:
                raise ValueError("Expanded archive too large")
            for member in archive.infolist():
                path = member.filename.split("/", 1)[-1]
                if member.is_dir() or not accepted(path) or member.file_size > 2_000_000:
                    continue
                subset = source["subset"]
                if subset and not any(path == prefix or path.startswith(prefix + "/") for prefix in subset):
                    continue
                destination = target / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(member))
        (target / "diamond-source-revision.txt").write_text(source["commit"])
        print(source["id"], source["commit"], "downloaded")


if __name__ == "__main__":
    main()
