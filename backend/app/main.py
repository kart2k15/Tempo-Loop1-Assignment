from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from typing import Tuple

from fastapi import FastAPI, HTTPException, Query

from app.db.connection import init_db
from app.github_client import InvalidRepoError, RateLimitError, RepoNotFoundError
from app.ingest import ingest_repo
from app.metrics import compute_collaboration_edges, compute_contributors
from app.narrative import NarrativeGenerationError, generate_narrative
from app.schemas import (
    CollaborationEdgeOut,
    CollaborationResponse,
    ContributorOut,
    ContributorsResponse,
    NarrativeResponse,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="GitHub Insights Service", lifespan=lifespan)


def _to_github_timestamp(d: date) -> str:
    return f"{d.isoformat()}T00:00:00Z"


def _validate_and_ingest(repo: str, since: date, until: date) -> Tuple[str, str]:
    """Shared by both /insights endpoints: validates the date range, ingests the window,
    and maps GitHubClient's typed errors to HTTP status codes. Returns the GitHub-format
    (since, until) timestamps for the caller to pass into metrics/narrative."""
    if until <= since:
        raise HTTPException(status_code=400, detail="`until` must be after `since`")

    since_ts, until_ts = _to_github_timestamp(since), _to_github_timestamp(until)

    try:
        ingest_repo(repo, since=since_ts, until=until_ts)
    except InvalidRepoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RepoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    return since_ts, until_ts


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/insights/contributors", response_model=ContributorsResponse)
def get_contributors(
    repo: str = Query(..., description="owner/repo, e.g. pandas-dev/pandas"),
    since: date = Query(..., description="inclusive start date (UTC)"),
    until: date = Query(..., description="exclusive end date (UTC)"),
) -> ContributorsResponse:
    since_ts, until_ts = _validate_and_ingest(repo, since, until)

    stats = compute_contributors(repo, since=since_ts, until=until_ts)
    return ContributorsResponse(
        repo=repo,
        since=since,
        until=until,
        contributors=[
            ContributorOut(login=s.login, commits=s.commits, prs_merged=s.prs_merged, lines_changed=s.lines_changed)
            for s in stats
        ],
    )


@app.get("/insights/narrative", response_model=NarrativeResponse)
def get_narrative(
    repo: str = Query(..., description="owner/repo, e.g. pandas-dev/pandas"),
    since: date = Query(..., description="inclusive start date (UTC)"),
    until: date = Query(..., description="exclusive end date (UTC)"),
) -> NarrativeResponse:
    since_ts, until_ts = _validate_and_ingest(repo, since, until)

    stats = compute_contributors(repo, since=since_ts, until=until_ts)
    try:
        result = generate_narrative(repo, since=since.isoformat(), until=until.isoformat(), stats=stats)
    except NarrativeGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return NarrativeResponse(
        repo=repo,
        since=since,
        until=until,
        narrative=result.narrative,
        root_cause_hypothesis=result.root_cause_hypothesis,
        confidence=result.confidence,
        evidence=result.evidence,
    )


@app.get("/insights/collaboration", response_model=CollaborationResponse)
def get_collaboration(
    repo: str = Query(..., description="owner/repo, e.g. pandas-dev/pandas"),
    since: date = Query(..., description="inclusive start date (UTC)"),
    until: date = Query(..., description="exclusive end date (UTC)"),
) -> CollaborationResponse:
    """Author <-> reviewer edges for PRs merged in this period - who reviewed whose PRs."""
    since_ts, until_ts = _validate_and_ingest(repo, since, until)

    edges = compute_collaboration_edges(repo, since=since_ts, until=until_ts)
    return CollaborationResponse(
        repo=repo,
        since=since,
        until=until,
        edges=[CollaborationEdgeOut(author=e.author, reviewer=e.reviewer, reviews=e.reviews) for e in edges],
    )
