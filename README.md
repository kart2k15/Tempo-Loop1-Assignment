# Tempo Take-Home Loop1 — GitHub Insights Service

A small service that ingests GitHub activity for any public repo and surfaces contributor
insights, an LLM-synthesized narrative over those numbers, and a PR reviewer collaboration
graph. Built for the Loop take-home assignment — see [`PLAN.md`](PLAN.md) for the design
decisions and [`NOTES.md`](NOTES.md) for the architecture tour, trade-offs, and AI usage notes.

## Quickstart (60 seconds)

Prerequisites: Python 3.9+, a GitHub [personal access token](https://github.com/settings/tokens)
(read-only / public scope is enough), and the [`claude` CLI](https://claude.com/claude-code)
authenticated (used for the narrative endpoint — no separate API key needed).

```bash
cp .env.example .env        # then paste your GitHub token into GITHUB_TOKEN=
make setup                  # creates backend/frontend venvs, installs deps
make run-backend             # terminal 1 - serves http://localhost:8000
```

Then, in another terminal:

```bash
curl "http://localhost:8000/insights/contributors?repo=pandas-dev/pandas&since=2026-08-25&until=2026-09-04"
```

Optional UI (terminal 3):

```bash
make run-frontend            # serves http://localhost:8501
```

Full interactive API docs (curl/Postman-friendly): http://localhost:8000/docs

## Make targets

| Command | What it does |
|---|---|
| `make setup` | Creates `backend/.venv` and `frontend/.venv`, installs pinned deps into each, seeds `.env` from `.env.example` if one doesn't exist yet |
| `make run-backend` | Runs the FastAPI app with `uvicorn --reload` on http://localhost:8000 |
| `make run-frontend` | Runs the Streamlit UI on http://localhost:8501 |
| `make test` | Runs the 55 offline tests (fixtures/mocks, no network) |
| `make test-integration` | Runs the 2 tests that hit live GitHub (needs `GITHUB_TOKEN` in `.env`) |
| `make clean` | Removes both venvs, the pytest cache, and `data/insights.db` (does **not** touch `.env`) |

## Architecture

```mermaid
flowchart TB
    subgraph Client["Client"]
        Browser["Browser / curl / Postman"]
    end

    subgraph Frontend["Frontend - Streamlit"]
        ST["streamlit_app.py<br/>form, contributors table,<br/>bar chart, pyvis collaboration<br/>graph, narrative panel"]
    end

    subgraph Backend["Backend - FastAPI on Uvicorn"]
        API["main.py<br/>GET /health<br/>GET /insights/contributors<br/>GET /insights/narrative<br/>GET /insights/collaboration"]
        ING["ingest.py<br/>cache check + orchestration"]
        MET["metrics.py<br/>SQL aggregation:<br/>rankings + collaboration edges"]
        NAR["narrative.py<br/>prompt builder + response parser"]
        GHC["github_client.py<br/>httpx client, pagination,<br/>retry/backoff, typed errors"]
    end

    subgraph Cache["Cache / persistence"]
        DB[("SQLite<br/>ingestions (repo, since, until)<br/>commits, pull_requests, reviews")]
    end

    subgraph External["External services"]
        GHAPI[("GitHub REST API")]
        CLI["claude CLI (subprocess)<br/>--output-format json<br/>LLM narrative synthesis"]
    end

    Browser -- "GET /insights/*" --> ST
    Browser -. "GET /insights/* (direct)" .-> API
    ST -- "HTTP GET" --> API

    API --> ING
    API --> MET
    API --> NAR

    ING -- "check freshness" --> DB
    ING -- "cache miss: fetch" --> GHC
    GHC -- "REST calls" --> GHAPI
    GHC -- "commits / PRs / reviews" --> ING
    ING -- "upsert rows" --> DB

    MET -- "SELECT ... GROUP BY" --> DB

    NAR -- "prompt with computed numbers" --> CLI
    CLI -- "JSON: narrative, confidence,<br/>root_cause_hypothesis, evidence" --> NAR
```

Every request re-checks SQLite before touching GitHub: a fresh `(repo, since, until)` hit skips
`github_client.py` entirely, a miss fetches and upserts. `metrics.py` never talks to GitHub —
it only aggregates the rows already present in SQLite. The narrative endpoint is the only one that
also shells out to the `claude` CLI, after computing the same numbers `/insights/contributors`
would return.

**On the "LLM" in the diagram**: the narrative endpoint doesn't call a separately billed LLM
API. It runs `claude -p "<prompt>" --output-format json` as a subprocess, authenticated by
the local Claude Code session/subscription already logged in on the machine running
the backend — no `ANTHROPIC_API_KEY` needed. This means the narrative endpoint only works on a
machine with the `claude` CLI installed and authenticated (see Quickstart). If you'd rather use
the Anthropic API directly instead (e.g. to run this somewhere with no interactive CLI
session, like CI) — swap the `subprocess.run([...])` call in `backend/app/narrative.py` for a
direct call via the `anthropic` Python SDK (`Anthropic(api_key=...).messages.create(...)`); the
prompt-building and response-parsing logic around it doesn't need to change, only that one call.

## Endpoints

All three take the same query params: `repo` (`owner/repo`), `since`, `until` (`YYYY-MM-DD`,
`until` exclusive).

| Endpoint | Returns |
|---|---|
| `GET /insights/contributors` | Contributors ranked by commits, PRs merged, lines changed |
| `GET /insights/narrative` | LLM-synthesized narrative + root-cause hypothesis + confidence + evidence over the same numbers |
| `GET /insights/collaboration` | Author ↔ reviewer edges: who reviewed whose merged PRs, and how many times |
| `GET /health` | Liveness check |

Example:

```bash
curl "http://localhost:8000/insights/narrative?repo=pandas-dev/pandas&since=2026-08-25&until=2026-09-04"
```

Error responses: `400` malformed `repo`/date range, `404` repo not found or private, `429`
GitHub rate limit exhausted, `502` narrative generation failed (CLI timeout/error), `422`
malformed/missing query params (FastAPI's standard validation).

## What "top contributors" means

Ranked by `(commits, PRs merged, lines changed)` in the given period — computed straight from
GitHub's own commit/PR data (no derived heuristics), so it's directly auditable against
`github.com/{repo}/commits` and `/pulls`. "Lines changed" is additions + deletions on **merged
PRs only** (not raw commits — GitHub's commit list endpoint doesn't expose line counts without
an expensive per-commit call, so this was a deliberate scope trade-off; see `PLAN.md`).

**One nuance worth knowing**: the `since`/`until` filter on commits uses GitHub's own filter,
which matches on *committer* date, while the numbers shown use *author* date. These usually
match, but can diverge for rebased/backported commits — a commit authored weeks earlier can
land on the branch (and pass GitHub's committer-date filter) inside your query window while its
author date falls outside it, so it won't appear in the ranking. Both dates are "correct," they
just answer slightly different questions ("when was this written" vs "when did it land").

## Choosing a repo and date range

The endpoints work against **any public GitHub repo** — `nodejs/node`, `pandas-dev/pandas`,
`apache/spark`, or any other. The trade-off is ingestion time on a cache miss: a large, active repo
over a long window means dozens of sequential per-PR "detail" and "reviews" calls (GitHub's
list endpoints don't include line counts or reviews, so those need one extra call each, per
merged PR — see `PLAN.md`). A repo/window with a handful of merged PRs ingests in a couple of
seconds; something like `pandas-dev/pandas` over 30 days can take over a minute on the first
(uncached) request. Once ingested, repeat queries for that same window are instant (see
Caching below).

Rule of thumb: for a quick demo, keep the window to ~1-2 weeks on an active repo, or use a
wider window on a quieter one. Configure it either way:
- **Streamlit UI**: the "Repository" text field and the "Since"/"Until" date pickers — defaults
  to `pandas-dev/pandas`, last 14 days.
- **Direct API calls**: the `repo`, `since`, `until` query params on any endpoint, e.g.
  `?repo=nodejs/node&since=2026-08-01&until=2026-08-08`.

## Testing

```bash
make test              # 55 offline tests - fixtures/mocks, no network, runs in ~0.3s
make test-integration   # 2 tests against live GitHub - needs GITHUB_TOKEN, ~15-20s
```

See `NOTES.md` for what's tested and why.

## Caching

Ingested data is persisted in SQLite (`data/insights.db`, gitignored) and keyed by
`(repo, since, until)` — the exact window queried, not just the repo. A repeat query for the
same window is served entirely from the local DB; a different window (even for the same repo)
always re-fetches from GitHub, since previously-ingested data for a different range isn't a
valid answer for a new one.
