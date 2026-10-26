-- Schema v19: study level (Sec 1 / Sec 2 / both) replaces "her current level".
-- Applied by app/db.py to a version 18 database, in one transaction. Views and one new table only;
-- the only existing row touched is the saved current_level setting. Back up the database first anyway.
BEGIN;

-- 1. Which levels each topic belongs to (one row per level; 'both' matches any choice).
--    A topic's levels are those of its questions and revision cards (sec1..sec4). Only if it has none
--    is topics.likely_level used. A topic whose level can't be worked out gets 'both'.
DROP VIEW IF EXISTS taught_topics;
DROP VIEW IF EXISTS topic_levels;

CREATE VIEW topic_levels AS
WITH content (topic, lvl) AS (
    SELECT topic, lower(level) FROM questions WHERE lower(level) IN ('sec1', 'sec2', 'sec3', 'sec4')
    UNION
    SELECT topic, lower(level) FROM revision_cards WHERE lower(level) IN ('sec1', 'sec2', 'sec3', 'sec4')
),
all_topics (topic) AS (
    SELECT topic FROM content UNION SELECT id FROM topics UNION SELECT topic FROM topic_status
    UNION SELECT topic FROM questions UNION SELECT topic FROM revision_cards
)
SELECT c.topic, c.lvl FROM content c
UNION ALL
SELECT a.topic,
       CASE WHEN lower(t.likely_level) IN ('sec1', 'sec2', 'sec3', 'sec4') THEN lower(t.likely_level) ELSE 'both' END
FROM all_topics a LEFT JOIN topics t ON t.id = a.topic
WHERE a.topic NOT IN (SELECT topic FROM content);

-- 2. The topics that count as taught: an explicit topic_status row wins ('taught' includes, 'not_taught'
--    excludes); with no row a topic is taught. Which levels are shown is a separate filter (topic_levels).
CREATE VIEW taught_topics AS
SELECT l.topic
FROM (SELECT DISTINCT topic FROM topic_levels) l
LEFT JOIN topic_status s ON s.topic = l.topic
WHERE s.status IS NULL OR s.status = 'taught';

-- 3. The saved setting: sec1 and sec2 stay; anything else (sec3, sec4) becomes sec2. 'both' is also allowed.
UPDATE app_settings SET value = '"sec2"'
WHERE key = 'current_level' AND value NOT IN ('"sec1"', '"sec2"', '"both"');

-- 4. The student's Sec 1 / Sec 2 / Both switches in Revise and Practice, for the parent's session log.
CREATE TABLE IF NOT EXISTS mode_level_log (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    at    TEXT NOT NULL,
    mode  TEXT NOT NULL CHECK (mode IN ('revise', 'practice')),
    level TEXT NOT NULL CHECK (level IN ('sec1', 'sec2', 'both'))
);

PRAGMA user_version = 19;

COMMIT;
