# OWASP State of AI — GitHub Surveyor

A FastAPI web application for surveying GitHub repositories as part of the [OWASP State of Agentic AI Security and Governance 2026](https://genai.owasp.org) research initiative.

Fetches live stats for a curated list of AI agent repos — stars, forks, contributors, commit activity, languages, issues, PRs, releases, and security advisories — and presents them in an interactive dark/light UI.

---

## Features

- **Bulk GitHub data fetch** via GraphQL + REST (contributors, commit history, releases, security advisories)
- **Interactive table** with search, sort, and activity-status filtering
- **Repo detail drawer** with commit trend chart, language breakdown, issue/PR charts, top contributors, and security advisories
- **Overview charts** — stars ranking, license distribution, activity status, language popularity
- **CSV export** of all cached data
- **SSE progress stream** for live fetch progress
- **Dark / light mode** toggle
- **Admin page** (`/admin`) — password-protected editor for `repos.txt` with duplicate detection, per-entry remove, backup history, and auto-refresh on save
- **File-based JSON cache** with configurable TTL

---

## Quick Start

```bash
# 1. Clone
git clone git@github.com:saikishu/stateofai-surveyor.git
cd stateofai-surveyor

# 2. Configure
cp .env.example .env
# Edit .env — set GITHUB_TOKEN and ADMIN_PASSWORD

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
| `GITHUB_TOKEN` | *(required)* | GitHub personal access token — needs `public_repo` scope |
| `CSV_PATH` | `repos.txt` | Path to the repo list file |
| `PORT` | `8000` | Server port |
| `ADMIN_PASSWORD` | *(required)* | Password for the `/admin` page |

Create a token at [github.com/settings/tokens](https://github.com/settings/tokens). Read-only `public_repo` scope is sufficient.

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
    ├── models.py              # Pydantic models
    └── templates/
        ├── index.html         # Main UI (Alpine.js + Chart.js)
        └── admin.html         # Admin repo editor
```

---

## API Endpoints

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

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).
