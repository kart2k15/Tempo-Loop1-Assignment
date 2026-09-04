from __future__ import annotations

from dataclasses import dataclass
from typing import List

from app.db.connection import db_session


@dataclass(frozen=True)
class ContributorStats:
    login: str
    commits: int
    prs_merged: int
    lines_changed: int


@dataclass(frozen=True)
class CollaborationEdge:
    author: str
    reviewer: str
    reviews: int


def compute_contributors(repo: str, since: str, until: str) -> List[ContributorStats]:
    """Top contributors in [since, until), ranked by (commits, prs_merged, lines_changed).

    Both counts come from SQL GROUP BY aggregation over the ingested rows for this repo, not
    a Python-side loop, so this stays cheap regardless of how many commits/PRs are ingested.

    commits/pull_requests may hold rows from other previously-ingested windows for the same
    repo (upserted by primary key, not partitioned by window) - the date filters here are what
    keep this call scoped correctly to the requested window regardless of ingestion history.
    """
    with db_session() as conn:
        commit_counts = {
            row["author_login"]: row["commits"]
            for row in conn.execute(
                """
                SELECT author_login, COUNT(*) AS commits
                FROM commits
                WHERE repo = ? AND authored_at >= ? AND authored_at < ? AND author_login IS NOT NULL
                GROUP BY author_login
                """,
                (repo, since, until),
            )
        }
        pr_stats = {
            row["author_login"]: (row["prs_merged"], row["lines_changed"])
            for row in conn.execute(
                """
                SELECT author_login,
                       COUNT(*) AS prs_merged,
                       COALESCE(SUM(additions), 0) + COALESCE(SUM(deletions), 0) AS lines_changed
                FROM pull_requests
                WHERE repo = ? AND merged_at >= ? AND merged_at < ? AND author_login IS NOT NULL
                GROUP BY author_login
                """,
                (repo, since, until),
            )
        }

    logins = sorted(set(commit_counts) | set(pr_stats))  # sorted for a deterministic tie-break
    stats = [
        ContributorStats(
            login=login,
            commits=commit_counts.get(login, 0),
            prs_merged=pr_stats.get(login, (0, 0))[0],
            lines_changed=pr_stats.get(login, (0, 0))[1],
        )
        for login in logins
    ]
    stats.sort(key=lambda s: (s.commits, s.prs_merged, s.lines_changed), reverse=True)
    return stats


def compute_collaboration_edges(repo: str, since: str, until: str) -> List[CollaborationEdge]:
    """Author <-> reviewer edges for PRs merged in [since, until): how many times `reviewer`
    reviewed a PR authored by `author`. Self-reviews are excluded - not a meaningful
    collaboration signal. Requires reviews to have been ingested for this window.
    """
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT pr.author_login AS author, r.reviewer_login AS reviewer, COUNT(*) AS reviews
            FROM reviews r
            JOIN pull_requests pr ON pr.repo = r.repo AND pr.number = r.pr_number
            WHERE r.repo = ?
              AND pr.merged_at >= ? AND pr.merged_at < ?
              AND pr.author_login IS NOT NULL
              AND r.reviewer_login IS NOT NULL
              AND pr.author_login != r.reviewer_login
            GROUP BY pr.author_login, r.reviewer_login
            ORDER BY author, reviewer
            """,
            (repo, since, until),
        )
        return [
            CollaborationEdge(author=row["author"], reviewer=row["reviewer"], reviews=row["reviews"])
            for row in rows
        ]
