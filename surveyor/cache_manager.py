"""Simple file-based JSON cache with TTL."""
from __future__ import annotations
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _key_path(key: str) -> Path:
    safe = key.replace("/", "__")
    return CACHE_DIR / f"{safe}.json"


def get(key: str, ttl_hours: float = 24) -> Optional[Any]:
    path = _key_path(key)
    if not path.exists():
        return None
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    if age_hours > ttl_hours:
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def set(key: str, value: Any) -> None:
    path = _key_path(key)
    with open(path, "w") as f:
        json.dump(value, f, default=str)


def delete(key: str) -> None:
    path = _key_path(key)
    if path.exists():
        path.unlink()


def list_keys() -> list[str]:
    return [p.stem.replace("__", "/") for p in CACHE_DIR.glob("*.json")]


def cache_age_hours(key: str) -> Optional[float]:
    path = _key_path(key)
    if not path.exists():
        return None
    return (time.time() - path.stat().st_mtime) / 3600
