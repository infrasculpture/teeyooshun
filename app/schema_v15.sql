-- Teeyooshun database schema, version 15 (rewards: STUDENT_UI_SPEC.md sections 8 and 11).
-- Applied by app/db.py to a version 14 database, in one transaction.

BEGIN;

-- The rewards ledger. Every XP or Star Shard award is one row, written by code (app/rewards.py) from logged
-- events; nothing here comes from a model call. XP, rank and the Shards balance are sums over this table, so
-- there is no running counter to drift. `ref` names the event that earned it (e.g. "rewrite:42"), and UNIQUE
-- means the same event can never pay twice, however often a page is reloaded. A purchase is a negative
-- `shards` row.
CREATE TABLE reward_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ref        TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL,           -- step_done / rewrite_done / card_up / check_passed / session_done /
                                        -- commission / commission_bonus / purchase
    xp         INTEGER NOT NULL DEFAULT 0,
    shards     INTEGER NOT NULL DEFAULT 0,
    day        TEXT NOT NULL,           -- local (Singapore) date of the event; the daily XP cap counts by it
    session_id INTEGER REFERENCES sessions (id),
    at         TEXT NOT NULL,           -- when the event happened (UTC)
    created_at TEXT NOT NULL            -- when the award was written (UTC)
);
CREATE INDEX idx_reward_events_day ON reward_events (day);
CREATE INDEX idx_reward_events_at ON reward_events (at);

-- Cosmetics she has bought in the Reward Shop (one row per item; items are defined in app/rewards_config.py).
CREATE TABLE reward_unlocks (
    item      TEXT PRIMARY KEY,
    cost      INTEGER NOT NULL,
    bought_at TEXT NOT NULL
);

-- What she has equipped: one item per slot (frame / badge / scene).
CREATE TABLE reward_equipped (
    slot       TEXT PRIMARY KEY,
    item       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Daily Commissions: the three chosen for each local day (fixed once chosen) and when each was completed.
CREATE TABLE commissions (
    day      TEXT NOT NULL,
    key      TEXT NOT NULL,
    position INTEGER NOT NULL,
    done_at  TEXT,
    PRIMARY KEY (day, key)
);

-- Small key/value store for rewards: `started_at` (effort before rewards were switched on doesn't count, so
-- old test answers on the server don't pay out) and `companion_image` (blank = the built-in placeholder;
-- the parent upload in task 5b sets it).
CREATE TABLE reward_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO reward_meta (key, value) VALUES ('started_at', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));

PRAGMA user_version = 15;

COMMIT;
