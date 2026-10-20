-- Teeyooshun database schema, version 14 ("Ask about this", STUDENT_UI_SPEC.md section 7).
-- Applied by app/db.py to a version 13 database, in one transaction.

BEGIN;

-- Every "Ask about this" conversation. A conversation is one page view (conv_key is made when the page is drawn),
-- so it starts empty each time she moves on. messages_json is a list of {"role": "student" | "tutor", "text", "ts"}
-- (ts is UTC; the daily cap counts her messages by it). stage is whether she had submitted when it began.
CREATE TABLE discussions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_key         TEXT NOT NULL UNIQUE,
    kind             TEXT NOT NULL CHECK (kind IN ('question', 'card')),
    scope_id         TEXT NOT NULL,                    -- the question id or the card id
    attempt_id       INTEGER REFERENCES attempts(id),  -- the marked attempt, for a conversation after submitting
    set_id           INTEGER,                          -- the Revise set, for a card
    stage            TEXT NOT NULL CHECK (stage IN ('before', 'after')),
    messages_json    TEXT NOT NULL,
    student_messages INTEGER NOT NULL DEFAULT 0,
    tokens           INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX idx_discussions_updated ON discussions (updated_at);

-- hint_uses gains tier 4 = "asked about this before submitting". SQLite can't change a CHECK in place, so the
-- table is rebuilt with its rows. attempts.with_help stays the one place that says "with help".
CREATE TABLE hint_uses_new (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT NOT NULL,
    tier        INTEGER NOT NULL CHECK (tier IN (1, 2, 3, 4)),
    attempt_id  INTEGER REFERENCES attempts(id),
    created_at  TEXT NOT NULL
);
INSERT INTO hint_uses_new (id, question_id, tier, attempt_id, created_at)
    SELECT id, question_id, tier, attempt_id, created_at FROM hint_uses;
DROP TABLE hint_uses;
ALTER TABLE hint_uses_new RENAME TO hint_uses;
CREATE INDEX idx_hint_uses_question ON hint_uses (question_id, attempt_id);

PRAGMA user_version = 14;

COMMIT;
