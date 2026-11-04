"""Explicit local-only neural adapter. No implicit downloads or API fallback."""
from __future__ import annotations

from .compat import getenv
from pathlib import Path

MODEL = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
PROFILE = {"model": MODEL, "revision": REVISION, "chunking": "diamond-symbol-1", "index": "diamond-1"}


class LocalEmbeddings:
    def __init__(self, path: str):
        import json
        import threading
        self.path = Path(path)
        self._model = None
        self._lock=threading.RLock()
        manifest = self.path / "diamond-model.json"
        if not manifest.is_file() or json.loads(manifest.read_text(encoding="utf-8")) != PROFILE:
            raise ValueError("Local model requires the pinned diamond-model.json manifest; use explicit model-download or supply approved offline files with that manifest")
        self.profile = {**PROFILE, "artifact": str(self.path.resolve())}

    def _load(self):
        with self._lock:
            return self._load_locked()

    @property
    def ready(self):
        return self._model is not None

    def _load_locked(self):
        if self._model is None:
            if not self.path.is_dir():
                raise ValueError("Approved embedding model files are unavailable")
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise ValueError("Install originspan-mcp[semantic] to enable neural retrieval") from exc
            self._model = SentenceTransformer(str(self.path), device="cpu", local_files_only=True, trust_remote_code=False)
            import os
            import torch
            torch.set_num_threads(max(1, min(16, int(getenv("DIAMOND_MODEL_THREADS", "4")))))
        return self._model

    def encode(self, texts, query=False):
        prefix = "query: " if query else "passage: "
        return self._load().encode([prefix + t for t in texts], normalize_embeddings=True,
                                  show_progress_bar=False).tolist()


def download_model(destination: str):
    """Only called by explicit `diamond model-download` operator command."""
    from huggingface_hub import snapshot_download
    snapshot_download(MODEL, revision=REVISION, local_dir=destination,
                      ignore_patterns=["*.onnx", "*.h5", "*.msgpack", "openvino/*"])
    import json
    (Path(destination) / "diamond-model.json").write_text(json.dumps(PROFILE), encoding="utf-8")
