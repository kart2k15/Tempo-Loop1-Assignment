from __future__ import annotations

import time

import pytest

from app.config import settings
from app.db.connection import db_session
from app.ingest import ingest_repo

pytestmark = pytest.mark.integration

requires_github_token = pytest.mark.skipif(
    not settings.github_token,
    reason="requires a real GITHUB_TOKEN in .env to hit the live GitHub API",
)


@requires_github_token
class TestIngestRepoIntegration:
    """Exercises ingest_repo() against the real GitHub API and a real SQLite file - no mocks.

    Uses a fixed, already-elapsed historical window on pallets/flask so merged-PR/commit
    counts are stable: merged_at is set once at merge time and can't change retroactively,
    so a window fully in the past won't gain or lose merged PRs on a later re-run.
    """

    REPO = "pallets/flask"
    SINCE = "2025-08-01T00:00:00Z"
    UNTIL = "2025-09-05T00:00:00Z"

    def test_full_sync_against_live_github(self, test_db):
        ingest_repo(self.REPO, since=self.SINCE, until=self.UNTIL, force=True)

        with db_session() as conn:
            n_commits = conn.execute(
                "SELECT COUNT(*) c FROM commits WHERE repo = ?", (self.REPO,)
            ).fetchone()["c"]
            prs = [
                dict(r)
                for r in conn.execute(
                    "SELECT number, additions, deletions FROM pull_requests WHERE repo = ?",
                    (self.REPO,),
                )
            ]

        assert n_commits == 23
        assert len(prs) == 5
        for pr in prs:
            assert pr["additions"] is not None
            assert pr["deletions"] is not None

    def test_second_call_is_a_cache_hit_no_network_needed(self, test_db):
        ingest_repo(self.REPO, since=self.SINCE, until=self.UNTIL, force=True)

        start = time.time()
        ingest_repo(self.REPO, since=self.SINCE, until=self.UNTIL)  # not forced -> must hit cache
        elapsed = time.time() - start

        assert elapsed < 0.5, f"expected a fast cache hit with no network call, took {elapsed:.2f}s"
