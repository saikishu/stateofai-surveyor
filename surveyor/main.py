"""FastAPI application — GitHub Repo Surveyor."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import git_sync, storage
from .github_client import GitHubClient
from .models import FetchProgress, RepoStats

# ── Env / config ──────────────────────────────────────────────────────────────
load_dotenv()

GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "")
REPOS_FILE     = Path(os.getenv("REPOS_FILE", "repos.txt"))
PORT           = int(os.getenv("PORT", "8000"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
GIT_USER_NAME  = os.getenv("GIT_USER_NAME",  "OWASP Surveyor Bot")
GIT_USER_EMAIL = os.getenv("GIT_USER_EMAIL", "surveyor@owasp.org")
GITHUB_REPO    = os.getenv("GITHUB_REPO",    "")

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger(__name__)

# ── App setup ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
app = FastAPI(title="GitHub Repo Surveyor", version="1.0.0")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# In-memory fetch progress (single concurrent fetch at a time)
_progress: FetchProgress = FetchProgress(total=0, completed=0, done=True)
_fetch_lock = asyncio.Lock()


# ── Repos-file helpers ────────────────────────────────────────────────────────

def load_repos_txt() -> List[str]:
    """Return a list of 'owner/repo' strings from the flat text file."""
    path = REPOS_FILE if REPOS_FILE.is_absolute() else Path.cwd() / REPOS_FILE
    if not path.exists():
        logger.warning("Repos file not found at %s", path)
        return []
    repos = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "/" in line:
                repos.append(line)
    return repos


# ── GitHub client factory ─────────────────────────────────────────────────────

def _require_token() -> str:
    if not GITHUB_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="GITHUB_TOKEN not set. Copy .env.example → .env and add your token.",
        )
    return GITHUB_TOKEN


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/repos")
async def list_repos() -> List[Dict]:
    """Return all repos from the text file, merged with any cached stats."""
    full_names = load_repos_txt()
    result = []
    for full_name in full_names:
        cached = storage.get(full_name)
        if cached:
            result.append(cached)
        else:
            result.append({
                "full_name": full_name,
                "name": full_name.split("/")[-1],
                "owner": full_name.split("/")[0],
                "fetch_status": "pending",
            })
    return result


@app.get("/api/repos/{owner}/{repo}")
async def get_repo(owner: str, repo: str) -> Dict:
    full_name = f"{owner}/{repo}"
    cached = storage.get(full_name)
    if cached:
        return cached
    raise HTTPException(status_code=404, detail="Not fetched yet — use /api/repos/{owner}/{repo}/fetch")


@app.post("/api/repos/{owner}/{repo}/fetch")
async def fetch_repo(owner: str, repo: str) -> Dict:
    """Fetch (or re-fetch) a single repo from GitHub."""
    token = _require_token()
    full_name = f"{owner}/{repo}"

    async with GitHubClient(token) as client:
        stats = await client.fetch_repo(full_name)

    data = stats.model_dump()
    storage.set(full_name, data)
    return data


@app.post("/api/repos/fetch-all")
async def fetch_all(background_tasks: BackgroundTasks) -> Dict:
    """Trigger background bulk fetch of every repo in the text file."""
    global _progress
    if not _fetch_lock.locked():
        full_names = load_repos_txt()
        _progress = FetchProgress(total=len(full_names), completed=0, done=False)
        background_tasks.add_task(_bulk_fetch_task, full_names)
        return {"started": True, "total": len(full_names)}
    return {"started": False, "message": "A fetch is already in progress"}


@app.get("/api/progress")
async def get_progress() -> FetchProgress:
    return _progress


@app.get("/api/stream/progress")
async def stream_progress():
    """Server-Sent Events stream — pushes progress JSON every second."""
    async def _generate():
        while True:
            data = _progress.model_dump_json()
            yield f"data: {data}\n\n"
            if _progress.done:
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/rate-limit")
async def rate_limit_status() -> Dict:
    token = _require_token()
    async with GitHubClient(token) as client:
        return await client.rate_limit_info()


@app.get("/api/export/csv")
async def export_csv() -> StreamingResponse:
    """Export all cached repo data as a flat CSV."""
    keys = storage.list_keys()
    rows_data = [storage.get(k) for k in keys]
    rows_data = [r for r in rows_data if r]

    if not rows_data:
        raise HTTPException(status_code=404, detail="No cached data to export. Fetch repos first.")

    output = io.StringIO()
    fieldnames = _csv_fieldnames()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in rows_data:
        writer.writerow(_flatten_for_csv(r))

    output.seek(0)
    filename = f"github_survey_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.delete("/api/data/repos/{owner}/{repo}")
async def clear_repo_data(owner: str, repo: str) -> Dict:
    storage.delete(f"{owner}/{repo}")
    return {"cleared": True}


# ── Admin auth ────────────────────────────────────────────────────────────────

def _admin_auth(x_admin_password: str = Header(default="")) -> None:
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="ADMIN_PASSWORD not set in .env")
    if x_admin_password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _repos_path() -> Path:
    return REPOS_FILE if REPOS_FILE.is_absolute() else Path.cwd() / REPOS_FILE


# ── Admin routes ───────────────────────────────────────────────────────────────

@app.get("/admin", include_in_schema=False)
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/api/admin/repos-txt")
async def admin_get_repos(_: None = Depends(_admin_auth)) -> Dict:
    path = _repos_path()
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return {"content": content}


@app.post("/api/admin/repos-txt")
async def admin_save_repos(
    request: Request,
    background_tasks: BackgroundTasks,
    _: None = Depends(_admin_auth),
) -> Dict:
    body = await request.json()
    content = body.get("content", "")

    path = _repos_path()

    # Back up current file
    backup_name = None
    if path.exists():
        backup_dir = storage.DATA_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_name = f"repos_{ts}.txt"
        (backup_dir / backup_name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    # Write new content
    path.write_text(content, encoding="utf-8")

    # Trigger background refresh
    full_names = load_repos_txt()
    refresh_started = False
    if not _fetch_lock.locked() and full_names:
        global _progress
        _progress = FetchProgress(total=len(full_names), completed=0, done=False)
        background_tasks.add_task(_bulk_fetch_task, full_names)
        refresh_started = True

    return {
        "saved": True,
        "backup": backup_name,
        "repos": len(full_names),
        "refresh_started": refresh_started,
    }


@app.get("/api/admin/backups")
async def admin_list_backups(_: None = Depends(_admin_auth)) -> List[Dict]:
    backup_dir = storage.DATA_DIR / "backups"
    if not backup_dir.exists():
        return []
    files = sorted(backup_dir.glob("repos_*.txt"), reverse=True)
    return [
        {
            "name": f.name,
            "size": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
        }
        for f in files[:30]
    ]


@app.get("/api/admin/git-status")
async def admin_git_status(_: None = Depends(_admin_auth)) -> Dict:
    """Return git status for data/ and any open sync PR."""
    return await git_sync.data_status(GITHUB_TOKEN, GITHUB_REPO)


@app.post("/api/admin/git-sync")
async def admin_git_sync(_: None = Depends(_admin_auth)) -> Dict:
    """Commit pending data/ changes to a new branch and open a GitHub PR."""
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=503, detail="GITHUB_TOKEN not set in .env")
    if not GITHUB_REPO:
        raise HTTPException(status_code=503, detail="GITHUB_REPO not set in .env (e.g. owner/repo)")
    return await git_sync.create_sync_pr(GIT_USER_NAME, GIT_USER_EMAIL, GITHUB_TOKEN, GITHUB_REPO)


# ── Background bulk fetch ─────────────────────────────────────────────────────

async def _bulk_fetch_task(full_names: List[str]) -> None:
    global _progress
    async with _fetch_lock:
        token = GITHUB_TOKEN
        if not token:
            _progress.errors.append("GITHUB_TOKEN not configured")
            _progress.done = True
            return

        async with GitHubClient(token) as client:
            for i, full_name in enumerate(full_names):
                _progress.current = full_name
                try:
                    stats = await client.fetch_repo(full_name)
                    storage.set(full_name, stats.model_dump())
                except Exception as exc:
                    logger.error("bulk fetch %s: %s", full_name, exc)
                    _progress.errors.append(f"{full_name}: {exc}")

                _progress.completed = i + 1
                # Small delay to be polite to GitHub API
                await asyncio.sleep(0.5)

        _progress.done = True
        _progress.current = None
        logger.info("Bulk fetch complete: %d/%d repos", _progress.completed, _progress.total)


# ── CSV export helpers ────────────────────────────────────────────────────────

def _csv_fieldnames() -> List[str]:
    return [
        "full_name", "name", "owner", "description", "homepage", "github_url",
        "stars", "forks", "watchers",
        "license_name", "license_spdx", "topics",
        "is_archived", "is_fork", "default_branch",
        "created_at", "updated_at", "pushed_at",
        "age_days", "days_since_push", "activity_status",
        "top_3_languages", "approx_loc", "total_code_bytes",
        "issues_open", "issues_closed",
        "prs_open", "prs_closed", "prs_merged",
        "release_count", "latest_release_tag", "latest_release_date",
        "avg_days_between_releases",
        "contributor_count",
        "commits_last_year", "commits_per_week_avg",
        "vulnerability_alert_count",
        "fetch_status", "fetched_at",
    ]


def _flatten_for_csv(r: Dict) -> Dict:
    return {
        **{k: r.get(k, "") for k in _csv_fieldnames()},
        "topics": "|".join(r.get("topics") or []),
        "top_3_languages": "|".join(r.get("top_3_languages") or []),
    }


def _int(v: Any) -> int:
    try:
        return int(str(v).replace(",", "").replace("K", "000").replace("k", "000"))
    except Exception:
        return 0


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("surveyor.main:app", host="0.0.0.0", port=PORT, reload=True)
