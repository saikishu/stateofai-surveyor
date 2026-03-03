"""Persistent file-based storage — data/ at project root, organised by concern."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# Project root is one level above this file (surveyor/)
DATA_DIR = Path(__file__).parent.parent / "data"
REPOS_DIR = DATA_DIR / "repos"


def _repo_path(full_name: str) -> Path:
    """data/repos/{owner}/{repo}.json"""
    owner, repo = full_name.strip().split("/", 1)
    return REPOS_DIR / owner / f"{repo}.json"


def get(full_name: str) -> Optional[Dict]:
    path = _repo_path(full_name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def set(full_name: str, value: Dict) -> None:
    path = _repo_path(full_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, default=str, indent=2), encoding="utf-8")


def delete(full_name: str) -> None:
    path = _repo_path(full_name)
    if path.exists():
        path.unlink()


def list_keys() -> List[str]:
    if not REPOS_DIR.exists():
        return []
    return [
        f"{p.parent.name}/{p.stem}"
        for p in REPOS_DIR.glob("*/*.json")
    ]
