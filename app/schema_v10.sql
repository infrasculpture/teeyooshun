-- Teeyooshun database schema, version 10 (student UI task 1: session planner and home screen).
-- Applied by app/db.py to a version 9 database, in one transaction.

BEGIN;

-- One study session ("conveyor belt"): a daily session or a year-end review.
-- plan_json holds the blocks and steps (what, in what order, done / skipped, time stamps per step).
CREATE TABLE IF NOT EXISTS sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL DEFAULT 'daily' CHECK (kind IN ('daily', 'year_end')),
    day            TEXT NOT NULL,                 -- local date (Singapore) the session started
    subject        TEXT NOT NULL DEFAULT '',
    started_at     TEXT NOT NULL,
    ended_at       TEXT,
    length_planned INTEGER NOT NULL,              -- minutes
    block          INTEGER NOT NULL DEFAULT 0,    -- the block she is in (0-based); a block is up to 60 minutes
    block_count    INTEGER NOT NULL DEFAULT 1,
    plan_json      TEXT NOT NULL,
    focus_topic    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_sessions_day ON sessions (day);

-- A revise set that belongs to a session step (NULL for sets started from the Revise page).
ALTER TABLE revise_sets ADD COLUMN session_id INTEGER REFERENCES sessions (id);

-- The topics that count as taught, for the planner, "Choose my own" and Revise alike.
--   1. An explicit topic_status row always wins: 'taught' includes the topic, 'not_taught' excludes it.
--   2. With no row, a topic is taught if its level is at or below app_settings.current_level.
--      Its level is the lowest level of its questions and approved revision cards; only if it has
--      none is topics.likely_level used. A topic whose level can't be worked out counts as taught.
CREATE VIEW IF NOT EXISTS taught_topics AS
WITH level_rank (lvl, n) AS (VALUES ('sec1', 1), ('sec2', 2), ('sec3', 3), ('sec4', 4)),
cur (n) AS (
    SELECT COALESCE((SELECT r.n FROM level_rank r
                     WHERE r.lvl = (SELECT json_extract(value, '$') FROM app_settings WHERE key = 'current_level')), 1)
),
content (topic, n) AS (
    SELECT u.topic, MIN(r.n)
    FROM (SELECT topic, level FROM questions UNION ALL SELECT topic, level FROM revision_cards) u
    LEFT JOIN level_rank r ON r.lvl = lower(u.level)
    GROUP BY u.topic
),
all_topics (topic) AS (
    SELECT topic FROM content UNION SELECT id FROM topics UNION SELECT topic FROM topic_status
)
SELECT a.topic
FROM all_topics a
LEFT JOIN content c ON c.topic = a.topic
LEFT JOIN topics t ON t.id = a.topic
LEFT JOIN level_rank tr ON tr.lvl = lower(t.likely_level)
LEFT JOIN topic_status s ON s.topic = a.topic
WHERE CASE WHEN s.status IS NOT NULL THEN s.status = 'taught'
           ELSE COALESCE(c.n, tr.n, 0) <= (SELECT n FROM cur) END;

PRAGMA user_version = 10;

COMMIT;
