from __future__ import annotations

import httpx

from app.db.connection import db_session
from app.github_client import GitHubClient
from app.ingest import ingest_repo


def commit(sha, login, date):
    return {
        "sha": sha,
        "author": {"login": login} if login else None,
        "commit": {"author": {"date": date}},
    }


def pull_request(number, login, created_at, merged_at, additions=None, deletions=None):
    return {
        "number": number,
        "user": {"login": login},
        "created_at": created_at,
        "merged_at": merged_at,
        "additions": additions,
        "deletions": deletions,
    }


def review(review_id, login, state="APPROVED", submitted_at="2025-06-13T00:00:00Z"):
    return {
        "id": review_id,
        "user": {"login": login},
        "state": state,
        "submitted_at": submitted_at,
    }


def make_client(handler, calls=None):
    if calls is not None:
        inner_handler = handler

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return inner_handler(request)

    return GitHubClient(token="fake-token", transport=httpx.MockTransport(handler))


class TestIngestRepo:
    def test_writes_commits_and_only_merged_prs_in_range(self, test_db):
        calls: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/commits"):
                return httpx.Response(
                    200,
                    json=[
                        commit("aaa", "alice", "2025-06-05T00:00:00Z"),
                        commit("bbb", "bob", "2025-06-10T00:00:00Z"),
                    ],
                )
            if path.endswith("/pulls"):
                # sorted by created_at desc, as the real GitHub list endpoint is
                return httpx.Response(
                    200,
                    json=[
                        pull_request(3, "carol", "2025-06-12T00:00:00Z", "2025-06-14T00:00:00Z"),
                        pull_request(2, "dave", "2025-06-08T00:00:00Z", None),  # not merged
                        pull_request(1, "erin", "2025-05-01T00:00:00Z", "2025-05-02T00:00:00Z"),  # before `since`
                    ],
                )
            if path.endswith("/pulls/3"):
                return httpx.Response(
                    200,
                    json=pull_request(3, "carol", "2025-06-12T00:00:00Z", "2025-06-14T00:00:00Z", additions=10, deletions=2),
                )
            if path.endswith("/pulls/3/reviews"):
                return httpx.Response(200, json=[review(1001, "frank"), review(1002, "grace", state="COMMENTED")])
            raise AssertionError(f"unexpected request: {path}")

        client = make_client(handler, calls)
        ingest_repo("o/r", since="2025-06-01T00:00:00Z", until="2025-07-01T00:00:00Z", client=client)

        with db_session() as conn:
            commits = [dict(r) for r in conn.execute("SELECT sha, author_login FROM commits ORDER BY sha")]
            prs = [dict(r) for r in conn.execute("SELECT number, author_login, additions, deletions FROM pull_requests")]
            reviews = [
                dict(r) for r in conn.execute("SELECT pr_number, reviewer_login, state FROM reviews ORDER BY review_id")
            ]

        assert commits == [
            {"sha": "aaa", "author_login": "alice"},
            {"sha": "bbb", "author_login": "bob"},
        ]
        assert prs == [{"number": 3, "author_login": "carol", "additions": 10, "deletions": 2}]
        assert reviews == [
            {"pr_number": 3, "reviewer_login": "frank", "state": "APPROVED"},
            {"pr_number": 3, "reviewer_login": "grace", "state": "COMMENTED"},
        ]
        # PR #1 was created before `since` - pagination must stop before reaching it
        assert not any(c.endswith("/pulls/1") for c in calls)
        # PR #2 was never merged - must not trigger a detail or reviews call
        assert not any(c.endswith("/pulls/2") for c in calls)
        assert not any(c.endswith("/pulls/2/reviews") for c in calls)

    def test_skips_refetch_when_cache_is_fresh(self, test_db):
        client = make_client(lambda request: httpx.Response(200, json=[]))
        ingest_repo("o/r", since="2025-06-01T00:00:00Z", until="2025-07-01T00:00:00Z", client=client)

        def fail_if_called(request: httpx.Request) -> httpx.Response:
            raise AssertionError("must not hit the network on a cache-fresh call")

        client2 = make_client(fail_if_called)
        ingest_repo("o/r", since="2025-06-01T00:00:00Z", until="2025-07-01T00:00:00Z", client=client2)

    def test_refetches_for_a_different_window(self, test_db):
        calls: list = []
        client = make_client(lambda request: httpx.Response(200, json=[]), calls)

        ingest_repo("o/r", since="2025-06-01T00:00:00Z", until="2025-07-01T00:00:00Z", client=client)
        count_after_first = len(calls)

        ingest_repo("o/r", since="2025-01-01T00:00:00Z", until="2025-02-01T00:00:00Z", client=client)
        assert len(calls) > count_after_first, "a different (since, until) window must not be served from cache"

    def test_force_bypasses_cache(self, test_db):
        calls: list = []
        client = make_client(lambda request: httpx.Response(200, json=[]), calls)

        ingest_repo("o/r", since="2025-06-01T00:00:00Z", until="2025-07-01T00:00:00Z", client=client)
        count_after_first = len(calls)

        ingest_repo("o/r", since="2025-06-01T00:00:00Z", until="2025-07-01T00:00:00Z", client=client, force=True)
        assert len(calls) > count_after_first
