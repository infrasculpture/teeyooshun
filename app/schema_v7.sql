-- Teeyooshun database schema, version 7 (Task 5: Revise mode).
-- Applied by app/db.py to a version 6 database, in one transaction.

BEGIN;

-- Book order for "new cards in book order": file name, first file page, row position.
-- Set when a card is approved; cards approved earlier get theirs from their staged item.
ALTER TABLE revision_cards ADD COLUMN sort_key TEXT NOT NULL DEFAULT '';

UPDATE revision_cards SET sort_key = COALESCE(
    (SELECT printf('%s:%04d:%08d', f.filename, CAST(json_extract(i.page_refs_json, '$[0]') AS INTEGER), i.id)
     FROM staged_items i JOIN source_files f ON f.id = i.file_id
     WHERE i.kind = 'card' AND i.status = 'approved' AND i.item_id = revision_cards.id
     ORDER BY i.id DESC LIMIT 1),
    id);

-- A topic a parent has switched on or off. "not_taught" hides the topic in Practise and Revise.
-- A topic with no row is offered as normal.
CREATE TABLE IF NOT EXISTS topic_status (
    topic      TEXT PRIMARY KEY,
    status     TEXT NOT NULL CHECK (status IN ('taught', 'not_taught')),
    set_by     TEXT NOT NULL DEFAULT 'parent',
    updated_at TEXT NOT NULL
);

-- Settings a parent can change on the settings page. Missing keys use the defaults in app/app_settings.py.
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,          -- JSON
    updated_at TEXT NOT NULL
);

-- One set of cards worked through together (a record of work done, and the end-of-set summary).
CREATE TABLE IF NOT EXISTS revise_sets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL DEFAULT 'student' CHECK (kind IN ('student', 'parent_quiz')),
    subject       TEXT NOT NULL DEFAULT '',
    topics_json   TEXT NOT NULL DEFAULT '[]',
    card_ids_json TEXT NOT NULL,
    exam_mode     INTEGER NOT NULL DEFAULT 0,
    recall_timing TEXT NOT NULL DEFAULT 'after_set',   -- after_set / after_each, as set when the set was made
    created_at    TEXT NOT NULL
);

-- Where each card is in the five-box system.
CREATE TABLE IF NOT EXISTS card_progress (
    card_id         TEXT PRIMARY KEY REFERENCES revision_cards (id),
    box             INTEGER NOT NULL,
    last_attempt_at TEXT NOT NULL,
    due_at          TEXT NOT NULL
);

-- Every recall of a card: typed (checked by the points engine), paper (self-tick) or a parent quiz.
CREATE TABLE IF NOT EXISTS recall_attempts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id               TEXT NOT NULL REFERENCES revision_cards (id),
    card_version          INTEGER NOT NULL,
    set_id                INTEGER REFERENCES revise_sets (id),
    method                TEXT NOT NULL CHECK (method IN ('typed', 'paper', 'parent_quiz')),
    points_total          INTEGER NOT NULL,
    points_covered        INTEGER NOT NULL,
    covered_ids_json      TEXT NOT NULL DEFAULT '[]',
    key_terms_missed_json TEXT NOT NULL DEFAULT '[]',
    typed_text            TEXT,
    command_word          TEXT NOT NULL DEFAULT '',
    command_note          TEXT NOT NULL DEFAULT '',   -- the command-word note shown, if one was relevant
    result_json           TEXT,                        -- typed checks: the checked marking result
    api_call_id           INTEGER REFERENCES api_calls (id),
    seconds_taken         INTEGER,
    box_before            INTEGER NOT NULL,
    box_after             INTEGER NOT NULL,
    created_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recall_card ON recall_attempts (card_id, created_at);
CREATE INDEX IF NOT EXISTS idx_recall_set ON recall_attempts (set_id);

PRAGMA user_version = 7;

COMMIT;
