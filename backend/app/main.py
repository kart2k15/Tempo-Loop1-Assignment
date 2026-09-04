from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException, Query

from app.db.connection import init_db
from app.github_client import InvalidRepoError, RateLimitError, RepoNotFoundError
from app.ingest import ingest_repo
from app.metrics import compute_contributors
from app.schemas import ContributorOut, ContributorsResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="GitHub Insights Service", lifespan=lifespan)


def _to_github_timestamp(d: date) -> str:
    return f"{d.isoformat()}T00:00:00Z"


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/insights/contributors", response_model=ContributorsResponse)
def get_contributors(
    repo: str = Query(..., description="owner/repo, e.g. pandas-dev/pandas"),
    since: date = Query(..., description="inclusive start date (UTC)"),
    until: date = Query(..., description="exclusive end date (UTC)"),
) -> ContributorsResponse:
    if until <= since:
        raise HTTPException(status_code=400, detail="`until` must be after `since`")

    since_ts = _to_github_timestamp(since)
    until_ts = _to_github_timestamp(until)

    try:
        ingest_repo(repo, since=since_ts, until=until_ts)
    except InvalidRepoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RepoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

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
