"""OriginSpan public settings with legacy DIAMOND environment compatibility."""
import os
from pathlib import Path


def getenv(name, default=None):
    if name.startswith("DIAMOND_"):
        return os.environ.get("ORIGINSPAN_" + name[8:], os.environ.get(name, default))
    if name.startswith("ORIGINSPAN_"):
        return os.environ.get(name, os.environ.get("DIAMOND_" + name[11:], default))
    return os.environ.get(name, default)


def required(name):
    value = getenv(name)
    if value is None:
        raise KeyError(name.replace("DIAMOND_", "ORIGINSPAN_"))
    return value


def default_state(root):
    root = Path(root)
    current, legacy = root / ".originspan", root / ".diamond"
    return legacy if not current.exists() and legacy.exists() else current


def canonical_environment(environ):
    for key, value in list(environ.items()):
        if key.startswith("DIAMOND_"):
            environ.setdefault("ORIGINSPAN_" + key[8:], value)


def claim(claims, name):
    current = "originspan_" + name
    return claims[current] if current in claims else claims.get("diamond_" + name)
