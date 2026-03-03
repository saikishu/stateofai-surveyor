"""Git sync utilities — push data/ changes to GitHub as a pull request.

Uses a temporary git worktree so the main working tree (and running server)
are never affected by branch checkouts.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent

# In-memory record of the most recently created sync PR.
# Cleared once the PR is confirmed merged.  Resets on server restart.
_latest_pr: Optional[Dict[str, Any]] = None


# ── Internal git helper ────────────────────────────────────────────────────────

async def _git(*args: str) -> tuple[int, str, str]:
    """Run a git command at REPO_ROOT and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode().strip(), err.decode().strip()


# ── Public API ─────────────────────────────────────────────────────────────────

async def data_status(token: str, repo_slug: str) -> Dict[str, Any]:
    """Return git status for data/ and, if a sync PR is open, its merge state.

    Response schema::

        {
          "pending": bool,          # are there un-pushed data/ changes?
          "count":   int,
          "files":   [str, ...],    # porcelain status lines  e.g. " M data/repos/…"
          "pr":      {              # present only when _latest_pr is set
            "number": int,
            "url":    str,
            "branch": str,
            "state":  "open" | "closed",
            "merged": bool,
          } | None,
        }
    """
    global _latest_pr

    # 1. Check pending data/ changes
    rc, out, _ = await _git("status", "--porcelain", "data/")
    files = [line for line in out.splitlines() if line.strip()]

    # 2. Check latest PR status (if any)
    pr_info: Optional[Dict] = None
    if _latest_pr and token and repo_slug:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"https://api.github.com/repos/{repo_slug}/pulls/{_latest_pr['number']}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
            if resp.status_code == 200:
                data = resp.json()
                merged = bool(data.get("merged"))
                pr_info = {
                    "number": _latest_pr["number"],
                    "url":    _latest_pr["url"],
                    "branch": _latest_pr["branch"],
                    "state":  data.get("state", "unknown"),
                    "merged": merged,
                }
                # Auto-clear once merged so next poll shows "up to date"
                if merged:
                    _latest_pr = None
            else:
                pr_info = {**_latest_pr, "state": "unknown", "merged": False}
        except Exception as exc:
            logger.warning("Could not fetch PR status: %s", exc)
            pr_info = {**_latest_pr, "state": "unknown", "merged": False}

    return {
        "pending": len(files) > 0,
        "count":   len(files),
        "files":   files,
        "pr":      pr_info,
    }


async def create_sync_pr(
    user_name: str,
    user_email: str,
    token: str,
    repo_slug: str,
) -> Dict[str, Any]:
    """Stage data/ changes, commit to a new branch, push, and open a GitHub PR.

    Uses a temporary git worktree so the main working tree is completely
    untouched — the running server keeps serving from its current data/.
    """
    global _latest_pr

    ts     = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"data/sync-{ts}"
    msg    = f"data: sync repo stats {ts}"

    # mkdtemp creates the dir; git worktree add requires it not to exist → remove first
    tmpdir = tempfile.mkdtemp(prefix="surveyor-sync-")
    shutil.rmtree(tmpdir)

    try:
        # ── 1. Create worktree on a new branch ────────────────────────────────
        rc, out, err = await _git("worktree", "add", tmpdir, "-b", branch)
        if rc != 0:
            raise RuntimeError(f"git worktree add failed: {err or out}")

        # ── 2. Copy current data/ files into the worktree ─────────────────────
        data_src = REPO_ROOT / "data"
        data_dst = Path(tmpdir) / "data"
        shutil.copytree(str(data_src), str(data_dst), dirs_exist_ok=True)

        # ── 3. Configure commit identity inside worktree ──────────────────────
        await _git("-C", tmpdir, "config", "user.name",  user_name)
        await _git("-C", tmpdir, "config", "user.email", user_email)

        # ── 4. Stage data/ only ───────────────────────────────────────────────
        rc, _, err = await _git("-C", tmpdir, "add", "data/")
        if rc != 0:
            raise RuntimeError(f"git add data/ failed: {err}")

        # Check there is actually something staged
        rc, staged, _ = await _git("-C", tmpdir, "diff", "--cached", "--name-only")
        if not staged.strip():
            logger.info("git-sync: nothing to commit in data/")
            return {"status": "nothing_to_commit"}

        files_synced = [f.strip() for f in staged.splitlines() if f.strip()]

        # ── 5. Commit ─────────────────────────────────────────────────────────
        rc, _, err = await _git("-C", tmpdir, "commit", "-m", msg)
        if rc != 0:
            raise RuntimeError(f"git commit failed: {err}")

        # ── 6. Push via HTTPS token auth ──────────────────────────────────────
        push_url = f"https://x-access-token:{token}@github.com/{repo_slug}.git"
        rc, _, err = await _git("-C", tmpdir, "push", push_url, f"HEAD:{branch}")
        if rc != 0:
            raise RuntimeError(f"git push failed: {err}")

        # ── 7. Open GitHub PR ─────────────────────────────────────────────────
        pr_url: Optional[str] = None
        pr_number: Optional[int] = None

        body_lines = ["Automated data sync from the admin panel.", "", "**Files changed:**", ""]
        body_lines += [f"- `{f}`" for f in files_synced]
        body_lines += ["", f"**Timestamp:** `{ts}`"]

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://api.github.com/repos/{repo_slug}/pulls",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                json={
                    "title": msg,
                    "body":  "\n".join(body_lines),
                    "head":  branch,
                    "base":  "main",
                },
            )

        if resp.status_code == 201:
            pr_data   = resp.json()
            pr_url    = pr_data.get("html_url")
            pr_number = pr_data.get("number")
            _latest_pr = {
                "number": pr_number,
                "url":    pr_url,
                "branch": branch,
            }
            logger.info("git-sync: PR #%s opened at %s", pr_number, pr_url)
        else:
            logger.error("GitHub PR creation failed: %s %s", resp.status_code, resp.text)
            raise RuntimeError(f"PR creation failed ({resp.status_code}): {resp.text[:200]}")

    finally:
        # Always clean up the worktree (ignore errors — git may have already cleaned up)
        try:
            await _git("worktree", "remove", "--force", tmpdir)
        except Exception:
            pass
        if Path(tmpdir).exists():
            shutil.rmtree(tmpdir, ignore_errors=True)

    return {
        "status":       "synced",
        "branch":       branch,
        "pr_url":       pr_url,
        "pr_number":    pr_number,
        "files_synced": files_synced,
    }
