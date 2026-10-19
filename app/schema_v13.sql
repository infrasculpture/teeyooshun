-- Teeyooshun database schema, version 13 ("Help me" on questions, STUDENT_UI_SPEC.md section 6).
-- Applied by app/db.py to a version 12 database, in one transaction.

BEGIN;

-- The written help for a question version, one row per tier that uses the model (1 = explain the question,
-- 2 = nudge). Tier 3 (the related card) is found by code and not stored. Written once, then reused.
CREATE TABLE question_help (
    question_id      TEXT NOT NULL,
    question_version INTEGER NOT NULL,
    tier             INTEGER NOT NULL CHECK (tier IN (1, 2)),
    help_json        TEXT NOT NULL,
    model            TEXT,
    api_call_id      INTEGER,
    created_at       TEXT NOT NULL,
    PRIMARY KEY (question_id, question_version, tier)
);

-- Each time she opens a tier of help. attempt_id is NULL until she submits; the first attempt that follows
-- takes the unclaimed rows (app/practice.py claim_hints) and is marked "with help".
CREATE TABLE hint_uses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT NOT NULL,
    tier        INTEGER NOT NULL CHECK (tier IN (1, 2, 3)),
    attempt_id  INTEGER REFERENCES attempts(id),
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_hint_uses_question ON hint_uses (question_id, attempt_id);

ALTER TABLE attempts ADD COLUMN with_help INTEGER NOT NULL DEFAULT 0;

PRAGMA user_version = 13;

COMMIT;
