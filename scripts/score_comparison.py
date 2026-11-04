"""Score observed Codanna symbol paths against the frozen retrieval tasks.

Scoring is at the same top-10 relevant-file unit used for DIAMOND. Raw output is
preserved. Returned bytes include the different tool's own context envelope;
matches are comparable, byte budgets are caps rather than identical payloads.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT / "evaluations/manifest.json").read_text())
raw = json.loads((ROOT / "artifacts/codanna/results.json").read_text())
expected = {t["id"]: set(t["relevant_paths"]) for t in manifest["tasks"]}
scored = []
for task in raw["tasks"]:
    if task["status"] != "OK":
        scored.append(dict(task=task["task"], status="UNTESTED", reason=task.get("reason", task.get("stderr"))))
        continue
    data = json.loads(task["stdout"])
    results = data.get("data")
    if not isinstance(results, list):
        scored.append(dict(task=task["task"], status="UNTESTED", reason="Unexpected result schema"))
        continue
    paths = [i.get("symbol", {}).get("file_path", "").replace("\\", "/").removeprefix("./") for i in results[:10]]
    relevant = expected[task["task"]]
    rank = next((index for index, path in enumerate(paths, 1) if path in relevant), None)
    scored.append(dict(task=task["task"], status="SCORED", relevant_path_recall_at_10=len(set(paths) & relevant)/len(relevant),
        reciprocal_rank=1/rank if rank else 0, paths=paths, seconds=task["seconds"], returned_bytes=task["returned_bytes"], tool_calls=1))
report = dict(tool="Codanna 0.17.1", tasks=scored,
    limitations=["Different default embedding models and chunks", "No cross-tool business-answer or symbol-precision claim",
                 "Codanna symbol semantic search was run; its separate document-search tool was not combined",
                 "Top-10 match budget shared, 160KB output cap; actual payload sizes differ",
                 "Small development tasks; not an independent or universal ranking"])
(ROOT / "artifacts/codanna/scored.json").write_text(json.dumps(report, indent=2))
print(json.dumps(scored, indent=2))
