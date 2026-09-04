# Tempo Take-Home Loop1: GitHub Insights Service — Implementation Plan

## Context

This is a from-scratch build for the Loop take-home assignment (`AI_ML — Home Assignmen_Tempo.pdf`). The assignment requires: an integration with an external API, an HTTP endpoint returning a computed insight, a second HTTP endpoint where an LLM synthesizes a narrative over those numbers (with root-cause hypothesis, confidence score, evidence chain), local runnability, and production-grade HTTP/security/performance practices.

This document reflects the decisions as actually built. See `README.md` for the quickstart, architecture diagram, and endpoint reference, and `NOTES.md` for the full trade-off discussion and AI usage notes.

## Decisions

- **Stack**: Python — FastAPI backend + Streamlit frontend (single language, both explicitly allowed by the assignment).
- **Integration**: GitHub. A personal access token with read-only scope is used against public repos, giving commit, PR, and review data for contributor rankings, an LLM narrative, and a PR reviewer collaboration signal — all from a single integration.
- **Storage**: SQLite — persisted, cached ingestion so repeat queries don't re-hit the GitHub API.
- **LLM for narrative endpoint**: shells out to the `claude` CLI in headless mode (`claude -p "<prompt>" --output-format json`) as a subprocess, using existing local CLI authentication rather than a separately billed API key. Explicitly permitted per the assignment's "On using AI coding assistants" section.
- **Repo scope**: endpoints accept any arbitrary public `owner/repo` as a query param — not fixed to one repo.
- **Demo repo for README examples**: `pandas-dev/pandas` — active, real PR/review history so insights and narrative are genuinely interesting, easy to sanity-check against the live GitHub UI. Endpoints themselves stay fully generic.
- **Git workflow**: trunk-based development with short-lived feature branches (not GitFlow's persistent `dev` branch).

## Scope (hard requirements from the assignment, plus one added signal)

1. GitHub ingestion for a given `owner/repo` + date range: commits, PRs (with merge info), and reviews on merged PRs.
2. `GET /insights/contributors` — top contributors by commits, PRs merged, lines changed, over a given period. Numbers-only, documented metric definition.
3. `GET /insights/narrative` — takes the same computed numbers, calls the `claude` CLI to produce: a 2-4 sentence narrative, root-cause hypothesis (when supported), confidence score (0-1), and an evidence chain (list of the specific numbers/facts backing the claim).
4. `GET /insights/collaboration` — author↔reviewer edges: who reviewed whose merged PRs, and how many times. Added beyond the hard requirements as a real, non-fabricated signal (see `NOTES.md`).
5. SQLite-backed caching of ingested GitHub data, keyed by `(repo, since, until)` and re-fetched once stale.
6. Streamlit page: repo/date-range input, contributors table, bar chart, collaboration graph, narrative panel.
7. README (60-second quickstart, architecture diagram) + NOTES.md (run instructions, architecture tour, "what's next", AI usage note).
8. Security: token never logged or committed; `.env` gitignored, `.env.example` provided; input validated on `owner/repo`/date params; GitHub API calls and the `claude` subprocess call use argument lists, never `shell=True` or raw string interpolation.
9. Error handling: malformed `owner/repo` or bad date range → `400` before any GitHub call; GitHub 404 (private/nonexistent repo) → `404` with a clear message, no stack trace leaked; GitHub rate-limit hit → `429`; narrative generation failure → `502`.
10. `Makefile` (`setup`, `run-backend`, `run-frontend`, `test`, `test-integration`, `clean`) as the one-command local-run story, in place of Docker.

## Explicitly out of scope

The assignment's "Optional — but nice to have" section lists several bonuses (a second integration, more insight signals, a background sync worker, an eval harness, containerization). Decisions:

- **Skipped**: a second integration, a background sync worker, an eval harness, Docker/containerization. Reasoning recorded in `NOTES.md`: each adds real build time, or (for Docker) unnecessary setup friction, without being necessary to satisfy any hard requirement.
- **Kept, despite being optional**: SQLite caching (core to satisfying the Performance grading criterion), a Streamlit frontend (cheap relative to the demo value), and a full test suite across the backend (cheap, and "what you chose to test and why" is graded directly).
- **Added beyond the hard requirements**: the PR reviewer collaboration endpoint (`GET /insights/collaboration`) — a real, ingested author↔reviewer signal rather than a fabricated one, at a modest additional ingestion cost (see `NOTES.md`).
- **Not ingested**: GitHub issues. Only commits, PRs, and reviews on merged PRs are pulled — nothing in the final scope reads issue data, so ingesting it would be dead code.

## Architecture

```
backend/
  app/
    main.py                    # FastAPI app, routes: /health, /insights/contributors,
                                # /insights/narrative, /insights/collaboration
    config.py                  # env var loading (GITHUB_TOKEN, DB path, cache TTL), repo-root-anchored
    db/
      connection.py            # SQLite connection + schema init
      schema.sql                # DDL: ingestions, commits, pull_requests, reviews
    github_client.py            # httpx wrapper over the GitHub REST API: pagination, retry/backoff,
                                # typed errors (InvalidRepoError, RepoNotFoundError, RateLimitError)
    ingest.py                    # fetch commits/PRs/reviews for owner/repo + date range, upsert into SQLite
    metrics.py                   # SQL aggregation: contributor rankings + collaboration edges
    narrative.py                 # builds prompt from metrics, invokes `claude -p` subprocess, parses JSON
    schemas.py                   # pydantic response models
  tests/
    conftest.py                  # shared test_db fixture (temp SQLite per test)
    test_github_client.py         # pagination, error mapping, retry/backoff (mocked transport)
    test_ingest.py                 # caching-by-window, early-stop PR pagination (mocked GitHub client)
    test_ingest_integration.py      # live GitHub, real SQLite - no mocks
    test_metrics.py                  # SQL aggregation correctness (seeded fixtures)
    test_main.py                      # endpoint wiring, error-status mapping (ingest_repo mocked)
    test_narrative.py                  # CLI response parsing, error paths (mocked subprocess)
  requirements.txt
  pytest.ini
frontend/
  streamlit_app.py              # calls backend HTTP API, renders table, bar chart, collaboration
                                 # graph (pyvis), and narrative panel
  requirements.txt
.env.example
Makefile
README.md
NOTES.md
```

### Data flow

1. Client calls `GET /insights/contributors?repo=owner/name&since=...&until=...`.
2. Backend checks SQLite for a fresh `(repo, since, until)` ingestion; if stale or missing, `ingest.py` pulls from the GitHub REST API (`/commits`, `/pulls`, per-PR detail, `/pulls/{n}/reviews`) and upserts rows.
3. `metrics.py` computes rankings and collaboration edges purely from SQLite rows via SQL `GROUP BY`/`JOIN`, re-scoped to the requested window at query time (the same tables can hold rows from other previously-ingested windows for the same repo).
4. `GET /insights/narrative` and `GET /insights/collaboration` reuse the same ingestion/metrics path. The narrative endpoint's `narrative.py` builds a compact JSON-numbers prompt and invokes `claude -p` synchronously (subprocess with timeout), parsing the model's JSON output into the response schema. A CLI failure/timeout returns `502` rather than hanging.

### Endpoints (as built)

- `GET /insights/contributors?repo={owner}/{name}&since=YYYY-MM-DD&until=YYYY-MM-DD` → `{repo, since, until, contributors: [{login, commits, prs_merged, lines_changed}]}`
- `GET /insights/narrative?repo=...&since=...&until=...` → `{repo, since, until, narrative, root_cause_hypothesis, confidence, evidence: [...]}`
- `GET /insights/collaboration?repo=...&since=...&until=...` → `{repo, since, until, edges: [{author, reviewer, reviews}]}`
- `GET /health` → liveness check

### Metric definition (documented in README)

"Top contributors" = ranked by `(commits, PRs merged, lines changed)`, computed straight from GitHub's own commit/PR data — no derived heuristics, so the numbers are directly auditable against `github.com/{repo}/commits` and `/pulls`. See README's "One nuance worth knowing" for the author-date-vs-committer-date caveat found during development.

## Testing

- `test_github_client.py`, `test_ingest.py`, `test_metrics.py`, `test_main.py`, `test_narrative.py`: 55 offline tests total, fixtures/mocks only, no network, run in ~0.3s.
- `test_ingest_integration.py`: 2 tests against live GitHub and a real SQLite file, no mocks.
- Manual verification at every step of the build: a running server hit with real `curl` calls, and the Streamlit UI driven in an actual headless browser (Playwright) with screenshots inspected.

## Verification

1. `make run-backend` runs cleanly, `GET /health` returns 200.
2. `curl "localhost:8000/insights/contributors?repo=pandas-dev/pandas&since=2026-08-25&until=2026-09-04"` returns sane, auditable numbers.
3. `curl ".../insights/narrative?..."` returns a narrative with confidence + evidence chain, and doesn't hang past the configured CLI timeout.
4. `curl ".../insights/collaboration?..."` returns real author↔reviewer edges.
5. `make test` passes all 55 offline tests; `make test-integration` passes both live-GitHub tests.
6. `make run-frontend` renders the table, bar chart, collaboration graph, and narrative for a sample repo.
7. `git log` on `main` shows small, reviewable PRs (scaffold → github client → ingestion → contributors endpoint → narrative endpoint → frontend → collaboration endpoint → docs) rather than one giant commit.

## Feature branch sequence (each PR reviewed and merged before starting the next)

1. `planning-stage` — `PLAN.md`
2. `feature/backend-scaffold` — `requirements.txt`, `.env.example`, `config.py`, `db/` (schema)
3. `feature/github-client` — `github_client.py`, GitHub REST wrapper
4. `feature/ingestion` — `ingest.py`, fetch + upsert into SQLite
5. `feature/insights-api` — `metrics.py` + `schemas.py` + `main.py`, `/insights/contributors` endpoint
6. `feature/narrative-api` — `narrative.py`, `/insights/narrative` endpoint
7. `feature/frontend` — `frontend/streamlit_app.py`
8. `feature/pr-collaboration` — reviews ingestion + `/insights/collaboration` endpoint
9. `feature/docs` — `Makefile`, `README.md`, `NOTES.md`
10. `feature/architecture-diagram`, `feature/docs-polish`, `feature/docs-formatting` — documentation follow-ups (diagram, content gaps, readability)
