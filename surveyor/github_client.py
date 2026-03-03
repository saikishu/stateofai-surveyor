"""GitHub API client — GraphQL for bulk stats, REST for granular details."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from .models import ContributorInfo, ReleaseEntry, RepoStats, SecurityAdvisory

logger = logging.getLogger(__name__)

# ── LOC estimation ────────────────────────────────────────────────────────────
_BPL: Dict[str, int] = {
    "Python": 35, "JavaScript": 40, "TypeScript": 42,
    "Go": 45, "Rust": 40, "Java": 55, "C": 50, "C++": 52,
    "C#": 50, "Ruby": 32, "PHP": 45, "Shell": 35, "Bash": 35,
    "Swift": 45, "Kotlin": 50, "Scala": 48, "R": 35,
    "HTML": 60, "CSS": 45, "YAML": 25, "JSON": 30, "Markdown": 40,
    "Dockerfile": 30, "Makefile": 30, "Vue": 45, "Svelte": 45,
    "Elixir": 35, "Haskell": 38, "Clojure": 35, "Lua": 35,
}
_DEFAULT_BPL = 40


def _estimate_loc(languages: Dict[str, int]) -> int:
    return sum(b // _BPL.get(lang, _DEFAULT_BPL) for lang, b in languages.items())


def _activity_status(days_since_push: int, is_archived: bool) -> str:
    if is_archived:
        return "archived"
    if days_since_push <= 30:
        return "active"
    if days_since_push <= 90:
        return "recent"
    if days_since_push <= 365:
        return "stale"
    return "dormant"


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.rstrip("Z")).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_last_page(link_header: str) -> Optional[int]:
    """Extract last page number from a GitHub Link header."""
    m = re.search(r'[?&]page=(\d+)[^>]*>;\s*rel="last"', link_header)
    return int(m.group(1)) if m else None


def _compute_commit_trends(s: RepoStats) -> None:
    """Derive 30d/90d counts and trend direction from weekly_commits (no API call)."""
    w = s.weekly_commits  # oldest → newest, 52 entries
    if not w:
        return
    s.commits_30d = sum(w[-5:])     # ~35 days, generous window
    s.commits_90d = sum(w[-13:])    # ~91 days

    last4  = sum(w[-4:])  / 4  if len(w) >= 4  else 0
    prev4  = sum(w[-8:-4]) / 4 if len(w) >= 8  else 0

    if prev4 == 0:
        pct = 100.0 if last4 > 0 else 0.0
    else:
        pct = (last4 - prev4) / prev4 * 100

    s.commit_growth_pct = round(pct, 1)

    if pct >= 50:
        s.commit_trend = "surging"
    elif pct >= 10:
        s.commit_trend = "rising"
    elif pct <= -50:
        s.commit_trend = "stalled"
    elif pct <= -10:
        s.commit_trend = "declining"
    else:
        s.commit_trend = "flat"


# ── GraphQL query ─────────────────────────────────────────────────────────────
_GQL_QUERY = """
query RepoDetails($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    nameWithOwner
    name
    description
    homepageUrl
    url
    stargazerCount
    forkCount
    isArchived
    isFork
    createdAt
    updatedAt
    pushedAt
    defaultBranchRef {
      name
      target {
        ... on Commit {
          history { totalCount }
        }
      }
    }
    watchers { totalCount }
    licenseInfo { name spdxId }
    repositoryTopics(first: 20) {
      nodes { topic { name } }
    }
    primaryLanguage { name }
    languages(first: 6, orderBy: {field: SIZE, direction: DESC}) {
      totalSize
      edges { size node { name } }
    }
    openIssues:   issues(states: OPEN)   { totalCount }
    closedIssues: issues(states: CLOSED) { totalCount }
    openPRs:      pullRequests(states: OPEN)   { totalCount }
    closedPRs:    pullRequests(states: CLOSED) { totalCount }
    mergedPRs:    pullRequests(states: MERGED) { totalCount }
    releases { totalCount }
    latestRelease { tagName name publishedAt }
  }
}
"""


class GitHubClient:
    _BASE = "https://api.github.com"
    _GQL  = "https://api.github.com/graphql"

    def __init__(self, token: str):
        self._token = token
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "StateOfAI-Surveyor/1.0",
        }
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "GitHubClient":
        self._client = httpx.AsyncClient(
            headers=self._headers, timeout=30.0, follow_redirects=True
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    # ── Low-level helpers ─────────────────────────────────────────────────────

    async def _gql(self, query: str, variables: Dict) -> Dict:
        assert self._client
        resp = await self._client.post(
            self._GQL, json={"query": query, "variables": variables}
        )
        resp.raise_for_status()
        body = resp.json()
        if "errors" in body:
            raise ValueError(f"GraphQL errors: {body['errors']}")
        return body["data"]

    async def _rest(
        self,
        path: str,
        params: Optional[Dict] = None,
        accept: Optional[str] = None,
        return_response: bool = False,
    ) -> Any:
        assert self._client
        headers: Dict[str, str] = {}
        if accept:
            headers["Accept"] = accept

        url = f"{self._BASE}{path}"
        resp = await self._client.get(url, params=params, headers=headers)

        remaining = int(resp.headers.get("X-RateLimit-Remaining", "100"))
        if remaining < 5:
            reset_ts = int(resp.headers.get("X-RateLimit-Reset", "0"))
            wait = max(1, reset_ts - int(datetime.now(timezone.utc).timestamp()) + 2)
            logger.warning("Rate-limit low (%d left), sleeping %ds", remaining, wait)
            await asyncio.sleep(min(wait, 120))

        if resp.status_code == 204:
            return resp if return_response else None
        if resp.status_code == 404:
            return resp if return_response else None
        if resp.status_code == 202:
            return None   # stats computing — caller retries
        resp.raise_for_status()
        return resp if return_response else resp.json()

    async def rate_limit_info(self) -> Dict:
        data = await self._rest("/rate_limit")
        return data or {}

    # ── Orchestrator ─────────────────────────────────────────────────────────

    async def fetch_repo(self, full_name: str, csv_row: Optional[Dict] = None) -> RepoStats:
        parts = full_name.strip().split("/", 1)
        if len(parts) != 2:
            return RepoStats(
                full_name=full_name, name=full_name, owner="",
                github_url=f"https://github.com/{full_name}",
                fetch_status="error",
                fetch_error=f"Cannot parse owner/repo from '{full_name}'",
                csv_row=csv_row,
            )

        owner, name = parts
        stats = RepoStats(
            full_name=full_name, name=name, owner=owner,
            github_url=f"https://github.com/{full_name}",
            csv_row=csv_row, fetch_status="fetching",
        )

        try:
            await self._apply_graphql(stats, owner, name)
            await self._apply_commit_activity(stats, owner, name)
            _compute_commit_trends(stats)                           # pure computation
            await self._apply_contributors(stats, owner, name)
            await self._apply_contributor_stats(stats, owner, name)
            await self._apply_releases(stats, owner, name)
            await self._apply_first_commit(stats, owner, name)
            await self._apply_security_advisories(stats, owner, name)
            await self._apply_issue_trends(stats, owner, name)
            stats.fetch_status = "complete"
        except httpx.HTTPStatusError as exc:
            msg = f"HTTP {exc.response.status_code}: {exc.request.url}"
            logger.error("fetch_repo %s → %s", full_name, msg)
            stats.fetch_status = "error"
            stats.fetch_error = msg
        except Exception as exc:
            logger.exception("fetch_repo %s failed", full_name)
            stats.fetch_status = "error"
            stats.fetch_error = str(exc)

        stats.fetched_at = datetime.now(timezone.utc).isoformat()
        return stats

    # ── GraphQL ───────────────────────────────────────────────────────────────

    async def _apply_graphql(self, s: RepoStats, owner: str, name: str) -> None:
        data = await self._gql(_GQL_QUERY, {"owner": owner, "name": name})
        r = data["repository"]

        s.description = r.get("description")
        s.homepage = r.get("homepageUrl") or None
        s.stars = r.get("stargazerCount", 0)
        s.forks = r.get("forkCount", 0)
        s.watchers = (r.get("watchers") or {}).get("totalCount", 0)
        s.is_archived = r.get("isArchived", False)
        s.is_fork = r.get("isFork", False)

        branch_ref = r.get("defaultBranchRef") or {}
        s.default_branch = branch_ref.get("name", "main")

        # Total commit count from history
        try:
            s.total_commits = (
                branch_ref.get("target", {})
                .get("history", {})
                .get("totalCount", 0)
            )
        except Exception:
            pass

        lic = r.get("licenseInfo") or {}
        s.license_name = lic.get("name")
        s.license_spdx = lic.get("spdxId")

        s.topics = [
            n["topic"]["name"]
            for n in (r.get("repositoryTopics") or {}).get("nodes", [])
        ]

        lang_data = r.get("languages") or {}
        s.total_code_bytes = lang_data.get("totalSize", 0)
        s.languages = {e["node"]["name"]: e["size"] for e in lang_data.get("edges", [])}
        s.top_3_languages = list(s.languages.keys())[:3]
        s.approx_loc = _estimate_loc(s.languages)

        s.issues_open   = (r.get("openIssues")   or {}).get("totalCount", 0)
        s.issues_closed = (r.get("closedIssues") or {}).get("totalCount", 0)
        s.prs_open      = (r.get("openPRs")   or {}).get("totalCount", 0)
        s.prs_closed    = (r.get("closedPRs") or {}).get("totalCount", 0)
        s.prs_merged    = (r.get("mergedPRs") or {}).get("totalCount", 0)

        s.release_count = (r.get("releases") or {}).get("totalCount", 0)
        lr = r.get("latestRelease") or {}
        s.latest_release_tag  = lr.get("tagName")
        s.latest_release_date = lr.get("publishedAt")

        now = datetime.now(timezone.utc)
        s.created_at = r.get("createdAt", "")
        s.updated_at = r.get("updatedAt", "")
        s.pushed_at  = r.get("pushedAt", "")

        created = _parse_dt(s.created_at)
        pushed  = _parse_dt(s.pushed_at)
        if created:
            s.age_days = (now - created).days
        if pushed:
            s.days_since_push = (now - pushed).days
        s.activity_status = _activity_status(s.days_since_push, s.is_archived)

    # ── Commit activity (52 weeks) ────────────────────────────────────────────

    async def _apply_commit_activity(self, s: RepoStats, owner: str, name: str) -> None:
        for attempt in range(4):
            raw = await self._rest(f"/repos/{owner}/{name}/stats/commit_activity")
            if raw is None and attempt < 3:
                await asyncio.sleep(3)
                continue
            if isinstance(raw, list) and raw:
                s.weekly_commits    = [w["total"] for w in raw]
                s.weekly_timestamps = [w["week"]  for w in raw]
                s.commits_last_year = sum(s.weekly_commits)
                nonzero = [c for c in s.weekly_commits if c > 0]
                s.commits_per_week_avg = sum(nonzero) / len(nonzero) if nonzero else 0.0
                break

    # ── Contributors (basic list) ─────────────────────────────────────────────

    async def _apply_contributors(self, s: RepoStats, owner: str, name: str) -> None:
        raw = await self._rest(
            f"/repos/{owner}/{name}/contributors",
            params={"per_page": 100, "anon": "false"},
        )
        if not isinstance(raw, list):
            return
        s.contributor_count = len(raw)
        s.top_contributors = [
            ContributorInfo(
                login=c.get("login", "anon"),
                avatar_url=c.get("avatar_url", ""),
                html_url=c.get("html_url", ""),
                contributions=c.get("contributions", 0),
            )
            for c in raw[:10]
        ]

    # ── Contributor detail stats (additions / deletions) ─────────────────────

    async def _apply_contributor_stats(self, s: RepoStats, owner: str, name: str) -> None:
        """Enrich top_contributors with additions/deletions from stats/contributors."""
        for attempt in range(4):
            raw = await self._rest(f"/repos/{owner}/{name}/stats/contributors")
            if raw is None and attempt < 3:
                await asyncio.sleep(3)
                continue
            if not isinstance(raw, list):
                break

            # Build lookup: login → {additions, deletions, commits}
            detail: Dict[str, Dict] = {}
            for entry in raw:
                login = (entry.get("author") or {}).get("login", "")
                if not login:
                    continue
                total_a = sum(w.get("a", 0) for w in entry.get("weeks", []))
                total_d = sum(w.get("d", 0) for w in entry.get("weeks", []))
                total_c = entry.get("total", 0)
                detail[login] = {"additions": total_a, "deletions": total_d, "commits_count": total_c}

            # Merge into existing top_contributors
            enriched = []
            for c in s.top_contributors:
                d = detail.get(c.login, {})
                enriched.append(ContributorInfo(
                    login=c.login,
                    avatar_url=c.avatar_url,
                    html_url=c.html_url,
                    contributions=c.contributions,
                    additions=d.get("additions", 0),
                    deletions=d.get("deletions", 0),
                    commits_count=d.get("commits_count", c.contributions),
                ))
            s.top_contributors = enriched
            break

    # ── First commit date ─────────────────────────────────────────────────────

    async def _apply_first_commit(self, s: RepoStats, owner: str, name: str) -> None:
        """Fetch the oldest commit on the default branch via Link header pagination."""
        path = f"/repos/{owner}/{name}/commits"
        resp = await self._rest(
            path,
            params={"per_page": 1, "sha": s.default_branch},
            return_response=True,
        )
        if resp is None or not hasattr(resp, "headers"):
            return

        link = resp.headers.get("Link", "")
        last_page = _parse_last_page(link)

        if last_page and last_page > 1:
            oldest_resp = await self._rest(
                path,
                params={"per_page": 1, "sha": s.default_branch, "page": last_page},
            )
            if isinstance(oldest_resp, list) and oldest_resp:
                date_str = (
                    oldest_resp[0].get("commit", {})
                    .get("author", {})
                    .get("date")
                )
                if date_str:
                    s.first_commit_date = date_str
                    d = _parse_dt(date_str)
                    if d:
                        s.commit_span_days = (datetime.now(timezone.utc) - d).days
        elif isinstance(resp.json() if hasattr(resp, "json") else [], list):
            # Single page — first commit is in this response
            data = resp.json() if hasattr(resp, "json") else []
            if isinstance(data, list) and data:
                date_str = data[0].get("commit", {}).get("author", {}).get("date")
                if date_str:
                    s.first_commit_date = date_str
                    d = _parse_dt(date_str)
                    if d:
                        s.commit_span_days = (datetime.now(timezone.utc) - d).days

    # ── Releases ──────────────────────────────────────────────────────────────

    async def _apply_releases(self, s: RepoStats, owner: str, name: str) -> None:
        raw = await self._rest(
            f"/repos/{owner}/{name}/releases", params={"per_page": 30}
        )
        if not isinstance(raw, list):
            return
        entries = [
            ReleaseEntry(
                tag_name=rel.get("tag_name", ""),
                name=rel.get("name") or rel.get("tag_name"),
                published_at=rel.get("published_at"),
                prerelease=rel.get("prerelease", False),
                url=rel.get("html_url"),
            )
            for rel in raw
        ]
        s.releases = entries
        dates = sorted(filter(None, [
            _parse_dt(e.published_at) for e in entries if not e.prerelease
        ]))
        if len(dates) >= 2:
            gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
            s.avg_days_between_releases = sum(gaps) / len(gaps)

    # ── Security advisories ───────────────────────────────────────────────────

    async def _apply_security_advisories(self, s: RepoStats, owner: str, name: str) -> None:
        raw = await self._rest(
            f"/repos/{owner}/{name}/security-advisories",
            params={"per_page": 100},
        )
        if not isinstance(raw, list):
            return
        advisories = []
        for adv in raw:
            cvss = adv.get("cvss") or {}
            advisories.append(SecurityAdvisory(
                ghsa_id=adv.get("ghsa_id", ""),
                cve_id=adv.get("cve_id"),
                severity=adv.get("severity", "unknown"),
                summary=adv.get("summary", ""),
                published_at=adv.get("published_at", ""),
                cvss_score=cvss.get("score"),
                url=adv.get("html_url"),
            ))
        s.security_advisories = advisories
        s.security_advisories_count = len(advisories)

    # ── Issue & PR time-window trends ─────────────────────────────────────────

    async def _apply_issue_trends(self, s: RepoStats, owner: str, name: str) -> None:
        now = datetime.now(timezone.utc)
        since_30d = (now - timedelta(days=30)).isoformat()
        since_90d = (now - timedelta(days=90)).isoformat()

        # 1. New issues opened in last 30d / 90d
        #    Fetch 100 most-recently-created open issues, filter client-side.
        recent_open = await self._rest(
            f"/repos/{owner}/{name}/issues",
            params={"state": "open", "sort": "created", "direction": "desc",
                    "per_page": 100},
        )
        if isinstance(recent_open, list):
            cutoff_30 = now - timedelta(days=30)
            cutoff_90 = now - timedelta(days=90)
            for item in recent_open:
                if item.get("pull_request"):   # skip PRs
                    continue
                created = _parse_dt(item.get("created_at"))
                if created and created >= cutoff_90:
                    s.issues_new_90d += 1
                    if created >= cutoff_30:
                        s.issues_new_30d += 1

        # 2. Active open issues (updated in last 30d)
        active_open = await self._rest(
            f"/repos/{owner}/{name}/issues",
            params={"state": "open", "since": since_30d, "per_page": 100},
        )
        if isinstance(active_open, list):
            s.issues_active_30d = sum(
                1 for i in active_open if not i.get("pull_request")
            )

        # 3. Issues closed in last 30d
        closed_recent = await self._rest(
            f"/repos/{owner}/{name}/issues",
            params={"state": "closed", "since": since_30d,
                    "sort": "updated", "direction": "desc", "per_page": 100},
        )
        if isinstance(closed_recent, list):
            cutoff_30 = now - timedelta(days=30)
            for item in closed_recent:
                if item.get("pull_request"):
                    continue
                closed_at = _parse_dt(item.get("closed_at"))
                if closed_at and closed_at >= cutoff_30:
                    s.issues_closed_30d += 1

        # 4. Active open PRs (updated in last 30d)
        active_prs = await self._rest(
            f"/repos/{owner}/{name}/pulls",
            params={"state": "open", "sort": "updated", "direction": "desc",
                    "per_page": 100},
        )
        if isinstance(active_prs, list):
            cutoff_30 = now - timedelta(days=30)
            s.prs_active_30d = sum(
                1 for pr in active_prs
                if _parse_dt(pr.get("updated_at")) and
                   _parse_dt(pr.get("updated_at")) >= cutoff_30
            )
