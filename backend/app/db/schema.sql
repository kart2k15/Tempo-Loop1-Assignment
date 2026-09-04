CREATE TABLE IF NOT EXISTS repos (
    repo TEXT PRIMARY KEY,
    last_fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commits (
    repo TEXT NOT NULL,
    sha TEXT NOT NULL,
    author_login TEXT,
    authored_at TEXT NOT NULL,
    additions INTEGER,
    deletions INTEGER,
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

CREATE TABLE IF NOT EXISTS reviews (
    repo TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    review_id INTEGER NOT NULL,
    reviewer_login TEXT,
    state TEXT,
    submitted_at TEXT,
    PRIMARY KEY (repo, review_id)
);
CREATE INDEX IF NOT EXISTS idx_reviews_repo_date ON reviews (repo, submitted_at);

CREATE TABLE IF NOT EXISTS issues (
    repo TEXT NOT NULL,
    number INTEGER NOT NULL,
    closer_login TEXT,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    is_pull_request INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (repo, number)
);
CREATE INDEX IF NOT EXISTS idx_issues_repo_closed ON issues (repo, closed_at);
