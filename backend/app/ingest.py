from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from app.config import settings
from app.db.connection import db_session
from app.github_client import GitHubClient


def _is_cache_fresh(repo: str, since: str, until: str) -> bool:
    with db_session() as conn:
        row = conn.execute(
            "SELECT fetched_at FROM ingestions WHERE repo = ? AND since = ? AND until = ?",
            (repo, since, until),
        ).fetchone()
    if row is None:
        return False
    fetched_at = datetime.fromisoformat(row["fetched_at"])
    age_seconds = (datetime.now(timezone.utc) - fetched_at).total_seconds()
    return age_seconds < settings.cache_ttl_seconds


def _collect_merged_prs_and_reviews(
    gh: GitHubClient, repo: str, since: str, until: str
) -> Tuple[List[dict], List[Tuple[int, dict]]]:
    """PRs merged within [since, until), plus every review submitted on each of them.

    Per-PR detail and per-PR review calls (the list endpoint has neither additions/deletions
    nor reviews) are bounded to only PRs actually merged in the window, not every PR in the
    repo's history.

    Known limitation: stops paginating once a PR's created_at falls before `since`, since the
    list endpoint is sorted by creation date descending - a long-lived PR opened before the
    window but merged inside it would be missed. Documented in NOTES.md.
    """
    merged_prs: List[dict] = []
    reviews: List[Tuple[int, dict]] = []
    for pr in gh.list_pull_requests(repo, state="all"):
        if pr["created_at"] < since:
            break
        merged_at = pr.get("merged_at")
        if merged_at and since <= merged_at < until:
            merged_prs.append(gh.get_pull_request(repo, pr["number"]))
            reviews.extend((pr["number"], review) for review in gh.list_reviews(repo, pr["number"]))
    return merged_prs, reviews


def ingest_repo(
    repo: str,
    since: str,
    until: str,
    force: bool = False,
    client: Optional[GitHubClient] = None,
) -> None:
    """Fetch commits, merged PRs, and their reviews for `repo` in [since, until) and upsert
    into SQLite.

    Skips the GitHub calls entirely if this exact (repo, since, until) window was already
    ingested within the cache TTL. `client` can be injected for testing.
    """
    if not force and _is_cache_fresh(repo, since, until):
        return

    owns_client = client is None
    gh = client or GitHubClient()
    try:
        commits = list(gh.list_commits(repo, since=since, until=until))
        merged_prs, reviews = _collect_merged_prs_and_reviews(gh, repo, since, until)
    finally:
        if owns_client:
            gh.close()

    with db_session() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO commits (repo, sha, author_login, authored_at)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    repo,
                    c["sha"],
                    (c.get("author") or {}).get("login"),
                    c["commit"]["author"]["date"],
                )
                for c in commits
            ],
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO pull_requests
                (repo, number, author_login, created_at, merged_at, additions, deletions)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    repo,
                    pr["number"],
                    (pr.get("user") or {}).get("login"),
                    pr["created_at"],
                    pr["merged_at"],
                    pr.get("additions"),
                    pr.get("deletions"),
                )
                for pr in merged_prs
            ],
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO reviews (repo, pr_number, review_id, reviewer_login, state, submitted_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    repo,
                    pr_number,
                    review["id"],
                    (review.get("user") or {}).get("login"),
                    review.get("state"),
                    review.get("submitted_at"),
                )
                for pr_number, review in reviews
            ],
        )
        conn.execute(
            "INSERT OR REPLACE INTO ingestions (repo, since, until, fetched_at) VALUES (?, ?, ?, ?)",
            (repo, since, until, datetime.now(timezone.utc).isoformat()),
        )
