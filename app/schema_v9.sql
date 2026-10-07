-- Teeyooshun database schema, version 9 (Ingestion Stage C: school notes and slide handouts).
-- Applied by app/db.py to a version 8 database, in one transaction.

BEGIN;

-- How a PDF's pages are laid out: 'a4' (one page each) or 'slides_4up' (four slides per page).
-- slide_order is 'down_then_across' or 'across_then_down' for slides_4up, else ''.
-- A change to either makes the file be read again as a new version.
ALTER TABLE source_files ADD COLUMN layout TEXT NOT NULL DEFAULT 'a4';
ALTER TABLE source_files ADD COLUMN slide_order TEXT NOT NULL DEFAULT '';

-- Printed text of notes pages and slides, for "Help me remember" and the hint button (not shown to her yet).
-- Rebuilt whenever the file's items are rebuilt; removed when the file version is superseded.
CREATE TABLE IF NOT EXISTS reference_passages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     INTEGER NOT NULL REFERENCES source_files (id),
    subject     TEXT NOT NULL,
    level       TEXT NOT NULL,
    topic       TEXT NOT NULL,
    source_ref  TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL,
    page_no     INTEGER NOT NULL,
    page_render TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_passages_topic ON reference_passages (subject, level, topic);

-- Printed learning outcomes ("By the end of this chapter you should be able to ...").
CREATE TABLE IF NOT EXISTS topic_outcomes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id    INTEGER NOT NULL REFERENCES source_files (id),
    subject    TEXT NOT NULL,
    level      TEXT NOT NULL,
    topic      TEXT NOT NULL,
    text       TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    page_no    INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outcomes_topic ON topic_outcomes (subject, level, topic);

PRAGMA user_version = 9;

COMMIT;
