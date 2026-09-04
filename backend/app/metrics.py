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
