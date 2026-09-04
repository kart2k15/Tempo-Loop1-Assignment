from __future__ import annotations

import re
from typing import Any, Iterator, Optional

import httpx

from app.config import settings

GITHUB_API_BASE = "https://api.github.com"

# owner/repo as GitHub allows them: alphanumerics, hyphen, underscore, dot.
REPO_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


class GitHubClientError(Exception):
    """Base class for all GitHub client errors."""


class InvalidRepoError(GitHubClientError):
    """Raised when an owner/repo string doesn't match GitHub's naming rules."""


class RepoNotFoundError(GitHubClientError):
    """Raised when GitHub returns 404 for a repo (nonexistent or not visible to this token)."""


class RateLimitError(GitHubClientError):
    """Raised when GitHub's rate limit has been exhausted."""

    def __init__(self, message: str, reset_at: Optional[int] = None):
        super().__init__(message)
        self.reset_at = reset_at


def validate_repo(repo: str) -> None:
    if not REPO_PATTERN.match(repo):
        raise InvalidRepoError(f"'{repo}' is not a valid owner/repo string")


class GitHubClient:
    """Thin wrapper over the GitHub REST API: auth, pagination, and typed errors."""

    def __init__(self, token: Optional[str] = None):
        self._client = httpx.Client(
            base_url=GITHUB_API_BASE,
            headers={
                "Authorization": f"Bearer {token or settings.github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _get(self, url: str, params: Optional[dict[str, Any]] = None) -> httpx.Response:
        resp = self._client.get(url, params=params)
        if resp.status_code == 404:
            raise RepoNotFoundError(f"GitHub returned 404 for {url}")
        if resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
            reset_at = int(resp.headers.get("x-ratelimit-reset", 0))
            raise RateLimitError("GitHub API rate limit exceeded", reset_at=reset_at)
        resp.raise_for_status()
        return resp

    def _paginate(self, path: str, params: Optional[dict[str, Any]] = None) -> Iterator[dict]:
        params = dict(params or {})
        params.setdefault("per_page", 100)

        url: Optional[str] = path
        next_params = params
        while url:
            resp = self._get(url, params=next_params)
            yield from resp.json()
            url = resp.links.get("next", {}).get("url")
            next_params = None  # the "next" URL already carries its own query string

    def list_commits(self, repo: str, since: str, until: str) -> Iterator[dict]:
        """Commits on the default branch in [since, until), ISO 8601 timestamps.

        Validates and dispatches eagerly (returns the generator from _paginate rather than
        being a generator itself via `yield from`) so InvalidRepoError raises at call time,
        not on first iteration.
        """
        validate_repo(repo)
        return self._paginate(f"/repos/{repo}/commits", {"since": since, "until": until})

    def list_pull_requests(self, repo: str, state: str = "all") -> Iterator[dict]:
        """PR list endpoint does not support date filtering server-side; callers filter by
        created_at/merged_at client-side after fetching."""
        validate_repo(repo)
        return self._paginate(
            f"/repos/{repo}/pulls",
            {"state": state, "sort": "created", "direction": "desc"},
        )

    def get_pull_request(self, repo: str, number: int) -> dict:
        """Single-PR detail, needed for additions/deletions (not present on the list endpoint)."""
        validate_repo(repo)
        return self._get(f"/repos/{repo}/pulls/{number}").json()

    def list_reviews(self, repo: str, pr_number: int) -> Iterator[dict]:
        validate_repo(repo)
        return self._paginate(f"/repos/{repo}/pulls/{pr_number}/reviews")

    def list_issues(self, repo: str, since: str, state: str = "all") -> Iterator[dict]:
        """Includes PRs (GitHub models PRs as issues); callers filter those out via the
        `pull_request` key present only on PR-backed issues."""
        validate_repo(repo)
        return self._paginate(f"/repos/{repo}/issues", {"since": since, "state": state})
