from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


class ContributorInfo(BaseModel):
    login: str
    avatar_url: str
    html_url: str
    contributions: int          # commit count from /contributors endpoint
    additions: int = 0          # total lines added (from stats/contributors)
    deletions: int = 0          # total lines deleted
    commits_count: int = 0      # commits (from stats/contributors — may differ)


class ReleaseEntry(BaseModel):
    tag_name: str
    name: Optional[str] = None
    published_at: Optional[str] = None
    prerelease: bool = False
    url: Optional[str] = None


class WeeklyActivity(BaseModel):
    week: int        # Unix timestamp (start of week)
    total: int       # total commits that week
    days: List[int] = Field(default_factory=list)  # commits per day (Sun-Sat)


class SecurityAdvisory(BaseModel):
    ghsa_id: str
    cve_id: Optional[str] = None
    severity: str               # critical | high | medium | low
    summary: str
    published_at: str
    cvss_score: Optional[float] = None
    url: Optional[str] = None


class RepoStats(BaseModel):
    """All collected statistics for a GitHub repository."""

    # ── Identity ─────────────────────────────────────────────────────
    full_name: str
    name: str
    owner: str
    description: Optional[str] = None
    homepage: Optional[str] = None
    github_url: str

    # ── Core metrics ─────────────────────────────────────────────────
    stars: int = 0
    forks: int = 0
    watchers: int = 0

    # ── License & metadata ───────────────────────────────────────────
    license_name: Optional[str] = None
    license_spdx: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    is_archived: bool = False
    is_fork: bool = False
    default_branch: str = "main"

    # ── Dates & lifecycle ────────────────────────────────────────────
    created_at: str = ""
    updated_at: str = ""
    pushed_at: str = ""
    age_days: int = 0
    days_since_push: int = 0
    activity_status: str = "unknown"   # active | recent | stale | dormant | archived

    # ── Languages (top-5 by bytes) ───────────────────────────────────
    languages: Dict[str, int] = Field(default_factory=dict)   # name → bytes
    top_3_languages: List[str] = Field(default_factory=list)
    total_code_bytes: int = 0
    approx_loc: int = 0

    # ── Issues ───────────────────────────────────────────────────────
    issues_open: int = 0
    issues_closed: int = 0

    # ── Issue trends (time-windowed) ─────────────────────────────────
    issues_new_30d: int = 0        # issues opened in last 30 days
    issues_new_90d: int = 0        # issues opened in last 90 days
    issues_active_30d: int = 0     # open issues updated in last 30 days
    issues_closed_30d: int = 0     # issues closed in last 30 days

    # ── Pull Requests ─────────────────────────────────────────────────
    prs_open: int = 0
    prs_closed: int = 0
    prs_merged: int = 0
    prs_active_30d: int = 0        # open PRs updated in last 30 days

    # ── Releases ─────────────────────────────────────────────────────
    release_count: int = 0
    latest_release_tag: Optional[str] = None
    latest_release_date: Optional[str] = None
    avg_days_between_releases: Optional[float] = None
    releases: List[ReleaseEntry] = Field(default_factory=list)

    # ── Contributors ─────────────────────────────────────────────────
    contributor_count: int = 0
    top_contributors: List[ContributorInfo] = Field(default_factory=list)

    # ── Commit activity ──────────────────────────────────────────────
    commits_last_year: int = 0
    commits_per_week_avg: float = 0.0
    weekly_commits: List[int] = Field(default_factory=list)      # 52 weeks, oldest→newest
    weekly_timestamps: List[int] = Field(default_factory=list)

    # ── Commit trends (computed, no extra API call) ───────────────────
    commits_30d: int = 0
    commits_90d: int = 0
    commit_trend: str = "unknown"      # surging | rising | flat | declining | stalled
    commit_growth_pct: float = 0.0     # % change: last 4 weeks vs prior 4 weeks

    # ── Commit span ──────────────────────────────────────────────────
    total_commits: int = 0             # lifetime total (from GraphQL history)
    first_commit_date: Optional[str] = None
    commit_span_days: int = 0          # days from first commit to today

    # ── Security ─────────────────────────────────────────────────────
    vulnerability_alert_count: int = 0       # dependabot (requires special scope)
    security_advisories_count: int = 0       # published GHSA advisories
    security_advisories: List[SecurityAdvisory] = Field(default_factory=list)

    # ── Original CSV row (all columns preserved) ─────────────────────
    csv_row: Optional[Dict[str, Any]] = None

    # ── Fetch metadata ───────────────────────────────────────────────
    fetched_at: Optional[str] = None
    fetch_error: Optional[str] = None
    fetch_status: str = "pending"   # pending | fetching | complete | error


class FetchProgress(BaseModel):
    total: int
    completed: int
    current: Optional[str] = None
    errors: List[str] = Field(default_factory=list)
    done: bool = False
