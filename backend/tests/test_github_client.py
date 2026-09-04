import httpx
import pytest

from app.github_client import (
    GitHubClient,
    InvalidRepoError,
    RateLimitError,
    RepoNotFoundError,
    validate_repo,
)


def make_client(handler) -> GitHubClient:
    return GitHubClient(token="fake-token", transport=httpx.MockTransport(handler))


class TestGitHubClient:
    def test_validate_repo_accepts_well_formed_owner_repo(self):
        validate_repo("pandas-dev/pandas")  # no raise

    @pytest.mark.parametrize("bad_repo", ["not a repo", "no-slash", "/leading-slash", "trailing-slash/", ""])
    def test_validate_repo_rejects_malformed_input(self, bad_repo):
        with pytest.raises(InvalidRepoError):
            validate_repo(bad_repo)

    def test_list_commits_validates_eagerly_not_on_iteration(self):
        """Regression test: list_commits must raise at call time, not on first next().

        Caught during manual testing: the original implementation used `yield from`,
        making it a generator function, so validate_repo() silently never ran until
        the caller started iterating.
        """
        client = make_client(lambda request: httpx.Response(200, json=[]))
        with pytest.raises(InvalidRepoError):
            client.list_commits("not a repo", since="2020-01-01", until="2020-02-01")

    def test_pagination_follows_link_header_across_pages(self):
        page_1 = [{"sha": "aaa"}, {"sha": "bbb"}]
        page_2 = [{"sha": "ccc"}]

        def handler(request: httpx.Request) -> httpx.Response:
            if "page=2" in str(request.url):
                return httpx.Response(200, json=page_2)
            return httpx.Response(
                200,
                json=page_1,
                headers={"Link": '<https://api.github.com/repos/o/r/commits?page=2>; rel="next"'},
            )

        client = make_client(handler)
        commits = list(client.list_commits("o/r", since="2020-01-01T00:00:00Z", until="2020-02-01T00:00:00Z"))

        assert [c["sha"] for c in commits] == ["aaa", "bbb", "ccc"]

    def test_404_raises_repo_not_found_error(self):
        client = make_client(lambda request: httpx.Response(404, json={"message": "Not Found"}))
        with pytest.raises(RepoNotFoundError):
            list(client.list_commits("o/r", since="2020-01-01T00:00:00Z", until="2020-02-01T00:00:00Z"))

    def test_rate_limited_403_raises_rate_limit_error_with_reset_time(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1700000000"},
            )

        client = make_client(handler)
        with pytest.raises(RateLimitError) as exc_info:
            list(client.list_commits("o/r", since="2020-01-01T00:00:00Z", until="2020-02-01T00:00:00Z"))
        assert exc_info.value.reset_at == 1700000000

    def test_non_rate_limit_403_raises_generic_http_error(self):
        """A 403 that isn't rate-limiting (e.g. blocked/forbidden) should not be
        misreported as RateLimitError."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "Forbidden"}, headers={"x-ratelimit-remaining": "42"})

        client = make_client(handler)
        with pytest.raises(httpx.HTTPStatusError):
            list(client.list_commits("o/r", since="2020-01-01T00:00:00Z", until="2020-02-01T00:00:00Z"))
