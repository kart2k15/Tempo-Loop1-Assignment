-- Tracks which (repo, since, until) windows have been ingested and when, so a query for a
-- window we've never fetched (or that's gone stale) always re-hits GitHub rather than
-- silently reusing data ingested for a different window.
CREATE TABLE IF NOT EXISTS ingestions (
    repo TEXT NOT NULL,
    since TEXT NOT NULL,
    until TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (repo, since, until)
);

CREATE TABLE IF NOT EXISTS commits (
    repo TEXT NOT NULL,
    sha TEXT NOT NULL,
    author_login TEXT,
    authored_at TEXT NOT NULL,
    PRIMARY KEY (repo, sha)
);
CREATE INDEX IF NOT EXISTS idx_commits_repo_date ON commits (repo, authored_at);

CREATE TABLE IF NOT EXISTS pull_requests (
    repo TEXT NOT NULL,
    number INTEGER NOT NULL,
    author_login TEXT,
    created_at TEXT NOT NULL,
    merged_at TEXT,
    additions INTEGER,
    deletions INTEGER,
    PRIMARY KEY (repo, number)
);
CREATE INDEX IF NOT EXISTS idx_prs_repo_merged ON pull_requests (repo, merged_at);

-- One row per review submitted on a merged PR we've ingested. Used to derive who reviewed
-- whose PRs (author_login on pull_requests <-> reviewer_login here), not a general-purpose
-- issue/review store - only covers PRs already in pull_requests.
CREATE TABLE IF NOT EXISTS reviews (
    repo TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    review_id INTEGER NOT NULL,
    reviewer_login TEXT,
    state TEXT,
    submitted_at TEXT,
    PRIMARY KEY (repo, review_id)
);
CREATE INDEX IF NOT EXISTS idx_reviews_repo_pr ON reviews (repo, pr_number);
