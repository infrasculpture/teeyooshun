-- Teeyooshun database schema, version 2 (Task 3: student practice loop).
-- Applied by app/db.py to a version 1 database, in one transaction.

BEGIN;

-- Question versions: an edited question gets a new version; attempts keep
-- pointing at the version she answered.
ALTER TABLE questions ADD COLUMN version INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS question_versions (
    question_id  TEXT NOT NULL REFERENCES questions (id),
    version      INTEGER NOT NULL,
    record_json  TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (question_id, version)
);

INSERT OR IGNORE INTO question_versions (question_id, version, record_json, content_hash, created_at)
    SELECT id, 1, record_json, content_hash, imported_at FROM questions;

ALTER TABLE attempts ADD COLUMN question_version INTEGER NOT NULL DEFAULT 1;

-- Seconds from the question appearing to submitting. Never shown during a
-- question; used later for pace stats (medians, extremes ignored).
ALTER TABLE attempts ADD COLUMN seconds_taken INTEGER;

-- One rewrite per first attempt, enforced by the database too.
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_rewrite
    ON attempts (parent_attempt_id) WHERE parent_attempt_id IS NOT NULL;

-- Things that happen to an attempt after it is marked. Rows are only ever
-- added, never changed or deleted. For agree/disagree the latest row counts.
CREATE TABLE IF NOT EXISTS attempt_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL REFERENCES attempts (id),
    event      TEXT NOT NULL CHECK (event IN
                   ('rewrite_skipped', 'parent_agree', 'parent_disagree', 'marking_looks_wrong')),
    actor      TEXT NOT NULL CHECK (actor IN ('student', 'parent')),
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_attempt ON attempt_events (attempt_id);

-- At most one skip per attempt.
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_skip
    ON attempt_events (attempt_id) WHERE event = 'rewrite_skipped';

PRAGMA user_version = 2;

COMMIT;
