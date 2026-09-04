from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.db.connection import db_session
from app.github_client import InvalidRepoError, RateLimitError, RepoNotFoundError
from app.main import app


@pytest.fixture
def client(test_db):
    # app's lifespan calls init_db() again on startup - harmless, schema is idempotent
    # (CREATE TABLE IF NOT EXISTS), and it must run against the same test_db-patched path.
    with TestClient(app) as c:
        yield c


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestGetContributors:
    def test_returns_ranked_contributors_without_hitting_network(self, client):
        with db_session() as conn:
            conn.execute(
                "INSERT INTO commits (repo, sha, author_login, authored_at) VALUES (?, ?, ?, ?)",
                ("o/r", "s1", "alice", "2025-01-10T00:00:00Z"),
            )
        with patch("app.main.ingest_repo") as mock_ingest:
            resp = client.get("/insights/contributors", params={"repo": "o/r", "since": "2025-01-01", "until": "2025-02-01"})

        mock_ingest.assert_called_once_with("o/r", since="2025-01-01T00:00:00Z", until="2025-02-01T00:00:00Z")
        assert resp.status_code == 200
        body = resp.json()
        assert body["repo"] == "o/r"
        assert body["contributors"] == [{"login": "alice", "commits": 1, "prs_merged": 0, "lines_changed": 0}]

    def test_invalid_repo_maps_to_400(self, client):
        with patch("app.main.ingest_repo", side_effect=InvalidRepoError("bad repo")):
            resp = client.get("/insights/contributors", params={"repo": "bad", "since": "2025-01-01", "until": "2025-02-01"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "bad repo"

    def test_repo_not_found_maps_to_404(self, client):
        with patch("app.main.ingest_repo", side_effect=RepoNotFoundError("no such repo")):
            resp = client.get("/insights/contributors", params={"repo": "o/r", "since": "2025-01-01", "until": "2025-02-01"})
        assert resp.status_code == 404

    def test_rate_limit_maps_to_429(self, client):
        with patch("app.main.ingest_repo", side_effect=RateLimitError("rate limited")):
            resp = client.get("/insights/contributors", params={"repo": "o/r", "since": "2025-01-01", "until": "2025-02-01"})
        assert resp.status_code == 429

    def test_until_before_since_is_400_before_any_ingestion(self, client):
        with patch("app.main.ingest_repo") as mock_ingest:
            resp = client.get("/insights/contributors", params={"repo": "o/r", "since": "2025-02-01", "until": "2025-01-01"})
        assert resp.status_code == 400
        mock_ingest.assert_not_called()

    def test_missing_params_is_422(self, client):
        resp = client.get("/insights/contributors")
        assert resp.status_code == 422

    def test_malformed_date_is_422(self, client):
        resp = client.get("/insights/contributors", params={"repo": "o/r", "since": "not-a-date", "until": "2025-01-01"})
        assert resp.status_code == 422
