# OWASP State of AI — GitHub Surveyor

A FastAPI web application for surveying GitHub repositories as part of the [OWASP State of Agentic AI Security and Governance 2026](https://genai.owasp.org) research initiative.

Fetches live stats for a curated list of AI agent repos — stars, forks, contributors, commit activity, languages, issues, PRs, releases, and security advisories — and presents them in an interactive dark/light UI.

---

## Features

- **Bulk GitHub data fetch** via GraphQL + REST (contributors, commit history, releases, security advisories)
- **Interactive table** with search, sort, and activity-status filtering
- **Repo detail drawer** with commit trend chart, language breakdown, issue/PR charts, top contributors, and security advisories
- **Overview charts** — stars ranking, license distribution, activity status, language popularity
- **CSV export** of all stored data
- **SSE progress stream** for live fetch progress
- **Dark / light mode** toggle
- **Admin page** (`/admin`) — password-protected editor for `repos.txt` with duplicate detection, per-entry remove, backup history, and auto-refresh on save
- **Git Sync** — admin panel detects pending `data/` changes and opens a GitHub PR to push them; tracks PR merge status automatically
- **Persistent git-tracked storage** — fetched stats stored as JSON under `data/repos/` and committed to the repository

---

## Quick Start

```bash
# 1. Clone
git clone git@github.com:saikishu/stateofai-surveyor.git
cd stateofai-surveyor

# 2. Configure
cp .env.example .env
# Edit .env — set GITHUB_TOKEN, ADMIN_PASSWORD, and GITHUB_REPO

# 3. Install
./setup.sh

# 4. Run
./run.sh
# → http://localhost:8000
```

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | *(required)* | GitHub fine-grained or classic personal access token (see below) |
| `REPOS_FILE` | `repos.txt` | Path to the repo list file |
| `PORT` | `8000` | Server port |
| `ADMIN_PASSWORD` | *(required)* | Password for the `/admin` page |
| `GITHUB_REPO` | *(required for Git Sync)* | Repo slug for PR creation, e.g. `owner/repo` |
| `GIT_USER_NAME` | `OWASP Surveyor Bot` | Commit author name used by Git Sync |
| `GIT_USER_EMAIL` | `surveyor@owasp.org` | Commit author email used by Git Sync |

### GitHub Token Setup

**Recommended — Fine-grained personal access token** (least privilege):

Create one at [github.com/settings/tokens?type=beta](https://github.com/settings/tokens?type=beta).

| Setting | Value |
|---|---|
| Resource owner | your user or org |
| Repository access | **Only select repositories** → pick this surveyor repo |
| **Contents** | Read and write *(push sync branches)* |
| **Pull requests** | Read and write *(open PRs)* |
| **Metadata** | Read-only *(auto-selected)* |

> Fetching data from the surveyed public repos requires no extra permissions — an authenticated token is sufficient for GitHub's GraphQL and REST APIs on public repositories.

**Alternative — Classic token** (`public_repo` + `repo` scopes): works but grants broader access than needed.

---

## Repo List Format (`repos.txt`)

One `owner/repo` per line. Lines starting with `#` are treated as comments and ignored.

```
# AI agent frameworks
openai/openai-agents-python
anthropics/claude-code

# Research
owner/repo-name
```

The list can be managed via the UI at `/admin`.

---

## Git Sync

Fetched repo stats are stored as JSON files under `data/repos/` and committed to this repository, making git the source of truth for all collected data.

When new repos are fetched, the `data/` directory accumulates uncommitted changes. The **Git Sync** panel in `/admin` handles pushing these back to GitHub:

1. **Status check** — on login the panel runs `git status data/` and lists any pending files.
2. **Sync to Git** — clicking the button creates a new branch (`data/sync-{timestamp}`), commits only the `data/` changes, pushes it, and opens a pull request automatically.
3. **PR tracking** — the panel polls the PR state. Once the PR is merged, the badge updates to "merged ✓" and clears on the next refresh.

The sync uses a git worktree so the running server's working tree is never touched during the operation.

```
Pending changes detected
       │
       ▼
  git worktree add /tmp/sync-…  (isolated copy)
       │
       ▼
  git add data/ && git commit
       │
       ▼
  git push → GitHub PR opened
       │
       ▼
  PR merged → badge clears → "Up to date"
```

> **Token scope**: `GITHUB_TOKEN` must have `repo` scope to push branches and create PRs.

---

## Project Structure

```
.
├── repos.txt                  # Input repo list
├── requirements.txt
├── setup.sh                   # Creates .venv and installs deps
├── run.sh                     # Starts uvicorn with --reload
├── data/
│   ├── repos/                 # Persistent repo data (owner/repo.json)
│   └── backups/               # repos.txt backups (timestamped)
└── surveyor/
    ├── main.py                # FastAPI routes
    ├── github_client.py       # GraphQL + REST GitHub client
    ├── storage.py             # Persistent file-based storage (data/)
    ├── git_sync.py            # Git worktree sync + GitHub PR creation
    ├── models.py              # Pydantic models
    └── templates/
        ├── index.html         # Main UI (Alpine.js + Chart.js)
        └── admin.html         # Admin repo editor
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Main UI |
| `GET` | `/admin` | Admin editor (password protected) |
| `GET` | `/api/repos` | All repos merged with stored stats |
| `POST` | `/api/repos/fetch-all` | Trigger bulk background fetch |
| `POST` | `/api/repos/{owner}/{repo}/fetch` | Fetch / refresh a single repo |
| `DELETE` | `/api/data/repos/{owner}/{repo}` | Remove a repo's stored data |
| `GET` | `/api/stream/progress` | SSE fetch progress stream |
| `GET` | `/api/rate-limit` | GitHub API rate limit status |
| `GET` | `/api/export/csv` | Export all stored data as CSV |
| `GET` | `/api/admin/git-status` | Pending `data/` changes + open PR state *(auth)* |
| `POST` | `/api/admin/git-sync` | Commit `data/`, push branch, open GitHub PR *(auth)* |

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).
