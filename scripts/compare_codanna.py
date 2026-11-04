"""Opt-in public-fixture baseline. Downloads a checksum-verified tool, not repo scripts.

Uses disposable copies of the identical approved fixture files. Raw outputs are
retained for review; unsupported result schemas are never scored as failures.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def download():
    if platform.system() != "Windows":
        raise ValueError("This downloader currently targets the Windows comparison binary")
    name = "codanna-0.17.1-windows-x64.zip"
    base = "https://github.com/bartolli/codanna/releases/download/v0.17.1/"
    with urllib.request.urlopen(base + name, timeout=90) as response:
        body = response.read(100_000_001)
    if len(body) > 100_000_000:
        raise ValueError("Binary archive exceeds limit")
    with urllib.request.urlopen(base + name + ".sha256", timeout=30) as response:
        checksum = response.read().decode().split()[0]
    if hashlib.sha256(body).hexdigest() != checksum:
        raise ValueError("Codanna archive checksum mismatch")
    dest = ROOT / ".diamond/tools/codanna"
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        for member in archive.infolist():
            filename = Path(member.filename).name
            if filename.lower().endswith((".exe", ".dll")) or filename in {"LICENSE", "NOTICE"}:
                (dest / filename).write_bytes(archive.read(member))
    exe = dest / "codanna.exe"
    if not exe.is_file():
        raise ValueError("Codanna executable absent from release archive")
    return exe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--executable", type=Path)
    args = parser.parse_args()
    exe = args.executable or (download() if args.download else ROOT / ".diamond/tools/codanna/codanna.exe")
    exe = exe.resolve(strict=True)
    manifest = json.loads((ROOT / "evaluations/manifest.json").read_text())
    out = ROOT / "artifacts/codanna"
    out.mkdir(parents=True, exist_ok=True)
    report = dict(tool="Codanna", version="0.17.1", platform=platform.platform(), tasks=[], indexing=[],
                  manifest_sha256=hashlib.sha256((ROOT / "evaluations/manifest.json").read_bytes()).hexdigest(),
                  context_budget="10 matches; 160000 output bytes recorded", model="tool default; inspect raw index output")
    for source in [s["id"] for s in manifest["sources"]] + ["ambiguity"]:
        original = ROOT / ("evaluations/fixtures/ambiguous" if source == "ambiguity" else ".diamond/benchmarks/" + source)
        if not original.exists():
            continue
        cwd = ROOT / ".diamond" / "codanna-fixtures" / source
        if not cwd.exists():
            shutil.copytree(original, cwd)
        def run(command, timeout=300):
            started = time.perf_counter()
            try:
                result = subprocess.run([str(exe), *command], cwd=cwd, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout, shell=False)
                return dict(status="OK" if result.returncode == 0 else "ERROR", returncode=result.returncode,
                            seconds=time.perf_counter()-started, stdout=result.stdout[:160000], stderr=result.stderr[:16000])
            except subprocess.TimeoutExpired:
                return dict(status="UNTESTED", reason="Tool process exceeded configured timeout", seconds=time.perf_counter()-started)
        initialized = run(["init"])
        indexed = run(["index", "."])
        report["indexing"].append(dict(source=source, init=initialized, index=indexed))
        if indexed["status"] != "OK":
            continue
        for task in [t for t in manifest["tasks"] if t["source"] == source]:
            result = run(["mcp", "semantic_search_with_context", "query:" + task["query"], "limit:10", "--json"])
            result.update(task=task["id"], source=source, returned_bytes=len(result.get("stdout", "").encode()),
                          scoring="RAW_OUTPUT_REQUIRES_REVIEW; no invented comparable score")
            report["tasks"].append(result)
    (out / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(out / "results.json")


if __name__ == "__main__":
    main()
