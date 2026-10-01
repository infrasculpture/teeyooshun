-- Teeyooshun database schema, version 3 (Task 4 Stage A: ingestion).
-- Applied by app/db.py to a version 2 database.
--
-- The questions table is rebuilt (SQLite's documented copy / drop / rename
-- method) so marking_mode can also be 'mcq' and a figure column can be added.
-- Foreign keys are switched off for the rebuild only; data is copied unchanged.

PRAGMA foreign_keys = OFF;

BEGIN;

CREATE TABLE questions_new (
    id           TEXT PRIMARY KEY,
    subject      TEXT NOT NULL,
    level        TEXT NOT NULL,
    topic        TEXT NOT NULL,
    source       TEXT NOT NULL,
    source_ref   TEXT NOT NULL DEFAULT '',
    marking_mode TEXT NOT NULL CHECK (marking_mode IN ('points', 'lorms', 'mcq')),
    marks        INTEGER NOT NULL,
    record_json  TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    imported_at  TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    -- needed_to_answer: she must draw or read a figure; left out of typed practice
    figure       TEXT NOT NULL DEFAULT 'none' CHECK (figure IN ('none', 'described', 'needed_to_answer'))
);

INSERT INTO questions_new
    (id, subject, level, topic, source, source_ref, marking_mode, marks, record_json,
     content_hash, imported_at, updated_at, version, figure)
SELECT id, subject, level, topic, source, source_ref, marking_mode, marks, record_json,
       content_hash, imported_at, updated_at, version, 'none'
FROM questions;

DROP TABLE questions;
ALTER TABLE questions_new RENAME TO questions;
CREATE INDEX IF NOT EXISTS idx_questions_tags ON questions (subject, level, topic);

-- The topic tree, loaded from docs/topics.yaml on every start.
CREATE TABLE IF NOT EXISTS topics (
    id           TEXT PRIMARY KEY,
    subject      TEXT NOT NULL,
    title        TEXT NOT NULL,
    theme        TEXT NOT NULL DEFAULT '',
    likely_level TEXT NOT NULL DEFAULT '',
    school_added INTEGER NOT NULL DEFAULT 0,
    sort_order   INTEGER NOT NULL DEFAULT 0
);

-- A source folder: data/sources/<subject>/<level>/<slug>/ with its source.yaml.
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL,
    title       TEXT NOT NULL,
    subject     TEXT NOT NULL,
    level       TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- One row per version of a PDF. A changed file (new hash) gets the next version.
CREATE TABLE IF NOT EXISTS source_files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     INTEGER NOT NULL REFERENCES sources (id),
    filename      TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    version       INTEGER NOT NULL,
    material_type TEXT NOT NULL,
    unit          TEXT,
    status        TEXT NOT NULL CHECK (status IN ('registered', 'done', 'failed', 'superseded')),
    page_count    INTEGER,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL,
    processed_at  TEXT,
    UNIQUE (source_id, filename, version)
);

-- One row per page. Cover pages keep only page_type and status: no render, no model output.
CREATE TABLE IF NOT EXISTS pages (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id                     INTEGER NOT NULL REFERENCES source_files (id),
    page_no                     INTEGER NOT NULL,
    render_path                 TEXT,
    rotation                    INTEGER NOT NULL DEFAULT 0,
    page_type                   TEXT,
    printed_page                TEXT NOT NULL DEFAULT '',
    status                      TEXT NOT NULL CHECK (status IN ('pending', 'done', 'skipped', 'failed')),
    model                       TEXT,
    input_tokens                INTEGER NOT NULL DEFAULT 0,
    output_tokens               INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens     INTEGER NOT NULL DEFAULT 0,
    cost_usd                    REAL,
    error                       TEXT,
    raw_json                    TEXT,
    created_at                  TEXT NOT NULL,
    UNIQUE (file_id, page_no)
);

-- Items waiting for review. Nothing reaches the question bank until approved.
CREATE TABLE IF NOT EXISTS staged_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id        INTEGER NOT NULL REFERENCES source_files (id),
    kind           TEXT NOT NULL CHECK (kind IN ('question', 'card')),
    item_id        TEXT NOT NULL,
    unit           TEXT NOT NULL DEFAULT '',
    qnum           TEXT NOT NULL DEFAULT '',
    part           TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'superseded')),
    record_json    TEXT NOT NULL,
    warnings_json  TEXT NOT NULL DEFAULT '[]',
    page_refs_json TEXT NOT NULL DEFAULT '[]',
    raw_json       TEXT,
    edited         INTEGER NOT NULL DEFAULT 0,
    reviewed_by    TEXT,
    reviewed_at    TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_staged_file ON staged_items (file_id, status);

-- Mark schemes read from answer keys, waiting to be matched to questions.
CREATE TABLE IF NOT EXISTS key_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         INTEGER NOT NULL REFERENCES source_files (id),
    unit            TEXT NOT NULL DEFAULT '',
    qnum            TEXT NOT NULL,
    part            TEXT NOT NULL DEFAULT '',
    scheme_json     TEXT NOT NULL,
    page_refs_json  TEXT NOT NULL DEFAULT '[]',
    matched_item_id INTEGER REFERENCES staged_items (id),
    superseded      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

PRAGMA user_version = 3;

COMMIT;

PRAGMA foreign_keys = ON;
