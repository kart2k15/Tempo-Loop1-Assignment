from __future__ import annotations

from app.db.connection import db_session
from app.metrics import ContributorStats, compute_contributors

REPO = "o/r"


def seed_commit(conn, sha, author_login, authored_at, repo=REPO):
    conn.execute(
        "INSERT OR REPLACE INTO commits (repo, sha, author_login, authored_at) VALUES (?, ?, ?, ?)",
        (repo, sha, author_login, authored_at),
    )


def seed_pr(conn, number, author_login, merged_at, additions, deletions, repo=REPO, created_at=None):
    conn.execute(
        """
        INSERT OR REPLACE INTO pull_requests
            (repo, number, author_login, created_at, merged_at, additions, deletions)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (repo, number, author_login, created_at or merged_at, merged_at, additions, deletions),
    )


class TestComputeContributors:
    def test_empty_window_returns_empty_list(self, test_db):
        assert compute_contributors(REPO, since="2025-01-01T00:00:00Z", until="2025-02-01T00:00:00Z") == []

    def test_ranks_by_commits_then_prs_then_lines(self, test_db):
        with db_session() as conn:
            seed_commit(conn, "s1", "alice", "2025-01-10T00:00:00Z")
            seed_commit(conn, "s2", "alice", "2025-01-11T00:00:00Z")
            seed_commit(conn, "s3", "bob", "2025-01-12T00:00:00Z")
            seed_pr(conn, 1, "bob", "2025-01-15T00:00:00Z", additions=50, deletions=10)

        stats = compute_contributors(REPO, since="2025-01-01T00:00:00Z", until="2025-02-01T00:00:00Z")

        assert stats == [
            ContributorStats(login="alice", commits=2, prs_merged=0, lines_changed=0),
            ContributorStats(login="bob", commits=1, prs_merged=1, lines_changed=60),
        ]

    def test_excludes_rows_outside_the_requested_window(self, test_db):
        with db_session() as conn:
            seed_commit(conn, "in", "alice", "2025-01-15T00:00:00Z")
            seed_commit(conn, "before", "alice", "2024-12-31T23:59:59Z")
            seed_commit(conn, "after", "alice", "2025-02-01T00:00:00Z")  # `until` is exclusive
            seed_pr(conn, 1, "alice", "2025-01-15T00:00:00Z", additions=5, deletions=0)
            seed_pr(conn, 2, "alice", "2025-02-01T00:00:00Z", additions=999, deletions=999)

        stats = compute_contributors(REPO, since="2025-01-01T00:00:00Z", until="2025-02-01T00:00:00Z")

        assert stats == [ContributorStats(login="alice", commits=1, prs_merged=1, lines_changed=5)]

    def test_excludes_other_repos(self, test_db):
        with db_session() as conn:
            seed_commit(conn, "s1", "alice", "2025-01-10T00:00:00Z", repo="other/repo")

        assert compute_contributors(REPO, since="2025-01-01T00:00:00Z", until="2025-02-01T00:00:00Z") == []

    def test_unmerged_prs_do_not_count(self, test_db):
        with db_session() as conn:
            seed_pr(conn, 1, "alice", merged_at=None, additions=100, deletions=100, created_at="2025-01-10T00:00:00Z")

        assert compute_contributors(REPO, since="2025-01-01T00:00:00Z", until="2025-02-01T00:00:00Z") == []

    def test_ties_break_alphabetically_for_determinism(self, test_db):
        with db_session() as conn:
            seed_commit(conn, "s1", "zed", "2025-01-10T00:00:00Z")
            seed_commit(conn, "s2", "amy", "2025-01-10T00:00:00Z")

        stats = compute_contributors(REPO, since="2025-01-01T00:00:00Z", until="2025-02-01T00:00:00Z")

        assert [s.login for s in stats] == ["amy", "zed"]
