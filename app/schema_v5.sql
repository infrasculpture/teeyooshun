-- Teeyooshun database schema, version 5 (MCQ explanations).
-- Applied by app/db.py to a version 4 database, in one transaction.

BEGIN;

-- One short explanation per (question version, option chosen), written by the light
-- model the first time that option is chosen and reused after that.
CREATE TABLE IF NOT EXISTS mcq_explanations (
    question_id       TEXT NOT NULL REFERENCES questions (id),
    question_version  INTEGER NOT NULL,
    chosen_option     TEXT NOT NULL,
    why_correct       TEXT NOT NULL,
    why_chosen_wrong  TEXT NOT NULL DEFAULT '',
    misconception     TEXT NOT NULL DEFAULT '',
    model             TEXT,
    api_call_id       INTEGER REFERENCES api_calls (id),
    created_at        TEXT NOT NULL,
    PRIMARY KEY (question_id, question_version, chosen_option)
);

PRAGMA user_version = 5;

COMMIT;
