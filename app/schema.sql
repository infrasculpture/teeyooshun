-- Teeyooshun database schema, version 1.
-- Applied by app/db.py when the database is new (PRAGMA user_version = 0).

-- One row per question. record_json holds the full question as imported
-- (never the calibration test answers).
CREATE TABLE IF NOT EXISTS questions (
    id           TEXT PRIMARY KEY,
    subject      TEXT NOT NULL,
    level        TEXT NOT NULL,
    topic        TEXT NOT NULL,
    source       TEXT NOT NULL,
    source_ref   TEXT NOT NULL DEFAULT '',
    marking_mode TEXT NOT NULL CHECK (marking_mode IN ('points', 'lorms')),
    marks        INTEGER NOT NULL,
    record_json  TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    imported_at  TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_questions_tags ON questions (subject, level, topic);

-- Every Claude API call, including failed ones, for the monthly cost view.
CREATE TABLE IF NOT EXISTS api_calls (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at                  TEXT NOT NULL,
    purpose                     TEXT NOT NULL,          -- marking / calibration / ...
    model                       TEXT NOT NULL,          -- model that served the call
    question_id                 TEXT,
    input_tokens                INTEGER NOT NULL DEFAULT 0,
    output_tokens               INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens     INTEGER NOT NULL DEFAULT 0,
    duration_ms                 INTEGER,
    stop_reason                 TEXT,
    request_id                  TEXT,
    error                       TEXT
);

CREATE INDEX IF NOT EXISTS idx_api_calls_created ON api_calls (created_at);

-- One row per marked student answer. Calibration runs do not write here.
CREATE TABLE IF NOT EXISTS attempts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id       TEXT NOT NULL REFERENCES questions (id),
    parent_attempt_id INTEGER REFERENCES attempts (id),  -- set on a rewrite
    answer_text       TEXT NOT NULL,
    marks_awarded     INTEGER,                           -- NULL if marking failed
    max_marks         INTEGER NOT NULL,
    valid_points      INTEGER,                           -- points mode: valid points found
    counted_points    INTEGER,                           -- points mode: points counted after the "any N" cap
    level_awarded     INTEGER,                           -- LORMS mode
    confidence        TEXT CHECK (confidence IN ('high', 'medium', 'low')),
    confidence_reason TEXT NOT NULL DEFAULT '',
    flags_json        TEXT NOT NULL DEFAULT '[]',        -- every flag that fired
    model_output_json TEXT,                              -- raw JSON from the model
    result_json       TEXT NOT NULL,                     -- checked result, for feedback
    model             TEXT,
    api_call_id       INTEGER REFERENCES api_calls (id),
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_question ON attempts (question_id);
CREATE INDEX IF NOT EXISTS idx_attempts_created ON attempts (created_at);
