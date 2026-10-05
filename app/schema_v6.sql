-- Teeyooshun database schema, version 6 (Task 4 Stage B: revision cards).
-- Applied by app/db.py to a version 5 database, in one transaction.

BEGIN;

-- Approved revision cards (one per revision-table row). record_json holds the full card
-- in the shape of docs/card_format_example.json. Revise mode (Task 5) reads from here.
CREATE TABLE IF NOT EXISTS revision_cards (
    id           TEXT PRIMARY KEY,
    subject      TEXT NOT NULL,
    level        TEXT NOT NULL,
    topic        TEXT NOT NULL,
    section      TEXT NOT NULL DEFAULT '',
    format       TEXT NOT NULL,
    recall       TEXT NOT NULL,
    record_json  TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    imported_at  TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cards_tags ON revision_cards (subject, level, topic);

-- Every version of a card, so recall records can point at the version she saw.
CREATE TABLE IF NOT EXISTS revision_card_versions (
    card_id      TEXT NOT NULL REFERENCES revision_cards (id),
    version      INTEGER NOT NULL,
    record_json  TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (card_id, version)
);

PRAGMA user_version = 6;

COMMIT;
