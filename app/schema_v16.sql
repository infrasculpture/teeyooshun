-- Teeyooshun database schema, version 16 (parent settings, task 5b: real-world rewards and uploaded images).
-- Applied by app/db.py to a version 15 database, in one transaction.

BEGIN;

-- Real-world rewards the parent defines ("30 min game time"). `retired` hides an item from the shop without
-- losing the history of requests made for it.
CREATE TABLE reward_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    cost       INTEGER NOT NULL CHECK (cost > 0),
    note       TEXT NOT NULL DEFAULT '',
    retired    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- A request for a real-world reward. The name and cost are copied here when she asks, so editing or retiring
-- the item later never changes what was held. The Shards themselves live only in the ledger (reward_events):
-- asking writes a `hold:<id>` row with negative shards, declining writes a `release:<id>` row that gives them
-- back, and approving writes nothing more (the hold is the spend). This table holds the status, not a balance.
CREATE TABLE reward_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id      INTEGER NOT NULL REFERENCES reward_items (id),
    name         TEXT NOT NULL,
    cost         INTEGER NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'declined')),
    requested_at TEXT NOT NULL,
    decided_at   TEXT
);
CREATE INDEX idx_reward_requests_status ON reward_requests (status);

-- Pictures the parent uploaded (the files are re-encoded and stored under <data dir>/uploads/, never in git).
CREATE TABLE uploaded_images (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL CHECK (kind IN ('companion', 'scene')),
    filename   TEXT NOT NULL UNIQUE,
    label      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

PRAGMA user_version = 16;

COMMIT;
