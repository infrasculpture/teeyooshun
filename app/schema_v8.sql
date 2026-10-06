-- Teeyooshun database schema, version 8 (Task 5b: "Help me remember" on revision cards).
-- Applied by app/db.py to a version 7 database, in one transaction.

BEGIN;

-- The help for one card version: why it matters, where the marks are, the common slip, a memory hook.
-- Written once by LIGHT_MODEL and reused. A parent's edit is kept (edited_by_parent) until the card changes.
CREATE TABLE IF NOT EXISTS card_help (
    card_id          TEXT NOT NULL REFERENCES revision_cards (id),
    card_version     INTEGER NOT NULL,
    help_json        TEXT NOT NULL,        -- {"why", "marks_in", "common_slip", "hook"}
    model            TEXT NOT NULL DEFAULT '',
    api_call_id      INTEGER REFERENCES api_calls (id),
    edited_by_parent INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    PRIMARY KEY (card_id, card_version)
);

-- Each time she opens the help panel. set_id is empty on the standalone card page.
-- followups_used counts the "I still don't get..." replies given from that opening.
CREATE TABLE IF NOT EXISTS help_views (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id        TEXT NOT NULL REFERENCES revision_cards (id),
    card_version   INTEGER NOT NULL,
    set_id         INTEGER REFERENCES revise_sets (id),
    opened_at      TEXT NOT NULL,
    followups_used INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_help_views_card ON help_views (card_id, opened_at);

-- "This help looks wrong" flags, listed on the parent card-help page until a parent marks them checked.
CREATE TABLE IF NOT EXISTS help_flags (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id      TEXT NOT NULL REFERENCES revision_cards (id),
    card_version INTEGER NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    flagged_by   TEXT NOT NULL DEFAULT 'student',
    created_at   TEXT NOT NULL,
    resolved_at  TEXT
);

-- 1 if she opened the card's help in the 24 hours before this attempt. Box movement is not affected.
ALTER TABLE recall_attempts ADD COLUMN with_help INTEGER NOT NULL DEFAULT 0;

PRAGMA user_version = 8;

COMMIT;
