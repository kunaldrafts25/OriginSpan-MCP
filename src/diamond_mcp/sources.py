"""Bounded source ingestion. Never imports, builds or executes client code."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

import httpx

MAX_FILE = 2_000_000
MAX_TOTAL = 50_000_000
MAX_FILES = 2000
SUFFIXES = {".html", ".htm", ".py", ".java", ".kt", ".kts", ".sql", ".json", ".yaml", ".yml", ".xml",
            ".properties", ".toml", ".md", ".txt", ".scip", ".ts", ".tsx", ".js", ".go", ".rs", ".cs", ".rb", ".c", ".cpp", ".h"}
SKIP = {".git", ".venv", "venv", "node_modules", "target", "build", "dist", "__pycache__", ".runtime", ".diamond", ".originspan"}
SECRET_NAME = re.compile(r"(^\.env|credential|secret|id_rsa|id_ed25519|\.pem$|\.key$|\.p12$)", re.I)
SECRET = re.compile(r"(?im)(?:-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----|(?:password|passwd|api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*[^\n,;]+|(?:gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}))")


def redact(value: str) -> str:
    return SECRET.sub("[REDACTED]", value)


def checked_path(path: str | Path, roots: tuple[Path, ...], directory=True) -> Path:
    raw = Path(path).absolute()
    if any(p.is_symlink() or p.is_junction() for p in (raw, *raw.parents)):
        raise ValueError("Symlinks and junctions are not supported sources")
    resolved = raw.resolve(strict=True)
    if not any(resolved.is_relative_to(root) for root in roots):
        raise ValueError("Source is outside operator-configured trusted roots")
    if directory != resolved.is_dir():
        raise ValueError("Unexpected source type")
    return resolved


def accepted(relative: str):
    path = PurePosixPath(relative)
    return (not path.is_absolute() and ".." not in path.parts and "\\" not in relative
        and ":" not in relative and not any(p in SKIP or SECRET_NAME.search(p) for p in path.parts)
        and (path.suffix.lower() in SUFFIXES or path.name == "Dockerfile"))


def copy_folder(source: Path, target: Path, cancel=lambda: None):
    """Copy selected artifacts into a new immutable-by-interface snapshot."""
    count = total = 0
    skipped = []
    digest = hashlib.sha256()
    target.mkdir(parents=True, exist_ok=False)
    for current, dirs, files in os.walk(source, followlinks=False):
        cancel()
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not SECRET_NAME.search(d)
                          and not (Path(current) / d).is_symlink() and not (Path(current) / d).is_junction())
        for name in sorted(files):
            cancel()
            path = Path(current) / name
            relative = path.relative_to(source).as_posix()
            if not accepted(relative) or path.is_symlink() or path.is_junction():
                skipped.append(relative)
                continue
            if path.stat().st_size > MAX_FILE:
                skipped.append(relative)
                continue
            # No application code or data files are evaluated. Reject binary and
            # obvious records; supported text artifacts remain operator-approved.
            body = path.read_bytes()
            if path.suffix != ".scip":
                try:
                    body.decode("utf-8-sig")
                except UnicodeDecodeError:
                    skipped.append(relative)
                    continue
            count += 1
            total += len(body)
            if count > MAX_FILES or total > MAX_TOTAL:
                raise ValueError("Snapshot exceeds 2000 files / 50 MB")
            dest = target / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            digest.update(relative.encode() + b"\0" + body)
    if not count:
        raise ValueError("No supported artifacts found")
    return dict(content_sha256=digest.hexdigest(), files=count, bytes=total,
                excluded_count=len(skipped), excluded=skipped[:100])


def extract_zip(body: bytes, target: Path):
    if len(body) > MAX_TOTAL:
        raise ValueError("Archive exceeds 50 MB")
    target.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        members = archive.infolist()
        if len(members) > MAX_FILES * 2 or sum(m.file_size for m in members) > MAX_TOTAL:
            raise ValueError("Expanded archive exceeds limits")
        for member in members:
            name = member.filename
            if member.is_dir():
                continue
            if name.startswith(("/", "\\")) or ".." in PurePosixPath(name).parts or "\\" in name or ":" in name:
                raise ValueError("Unsafe archive path")
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("Archive symlink rejected")
            if not accepted(name) or member.file_size > MAX_FILE:
                continue
            destination = target / name
            if destination.exists():
                raise ValueError("Duplicate archive path")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(member))


def public_github(repository: str, revision: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not re.fullmatch(r"[a-fA-F0-9]{40}", revision):
        raise ValueError("Supply owner/repository and a pinned 40-character commit")
    if any(p in {".", ".."} for p in repository.split("/")):
        raise ValueError("Invalid GitHub repository")
    # Fixed public host, no redirects, credentials, git hooks or subprocesses.
    with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
        with client.stream("GET", f"https://codeload.github.com/{repository}/zip/{revision}") as response:
            response.raise_for_status()
            parts, length = [], 0
            for chunk in response.iter_bytes():
                length += len(chunk)
                if length > MAX_TOTAL:
                    raise ValueError("GitHub archive exceeds limit")
                parts.append(chunk)
    return b"".join(parts)
