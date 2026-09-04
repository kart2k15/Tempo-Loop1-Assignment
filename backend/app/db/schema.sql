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
