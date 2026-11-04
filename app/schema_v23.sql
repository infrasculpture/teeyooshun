-- Teeyooshun database schema, version 23 (the scaffolded recall ladder: word bank, fill the gaps, starters, free
-- recall; stuck and retired cards; low-energy sessions).
-- Applied by app/db.py to a version 22 database, in one transaction. Columns are added and two tables are created; no
-- row changes. Back up the database first anyway.

BEGIN;

-- A card that fails (under the "move down" percentage) several times in a row is stuck: left out of normal sets and
-- listed for the parent. A parent can also retire a card (left out for good; its history is kept).
ALTER TABLE card_progress ADD COLUMN consecutive_fails INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_progress ADD COLUMN stuck INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_progress ADD COLUMN stuck_at TEXT;
ALTER TABLE card_progress ADD COLUMN retired INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_progress ADD COLUMN retired_at TEXT;

-- How the card was recalled: wordbank / gaps / starters / free / match, or NULL for older attempts (typed, paper
-- and parent quiz keep using `method`). help_starts counts the "Give me a start" uses; low_energy is 1 when the
-- set was a low-energy one.
ALTER TABLE recall_attempts ADD COLUMN format TEXT;
ALTER TABLE recall_attempts ADD COLUMN help_starts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE recall_attempts ADD COLUMN low_energy INTEGER NOT NULL DEFAULT 0;

-- "Low energy today": chosen on the Revise start screen (revise_sets) or the session start screen (sessions).
ALTER TABLE revise_sets ADD COLUMN low_energy INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN low_energy INTEGER NOT NULL DEFAULT 0;

-- The format chosen for a card in a set, fixed when she first opens it so a reload never changes it.
CREATE TABLE recall_work (
    set_id     INTEGER NOT NULL REFERENCES revise_sets (id),
    card_id    TEXT NOT NULL REFERENCES revision_cards (id),
    format     TEXT NOT NULL,
    started_at TEXT NOT NULL,
    PRIMARY KEY (set_id, card_id)
);

-- One row per point (or slot) of a card in a set, so she can leave mid-card and the dashboard can count formats and
-- "Give me a start" use. slot is 1-based. unit_id is the card point it was credited to (free recall: the point the
-- slot matched, NULL if none). score is NULL while a start has been shown but the point is not answered yet.
CREATE TABLE recall_points (
    set_id        INTEGER NOT NULL REFERENCES revise_sets (id),
    card_id       TEXT NOT NULL REFERENCES revision_cards (id),
    slot          INTEGER NOT NULL,
    unit_id       TEXT,
    response      TEXT NOT NULL DEFAULT '',
    score         REAL,
    start_used    INTEGER NOT NULL DEFAULT 0,
    feedback_json TEXT,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (set_id, card_id, slot)
);
CREATE INDEX idx_recall_points_created ON recall_points (created_at);

PRAGMA user_version = 23;

COMMIT;
