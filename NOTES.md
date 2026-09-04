# Notes

## 1. How to run it locally

```bash
cp .env.example .env    # paste a GitHub personal access token into GITHUB_TOKEN=
make setup               # creates backend/frontend venvs, installs pinned deps
make run-backend          # http://localhost:8000, interactive docs at /docs
make run-frontend          # optional, http://localhost:8501
```

Requires Python 3.9+, a GitHub PAT (read-only/public scope), and the `claude` CLI
authenticated locally (used by the narrative endpoint via subprocess — no separate LLM API
key). `make test` runs the 55 offline tests; `make test-integration` runs 2 tests against live
GitHub (needs `GITHUB_TOKEN`). See `README.md` for endpoint details and example `curl` calls.

## 2. Architecture tour

See the diagram in `README.md` for the full request path. In short:

**Request flow.** `main.py` (FastAPI) validates the request, then:
- `ingest.py` checks SQLite for a fresh `(repo, since, until)` cache hit; on a miss, it pulls
  commits/PRs/reviews from GitHub via `github_client.py` (a thin `httpx` wrapper: Link-header
  pagination, typed 404/403/5xx errors, retry-with-backoff on transient failures) and upserts
  them into SQLite.
- `metrics.py` computes rankings and collaboration edges via plain SQL aggregation over the
  rows already present in SQLite — re-scoped by the requested window at query time, since the
  same tables can hold rows from other previously-ingested windows for the same repo.
- `narrative.py` takes those same computed numbers and shells out to the `claude` CLI.

**LLM auth: subscription, not API key.** `narrative.py` calls
`claude -p "<prompt>" --output-format json` as a subprocess, authenticated by the local
Claude Code session already logged in — no `ANTHROPIC_API_KEY` needed, per the assignment's
explicit allowance for using AI coding tools this way. To run this somewhere with no
interactive CLI session available (e.g. a CI job), the swap is small: replace the
`subprocess.run([...])` call in `narrative.py` with a direct call to
`anthropic.Anthropic(api_key=...).messages.create(...)` via the `anthropic` Python SDK — the
prompt-building (`_build_prompt`) and response-parsing (`_strip_markdown_fences`,
`NarrativeResult`) logic wouldn't need to change, only the transport.

**Frontend.** `frontend/streamlit_app.py` is a fully separate HTTP client of the backend (not a
direct import): a form for repo/date range, a contributors table, a bar chart, an interactive
PR-reviewer collaboration graph (pyvis/vis.js), and the narrative panel.

**Scope trade-offs.** The assignment's optional list (a second integration, more insight
signals, a background sync worker, an eval harness, Docker) was mostly declined in favor of
getting the required pieces genuinely solid — real end-to-end verification against live GitHub
and a running server at every step, not just unit tests against mocks. The two things kept
despite being "optional" — SQLite caching and a real frontend — were cheap relative to their
value for the Performance and Operability grading criteria. The PR reviewer collaboration graph
(`GET /insights/collaboration`) was added after initial scope was locked, once it became clear
a real author↔reviewer signal (not a fabricated one) was worth the extra ingestion work.

**Why not Docker.** Not a cost issue — the Docker engine/CLI is free and open-source. Assuming
a reviewer already has Docker Desktop installed and running, or asking them to install it (or a
lighter alternative like Podman/Colima) just to read numbers off an HTTP endpoint, is real
setup friction with no corresponding benefit here — there's no multi-service orchestration need
this app actually has. A `Makefile` + pinned `requirements.txt` per component gives the same
"one-command local run" outcome (`make setup && make run-backend`) without that tax.

## 3. What I'd do next with another day

- **Bound ingestion latency for large/active repos.** A repo like `pandas-dev/pandas` over a
  couple of weeks means dozens of sequential per-PR detail + reviews calls on the first
  (uncached) request — tens of seconds. I'd move to a background sync worker (mentioned as
  optional in the assignment) so ingestion doesn't block the request, or at minimum fetch PR
  details/reviews concurrently instead of sequentially.
- **Fix the known PR-pagination edge case**: `ingest.py` stops paginating PRs once a PR's
  `created_at` falls before `since`, since the list endpoint is sorted by creation date
  descending — a long-lived PR opened before the window but merged inside it would be missed.
  Correct fix: also query by a `sort=updated` pass, or accept the extra API cost of not
  early-stopping for repos where this matters.
- **An eval harness for the narrative prompt** — a small fixed set of (numbers → expected
  narrative properties) cases to run before changing the prompt or swapping models, per the
  assignment's suggestion. Skipped for time; the manual verification in this session (checking
  real narratives against real numbers) stood in for it during development.
- **Retry/backoff for the `claude` CLI call**, matching what `github_client.py` already does
  for GitHub — right now a transient CLI hiccup surfaces as a `502` rather than being retried.
- **Second integration** (Jira/Linear) behind the same `ingest`/`metrics` shape, now that the
  pattern is established — would mostly be a new `*_client.py` + schema tables.

## 4. AI usage

Built with Claude Code end-to-end, including this conversation itself. Concretely:

- All code (backend, frontend, tests) was written by Claude Code, but reviewed and directed
  interactively, not accepted blind — several real bugs were caught and fixed before being
  committed, not after: a `github_client.py` validation bug (using `yield from` made
  `list_commits` etc. generator functions, so `InvalidRepoError` silently never raised until
  the caller started iterating — a regression test now guards this), a caching-correctness bug
  in `ingest.py` (freshness was originally keyed by repo alone, so querying the same repo for a
  different date range would wrongly reuse data from an unrelated window), and a pyvis physics
  bug in the frontend (a full options replacement was silently dropping the `barnes_hut()`
  physics config, leaving the graph in constant motion and making it genuinely hard to hover in
  real use).
- The `GET /insights/narrative` endpoint's own LLM call is Claude Code itself: it shells out to
  `claude -p "<prompt>" --output-format json`, using the local CLI session's existing auth
  rather than a separately billed API key, per the assignment's explicit allowance for this.
- Every claim in this codebase that could be checked, was: endpoints were hit with real `curl`
  calls against a running server (not just unit tests), the Streamlit UI was driven in a real
  headless browser via Playwright with screenshots inspected, and the two integration tests hit
  live GitHub rather than only mocks.
