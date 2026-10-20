"""Every number and name used by the rewards (docs/STUDENT_UI_SPEC.md section 8), in one place.

Change a value here and restart the container; nothing else needs editing. The rules that use these
live in app/rewards.py. No randomness anywhere: the same effort always earns the same reward.
"""

# ---- Names shown on screen (rename here) ---------------------------------------------------------------
RANK_LABEL = "Adventurer Rank"
XP_LABEL = "XP"
SHARDS_LABEL = "Star Shards"
COMMISSIONS_LABEL = "Daily Commissions"
COMPANION_NAME = "Pip"
COMPANION_DEFAULT_IMAGE = "/static/companion.svg"   # original placeholder art; a parent upload (5b) replaces it

# ---- XP: effort, not just marks ---------------------------------------------------------------------
XP = {
    "step_done": 20,       # a session step finished (warm-up, learn, quick-fire, practise, topic check, review)
    "rewrite_done": 15,    # a rewrite submitted and marked
    "card_up": 5,          # a revision card moved up a box
    "check_passed": 40,    # a topic check finished with at least CHECK_PASS of the marks
    "session_done": 50,    # a session finished (with at least one step done)
}
CHECK_PASS = 0.6

# The daily XP cap is about two full 60-minute sessions' worth, so a long weekend day doesn't hit it.
# A typical 60-minute session: 5 steps, about 17 cards moved up (warm-up 8, learn 4, quick-fire 5),
# 4 rewrites, 1 topic check passed, 1 session finished = 100 + 85 + 60 + 40 + 50 = 335 XP.
TYPICAL_60_MIN = {"step_done": 5, "card_up": 17, "rewrite_done": 4, "check_passed": 1, "session_done": 1}
DAILY_XP_CAP = 2 * sum(XP[k] * n for k, n in TYPICAL_60_MIN.items())   # 670

# ---- Ranks: (XP needed, title). Rank 1 is the first row. --------------------------------------------
RANKS = [
    (0, "Wanderer"),
    (150, "Trail Walker"),
    (350, "Path Seeker"),
    (600, "Lantern Bearer"),
    (900, "Map Reader"),
    (1250, "Ridge Climber"),
    (1650, "River Crosser"),
    (2100, "Grove Keeper"),
    (2600, "Cliff Scaler"),
    (3200, "Star Reader"),
    (3900, "Storm Walker"),
    (4700, "Lore Seeker"),
    (5600, "Peak Climber"),
    (6600, "Sky Sailor"),
    (7700, "Waypoint Warden"),
    (8900, "Realm Ranger"),
    (10200, "Sage Scout"),
    (11600, "Star Navigator"),
    (13100, "Grand Explorer"),
    (14700, "Legend of the Realm"),
]

# ---- Star Shards ------------------------------------------------------------------------------------
SHARDS_PER_SESSION = 10
SESSIONS_WITH_SHARDS_PER_DAY = 2   # more sessions in a day still earn XP, but not more Shards
SHARDS_PER_COMMISSION = 5
COMMISSIONS_BONUS = 15             # all three done in one day
COMMISSIONS_PER_DAY = 3

# Daily Commissions: a small fixed pool. "finish_session" is offered every day; the other two rotate through
# the rest by date. `need` is how many of the thing count as done.
COMMISSION_POOL = {
    "finish_session": {"label": "Finish today's session", "need": 1},
    "one_rewrite": {"label": "Do one rewrite", "need": 1},
    "pass_check": {"label": "Pass a topic check", "need": 1},
    "cards_up": {"label": "Move 3 cards up a box", "need": 3},
    "practise": {"label": "Answer 3 questions", "need": 3},
}
ALWAYS_OFFERED = "finish_session"

# ---- Region map: when a taught topic counts as "secure" ------------------------------------------------
# Questions: at least SECURE_MIN_QUESTIONS different questions answered at SECURE_SCORE or better on the
# first attempt. Cards: the topic's cards average box SECURE_CARD_BOX or higher (unseen cards count as box 1).
# A topic with questions must pass the question test; a topic with cards must pass the card test; both if both.
SECURE_MIN_QUESTIONS = 3
SECURE_SCORE = 0.75
SECURE_CARD_BOX = 3

# ---- Reward Shop: cosmetic unlocks. min_rank = the rank that makes the item available. ---------------
# slot "frame": panel border style; "badge": a title shown beside the rank; "scene": the page background.
# Each id has matching CSS in app/static/theme.css (body[data-frame=...], [data-scene=...]).
SHOP = [
    {"id": "frame-oak", "slot": "frame", "name": "Oakwood frame", "cost": 30, "min_rank": 1,
     "about": "Warm wooden edges on every panel."},
    {"id": "frame-gilded", "slot": "frame", "name": "Gilded frame", "cost": 80, "min_rank": 3,
     "about": "A double gold border."},
    {"id": "frame-starlit", "slot": "frame", "name": "Starlit frame", "cost": 150, "min_rank": 6,
     "about": "Gold with a soft glow in your element colour."},
    {"id": "badge-pathfinder", "slot": "badge", "name": "Title: Pathfinder", "cost": 40, "min_rank": 2,
     "title": "Pathfinder", "about": "Shown beside your rank."},
    {"id": "badge-lorekeeper", "slot": "badge", "name": "Title: Lorekeeper", "cost": 100, "min_rank": 5,
     "title": "Lorekeeper", "about": "Shown beside your rank."},
    {"id": "badge-starseeker", "slot": "badge", "name": "Title: Starseeker", "cost": 200, "min_rank": 8,
     "title": "Starseeker", "about": "Shown beside your rank."},
    {"id": "scene-meadow", "slot": "scene", "name": "Dawn meadow", "cost": 60, "min_rank": 2,
     "about": "A soft green-gold morning behind the panels."},
    {"id": "scene-harbour", "slot": "scene", "name": "Dusk harbour", "cost": 120, "min_rank": 4,
     "about": "Evening blues and a warm horizon."},
    {"id": "scene-night", "slot": "scene", "name": "Starry night", "cost": 220, "min_rank": 7,
     "about": "A deep night sky (the panels stay light and easy to read)."},
]
SLOTS = ("frame", "badge", "scene")
