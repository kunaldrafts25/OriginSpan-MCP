"""Operator-owned, content-pinned local model profiles."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

NAME = re.compile(r"[a-z][a-z0-9-]{0,47}\Z")
TIERS = {
    "laptop": dict(context=4096, output=768, model_calls=8, tool_calls=20, threads=4, gpu_layers=0),
    "office": dict(context=16384, output=2048, model_calls=16, tool_calls=40, threads=8, gpu_layers=99),
}
CANDIDATES = [
    {"model": "Qwen/Qwen3.5-2B", "tier": "laptop", "role": "workflow prototype, not office-quality certification"},
    {"model": "Qwen/Qwen3.8-27B", "tier": "office", "role": "mixed-document reasoning candidate"},
    {"model": "Qwen/Qwen3-Coder-30B-A3B-Instruct", "tier": "office", "role": "code investigation candidate"},
    {"model": "mistralai/Devstral-Small-2-24B-Instruct-2512", "tier": "office", "role": "alternative code investigation candidate"},
]


def runtime_libraries(folder):
    return {str(p.relative_to(folder)): p for p in sorted(folder.rglob('*'))
            if p.is_file() and (p.suffix.lower() in {'.dll', '.dylib'} or '.so' in p.name)}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def replace_file(temporary: Path, destination: Path):
    import os
    import time
    # Windows scanners/readers may briefly hold a file without delete sharing.
    # Retry only permission errors, preserving the old checkpoint until success.
    for attempt in range(6):
        try:
            os.replace(temporary, destination)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(.025 * (2 ** attempt))


def atomic_json(path: Path, data):
    import os
    import uuid
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        replace_file(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def profile_path(state: Path, name: str) -> Path:
    if not NAME.fullmatch(name):
        raise ValueError("Profile name must be a lowercase identifier, not a path")
    return state / "offline-models" / (name + ".json")


def install(state: Path, name: str, *, model: Path, runtime: Path, model_id: str,
            revision: str, runtime_version: str, license_name: str, tier="laptop", quantization="Q4_K_M"):
    if tier not in TIERS:
        raise ValueError("Unknown hardware tier")
    for value in (model_id, revision, runtime_version, license_name, quantization):
        if not value.strip() or len(value) > 240 or "\n" in value:
            raise ValueError("Supply explicit model/runtime revisions, license and quantization")
    if revision.lower() in {"main", "latest", "master"}:
        raise ValueError("Use an immutable model revision, not main/latest")
    model, runtime = model.resolve(strict=True), runtime.resolve(strict=True)
    if not model.is_file() or not runtime.is_file():
        raise ValueError("Model and runtime must be approved local files")
    with model.open("rb") as f:
        if f.read(4) != b"GGUF":
            raise ValueError("The initial runtime requires a GGUF model")
    value = dict(version=1, name=name, tier=tier, model_id=model_id, revision=revision,
                 license=license_name, quantization=quantization,
                 model=dict(path=str(model), sha256=digest(model)),
                 runtime=dict(path=str(runtime), sha256=digest(runtime), version=runtime_version,
                              libraries={relative: digest(p) for relative, p in runtime_libraries(runtime.parent).items()}),
                 budgets=TIERS[tier], thinking=False,
                 qualification="UNQUALIFIED", notes="Operator-supplied files; license and quality require review")
    path = profile_path(state, name)
    if path.exists():
        raise ValueError("Profile already exists; use a new versioned name")
    atomic_json(path, value)
    return value


def load(state: Path, name: str, *, verify=True):
    path = profile_path(state, name)
    if not path.is_file():
        raise ValueError("Model profile is not installed; use models add with approved local files")
    if path.stat().st_size > 64_000:
        raise ValueError("Model manifest exceeds its budget")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("version") != 1 or value.get("name") != name or value.get("tier") not in TIERS:
        raise ValueError("Invalid model manifest")
    # Budgets are application policy, never arbitrary model/server instructions.
    if value.get("budgets") != TIERS[value["tier"]] or value.get("thinking") is not False:
        raise ValueError("Unsupported profile policy; create an approved standard profile")
    for kind in ("model", "runtime"):
        entry = value.get(kind, {})
        file = Path(entry.get("path", ""))
        if not file.is_absolute() or not file.is_file() or not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", "")):
            raise ValueError("Approved local " + kind + " file is missing or invalid")
        if verify and digest(file) != entry["sha256"]:
            raise ValueError(kind + " hash differs from the approved manifest")
    runtime = Path(value['runtime']['path'])
    libraries = value['runtime'].get('libraries', {})
    actual = set(runtime_libraries(runtime.parent))
    if set(libraries) != actual:
        raise ValueError('Runtime library set differs from the approved manifest')
    for relative, sha in libraries.items():
        file = (runtime.parent / relative).resolve()
        if not file.is_relative_to(runtime.parent) or (verify and digest(file) != sha):
            raise ValueError('Runtime library hash differs from the approved manifest')
    return value


def installed(state: Path):
    folder = state / "offline-models"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]
