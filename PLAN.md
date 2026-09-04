# Tempo Take-Home Loop1: GitHub Insights Service — Implementation Plan

## Context

This is a from-scratch build for the Loop take-home assignment (`AI_ML — Home Assignmen_Tempo.pdf`). The assignment requires: an integration with an external API, an HTTP endpoint returning a computed insight, a second HTTP endpoint where an LLM synthesizes a narrative over those numbers (with root-cause hypothesis, confidence score, evidence chain), local runnability, and production-grade HTTP/security/performance practices.

## Decisions

- **Stack**: Python — FastAPI backend + Streamlit frontend (single language, both explicitly allowed by the assignment).
- **Integration**: GitHub. A personal access token with read-only scope is used against public repos, which give rich commit/PR/review/issue data covering all three example insight categories (top contributors, top reviewers, top closers) from a single integration.
- **Storage**: SQLite — persisted, cached ingestion so repeat queries don't re-hit the GitHub API.
- **LLM for narrative endpoint**: shells out to the `claude` CLI in headless mode (`claude -p "<prompt>" --output-format json`) as a subprocess, using existing local CLI authentication rather than a separately billed API key. Explicitly permitted per the assignment's "On using AI coding assistants" section.
- **Repo scope**: endpoints accept any arbitrary public `owner/repo` as a query param — not fixed to one repo.
- **Demo repo for README examples**: `pandas-dev/pandas` — active, real PR/review/issue history so insights and narrative are genuinely interesting, moderate size, easy to sanity-check against the live GitHub UI. Endpoints themselves stay fully generic.
- **Git workflow**: trunk-based development with short-lived feature branches (not GitFlow's persistent `dev` branch).

## Scope (MVP, hard requirements from assignment)

1. GitHub ingestion for a given `owner/repo` + date range: commits, PRs (with merge info), reviews, issues.
2. `GET /insights/contributors` — top contributors by commits on default branch, PRs merged, lines changed, over a given period. Numbers-only, documented metric definition.
3. `GET /insights/narrative` — takes the same computed numbers, calls the `claude` CLI to produce: a 2-4 sentence narrative, root-cause hypothesis (when supported), confidence score (0-1), and an evidence chain (list of the specific numbers/facts backing the claim).
4. SQLite-backed caching of ingested GitHub data (TTL-based re-fetch).
5. Streamlit page: repo/date-range input, contributors table, narrative panel.
6. README (60-second quickstart) + NOTES.md (run instructions, architecture tour, "what's next", AI usage note).
7. Security: token never logged or committed; `.env` gitignored, `.env.example` provided; input validated on `owner/repo`/date params; GitHub API calls and the `claude` subprocess call use argument lists, never `shell=True` or raw string interpolation.
8. Error handling: malformed `owner/repo` → `400` before any GitHub call; GitHub 404 (private/nonexistent repo) → `404` with a clear message, no stack trace leaked; GitHub rate-limit hit → `429`/`503` with a clear message.
9. `Makefile` (`make setup`, `make run`, `make test`) as the one-command local-run story, in place of Docker.

## Explicitly out of scope

The assignment's "Optional — but nice to have" section lists several bonuses (frontend, more insight signals, caching, a second integration, background sync, tests, an eval harness, containerization). All are optional, not required. Decisions:

- **Skipped**: a second integration, additional insight signals beyond contributors, a background sync worker, an eval harness, Docker/containerization. Reasoning recorded in `NOTES.md`: each adds real build time or (for Docker) local tooling/licensing friction, without being necessary to satisfy any hard requirement.
- **Kept, despite being optional**: SQLite caching (already core to satisfying the Performance grading criterion), a Streamlit frontend (cheap relative to the demo value), and lightweight tests on the metrics/narrative logic (cheap, and "what you chose to test and why" is graded directly).

## Architecture

```
backend/
  app/
    main.py              # FastAPI app, routes
    config.py             # env var loading (GITHUB_TOKEN, DB path, cache TTL)
    db.py                 # SQLite connection + schema init
    github_client.py      # thin wrapper over GitHub REST API (httpx), pagination, rate-limit handling
    ingest.py              # fetch commits/PRs/reviews/issues for owner/repo + date range, upsert into SQLite
    metrics.py             # pure functions: compute contributor rankings from stored rows
    narrative.py           # builds prompt from metrics, invokes `claude -p` subprocess, parses JSON response
    schemas.py              # pydantic request/response models
  tests/
    test_metrics.py         # unit tests on metric computation (no network)
    test_narrative_prompt.py # test prompt construction / response parsing with a fake CLI output
  requirements.txt
frontend/
  streamlit_app.py         # calls backend HTTP API, renders tables + narrative
.env.example
Makefile                   # make setup / make run / make test
README.md
NOTES.md
```

### Data flow

1. Client calls `GET /insights/contributors?repo=owner/name&since=...&until=...`.
2. Backend checks SQLite for cached ingestion of that repo covering the range (keyed by repo + fetched-at timestamp); if stale/missing, `ingest.py` pulls from GitHub REST API (`/commits`, `/pulls`, `/pulls/{n}/reviews`, `/issues`) and upserts rows.
3. `metrics.py` computes rankings purely from SQLite rows (aggregate via SQL `GROUP BY` where possible, no quadratic loops).
4. `GET /insights/narrative` calls the same ingestion/metrics path, then `narrative.py` builds a compact JSON-numbers prompt and invokes `claude -p` synchronously (subprocess with timeout), parses the model's JSON output into the response schema. If the CLI call fails/times out, returns a `502` with a clear error rather than hanging.

### Key endpoints (initial contract)

- `GET /insights/contributors?repo={owner}/{name}&since=YYYY-MM-DD&until=YYYY-MM-DD` → `{repo, period, contributors: [{login, commits, prs_merged, lines_changed}]}`
- `GET /insights/narrative?repo=...&since=...&until=...` → `{repo, period, narrative, root_cause_hypothesis, confidence, evidence: [...]}`
- `GET /health` → liveness check

### Metric definition (documented in README)

"Top contributors" = ranked by (commits to default branch in range, PRs merged in range, net lines changed in merged PRs), computed straight from GitHub's own commit/PR data — no derived heuristics, so the numbers are directly auditable against `github.com/{repo}/commits` and `/pulls`.

## Testing

- `metrics.py`: unit tests against hand-built SQLite fixtures (no network calls) — verify ranking math, edge cases (empty range, single contributor, tie-breaking).
- `narrative.py`: test prompt construction and JSON-response parsing with a stubbed subprocess (monkeypatch `subprocess.run`), including malformed-JSON-from-CLI handling.
- Manual verification: run backend, `curl` both endpoints against a real public repo, cross-check contributor numbers against the GitHub UI.

## Verification

1. `uvicorn app.main:app --reload` runs cleanly, `GET /health` returns 200.
2. `curl 'localhost:8000/insights/contributors?repo=pandas-dev/pandas&since=2024-01-01&until=2024-06-30'` returns sane, auditable numbers.
3. `curl '.../insights/narrative?...'` returns a narrative with confidence + evidence chain, and doesn't hang past a defined timeout.
4. `pytest` passes for the two test files.
5. `streamlit run frontend/streamlit_app.py` renders the table + narrative for a sample repo.
6. `git log` on `main` shows small, reviewable PRs (scaffold → github client → ingest+db → metrics endpoint → narrative endpoint → streamlit → README/tests) rather than one giant commit.

## Feature branch sequence (each PR reviewed and merged before starting the next)

1. `planning-stage` — `PLAN.md` (this file) → PR → merge to `main`
2. `feature/backend-scaffold` — `requirements.txt`, `.env.example`, `config.py`, `db.py` (schema)
3. `feature/github-client` — `github_client.py`, GitHub REST wrapper
4. `feature/ingestion` — `ingest.py`, fetch + upsert into SQLite
5. `feature/insights-api` — `metrics.py` + `schemas.py` + `main.py`, `/insights/contributors` endpoint
6. `feature/narrative-api` — `narrative.py`, `/insights/narrative` endpoint
7. `feature/frontend` — `frontend/streamlit_app.py`
8. `feature/tests` — test suite
9. `feature/docs` — `README.md` + `NOTES.md`
