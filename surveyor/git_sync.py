"""Git sync utilities — push data/ changes to GitHub via the Git Data API.

Commit author identity (name + email) comes entirely from GIT_USER_NAME /
GIT_USER_EMAIL in .env, not from the local git config or token owner.

Flow: detect local changes (git status) → create blobs → build tree →
      create commit (custom author) → create branch ref → open PR.
No git worktree or local commits are made.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
_GH_API   = "https://api.github.com"

# In-memory record of the most recently created sync PR.
# Cleared once the PR is confirmed merged.  Resets on server restart.
_latest_pr: Optional[Dict[str, Any]] = None


# ── Internal helpers ───────────────────────────────────────────────────────────

async def _git(*args: str) -> tuple[int, str, str]:
    """Run a git command at REPO_ROOT."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode().strip(), err.decode().strip()


def _gh_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _gh(client: httpx.AsyncClient, method: str, path: str, token: str, **kwargs) -> Dict:
    resp = await client.request(
        method,
        f"{_GH_API}{path}",
        headers=_gh_headers(token),
        **kwargs,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"GitHub API {method} {path} → {resp.status_code}: {resp.text[:300]}")
    return resp.json()


# ── Public API ─────────────────────────────────────────────────────────────────

async def _advance_local_head() -> None:
    """After a PR is merged, bring the local HEAD and index in sync with remote main.

    Uses git fetch + reset --mixed so:
    - Local HEAD and index advance to the merged remote state.
    - Working tree (live data files written by the app) is untouched.
    - Next `git status data/` only shows files written *after* the merged PR.
    """
    rc, _, err = await _git("fetch", "origin", "main")
    if rc != 0:
        logger.warning("git fetch failed after PR merge: %s", err)
        return
    rc, _, err = await _git("reset", "--mixed", "FETCH_HEAD")
    if rc != 0:
        logger.warning("git reset --mixed failed after PR merge: %s", err)


async def data_status(token: str, repo_slug: str) -> Dict[str, Any]:
    """Return git status for data/ and, if a sync PR is open, its merge state.

    When a PR is detected as merged, the local repo is fast-forwarded to the
    remote main (index only — working tree untouched) before re-checking status,
    so the panel correctly reflects only changes made *after* the merged PR.
    """
    global _latest_pr

    # ── Check latest PR status first ──────────────────────────────────────────
    pr_info: Optional[Dict] = None
    if _latest_pr and token and repo_slug:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                data = await _gh(client, "GET",
                                 f"/repos/{repo_slug}/pulls/{_latest_pr['number']}",
                                 token)
            merged = bool(data.get("merged"))
            if merged:
                # Advance local HEAD/index to the merged remote state, then
                # re-run git status so it compares against the new baseline.
                await _advance_local_head()
                _latest_pr = None
                # pr_info intentionally left None — UI shows "Up to date" or
                # fresh pending count after the pull
            else:
                pr_info = {
                    "number": _latest_pr["number"],
                    "url":    _latest_pr["url"],
                    "branch": _latest_pr["branch"],
                    "state":  data.get("state", "unknown"),
                    "merged": False,
                }
        except Exception as exc:
            logger.warning("Could not fetch PR status: %s", exc)
            pr_info = {**_latest_pr, "state": "unknown", "merged": False}

    # ── Fresh git status (after potential HEAD advance) ───────────────────────
    rc, out, _ = await _git("status", "--porcelain", "data/")
    files = [line for line in out.splitlines() if line.strip()]

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
    """Commit pending data/ changes and open a GitHub PR via the Git Data API.

    Author identity is set from user_name / user_email (sourced from .env),
    completely independent of the local git config or the token owner's profile.
    The local working tree is never modified.
    """
    global _latest_pr

    ts     = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"data/sync-{ts}"
    msg    = f"data: sync repo stats {ts}"
    now    = datetime.now(timezone.utc).isoformat()

    # ── 1. Detect changed data/ files via local git status ────────────────────
    rc, out, _ = await _git("status", "--porcelain", "data/")
    changed: List[tuple[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        xy   = line[:2]          # e.g. " M", "??", " D"
        path = line[3:].strip()  # e.g. "data/repos/owner/repo.json"
        if path.startswith("data/"):
            changed.append((xy, path))

    if not changed:
        return {"status": "nothing_to_commit"}

    async with httpx.AsyncClient(timeout=30.0) as client:

        # ── 2. Get base commit + tree SHA from main ────────────────────────────
        ref_data    = await _gh(client, "GET", f"/repos/{repo_slug}/git/ref/heads/main", token)
        base_sha    = ref_data["object"]["sha"]
        commit_data = await _gh(client, "GET", f"/repos/{repo_slug}/git/commits/{base_sha}", token)
        base_tree   = commit_data["tree"]["sha"]

        # ── 3. Create blobs for each modified/added file ───────────────────────
        tree_items: List[Dict] = []
        files_synced: List[str] = []

        for xy, path in changed:
            is_deleted = xy.strip() == "D"
            files_synced.append(path)

            if is_deleted:
                # Null SHA removes the file from the tree
                tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            else:
                raw = (REPO_ROOT / path).read_bytes()
                blob = await _gh(client, "POST", f"/repos/{repo_slug}/git/blobs", token,
                                 json={"content": base64.b64encode(raw).decode(), "encoding": "base64"})
                tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})

        # ── 4. Create new tree ─────────────────────────────────────────────────
        new_tree = await _gh(client, "POST", f"/repos/{repo_slug}/git/trees", token,
                             json={"base_tree": base_tree, "tree": tree_items})

        # ── 5. Create commit with author from .env ─────────────────────────────
        identity   = {"name": user_name, "email": user_email, "date": now}
        new_commit = await _gh(client, "POST", f"/repos/{repo_slug}/git/commits", token,
                               json={
                                   "message":   msg,
                                   "tree":      new_tree["sha"],
                                   "parents":   [base_sha],
                                   "author":    identity,
                                   "committer": identity,
                               })

        # ── 6. Create branch ref ───────────────────────────────────────────────
        await _gh(client, "POST", f"/repos/{repo_slug}/git/refs", token,
                  json={"ref": f"refs/heads/{branch}", "sha": new_commit["sha"]})

        # ── 7. Open PR ─────────────────────────────────────────────────────────
        body_lines  = ["Automated data sync from the admin panel.", "", "**Files changed:**", ""]
        body_lines += [f"- `{f}`" for f in files_synced]
        body_lines += ["", f"**Timestamp:** `{ts}`"]

        pr = await _gh(client, "POST", f"/repos/{repo_slug}/pulls", token,
                       json={
                           "title": msg,
                           "body":  "\n".join(body_lines),
                           "head":  branch,
                           "base":  "main",
                       })

    pr_url    = pr["html_url"]
    pr_number = pr["number"]
    _latest_pr = {"number": pr_number, "url": pr_url, "branch": branch}
    logger.info("git-sync: PR #%s opened at %s", pr_number, pr_url)

    return {
        "status":       "synced",
        "branch":       branch,
        "pr_url":       pr_url,
        "pr_number":    pr_number,
        "files_synced": files_synced,
    }
