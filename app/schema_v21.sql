-- Teeyooshun database schema, version 21 ("Read about this": book references with on-screen pages).
-- Applied by app/db.py to a version 20 database, in one transaction. hint_uses is rebuilt with its rows (to allow
-- tier 5) and one new table is added; no other row changes. Back up the database first anyway.

BEGIN;

-- hint_uses gains tier 5 = "Read about this, opened before submitting". SQLite can't change a CHECK in place, so the
-- table is rebuilt with its rows, as schema v14 did. attempts.with_help stays the one place that says "with help".
CREATE TABLE hint_uses_new (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT NOT NULL,
    tier        INTEGER NOT NULL CHECK (tier IN (1, 2, 3, 4, 5)),
    attempt_id  INTEGER REFERENCES attempts(id),
    created_at  TEXT NOT NULL
);
INSERT INTO hint_uses_new (id, question_id, tier, attempt_id, created_at)
    SELECT id, question_id, tier, attempt_id, created_at FROM hint_uses;
DROP TABLE hint_uses;
ALTER TABLE hint_uses_new RENAME TO hint_uses;
CREATE INDEX idx_hint_uses_question ON hint_uses (question_id, attempt_id);
CREATE INDEX idx_hint_uses_created ON hint_uses (created_at);   -- added by schema v17; dropping the table dropped it

-- Every time she opened "Read about this" (before submitting, after submitting, or in Revise), for the parent's
-- dashboard and session log. Only a read before submitting is also help (it has a hint_uses row, tier 5).
-- Plain columns, no foreign keys: this is a log.
CREATE TABLE reference_uses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL CHECK (kind IN ('question', 'card')),
    scope_id   TEXT NOT NULL,                  -- the question id or the card id
    subject    TEXT NOT NULL,
    stage      TEXT NOT NULL CHECK (stage IN ('before', 'after', 'revise')),
    attempt_id INTEGER,                        -- the marked attempt, for a read after submitting
    set_id     INTEGER,                        -- the Revise set, for a card
    session_id INTEGER,                        -- the session it happened in, if any
    created_at TEXT NOT NULL
);
CREATE INDEX idx_reference_uses_created ON reference_uses (created_at);
CREATE INDEX idx_reference_uses_session ON reference_uses (session_id);

PRAGMA user_version = 21;

COMMIT;
