"""Rewards (docs/STUDENT_UI_SPEC.md sections 8 and 11): XP and Adventurer Rank, Star Shards, Daily Commissions,
the Reward Shop and the region map. All numbers and names are in app/rewards_config.py.

Every award is made here, in code, from events the app already logs (marked attempts, card recalls, session
steps). award_pending() looks for events that haven't paid yet and writes one reward_events row for each;
the row's UNIQUE ref means reloading a page never pays twice. No model calls, no randomness.
"""

import json
import logging
from datetime import date, datetime, time, timedelta, timezone

from app import rewards_config as cfg
from app import session_plan
from app.practice import practice_filter, subject_label
from app.session import ACTIVITY_STEPS
from app.session_plan import LOCAL_TZ, STAMP
from app.topics import taught_topics

log = logging.getLogger("uvicorn.error")
RECENT_SESSION_DAYS = 7        # sessions older than this are fully paid already; don't re-read them
MAP_COLUMNS = 6                # waypoints per row of the trail on the map


# ---- Time helpers ------------------------------------------------------------------------------------

def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(STAMP)


def _parse(text: str) -> datetime:
    return datetime.strptime(text, STAMP).replace(tzinfo=timezone.utc)


def day_of(text: str) -> str:
    """The local (Singapore) date of a UTC time stamp, as YYYY-MM-DD."""
    return session_plan.local_day(_parse(text)).isoformat()


def day_bounds(day: date) -> tuple[str, str]:
    """The UTC stamps where a local day starts and the next one starts."""
    start = datetime.combine(day, time(0), LOCAL_TZ)
    return _stamp(start), _stamp(start + timedelta(days=1))


# ---- Small key/value store ---------------------------------------------------------------------------

def get_meta(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM reward_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO reward_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 (key, value))
    conn.commit()


def companion_image(conn) -> str:
    """The companion's picture: the parent's upload once 5b sets it, else the built-in placeholder."""
    return get_meta(conn, "companion_image") or cfg.COMPANION_DEFAULT_IMAGE


# ---- The ledger ----------------------------------------------------------------------------------------

def _paid(conn, ref: str) -> bool:
    return conn.execute("SELECT 1 FROM reward_events WHERE ref = ?", (ref,)).fetchone() is not None


def xp_on_day(conn, day: str) -> int:
    return conn.execute("SELECT COALESCE(SUM(xp), 0) FROM reward_events WHERE day = ?", (day,)).fetchone()[0]


def _pay(conn, ref: str, kind: str, at: str, *, xp: int = 0, shards: int = 0, session_id=None, day: str = "") -> bool:
    """Write one award unless `ref` has paid already. XP is trimmed to what is left under the daily cap (the row is
    still written, so the event is not looked at again). Returns True if a row was written. No commit."""
    if _paid(conn, ref):
        return False
    day = day or day_of(at)
    if xp > 0:
        xp = max(0, min(xp, cfg.DAILY_XP_CAP - xp_on_day(conn, day)))
    conn.execute("INSERT INTO reward_events (ref, kind, xp, shards, day, session_id, at, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (ref, kind, xp, shards, day, session_id, at, _stamp(session_plan.utc_now_dt())))
    return True


def totals(conn) -> dict:
    row = conn.execute("SELECT COALESCE(SUM(xp), 0) AS xp, COALESCE(SUM(shards), 0) AS shards FROM reward_events").fetchone()
    return {"xp": row["xp"], "shards": row["shards"]}


# ---- Turning logged events into awards ---------------------------------------------------------------

def award_pending(conn, now=None) -> bool:
    """Pay every event that hasn't paid yet, then tick off today's (and yesterday's) commissions.
    Called when the home, session and shop pages load (awards are made from the logs, so it doesn't matter that
    she sees them a page later). A failure is logged and never breaks the page."""
    now = now or session_plan.utc_now_dt()
    start = get_meta(conn, "started_at")
    try:
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")   # one writer at a time, so the daily cap can't be overshot
        _award_rewrites(conn, start)
        _award_cards(conn, start)
        _award_sessions(conn, start, now)
        _award_commissions(conn, start, now)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        log.exception("Rewards: award_pending failed")
        return False


def _award_rewrites(conn, start: str) -> None:
    for r in conn.execute(
            """SELECT a.id, a.created_at FROM attempts a
               WHERE a.parent_attempt_id IS NOT NULL AND a.marks_awarded IS NOT NULL AND a.created_at >= ?
                 AND NOT EXISTS (SELECT 1 FROM reward_events e WHERE e.ref = 'rewrite:' || a.id)
               ORDER BY a.id""", (start,)).fetchall():
        _pay(conn, f"rewrite:{r['id']}", "rewrite_done", r["created_at"], xp=cfg.XP["rewrite_done"])


def _award_cards(conn, start: str) -> None:
    for r in conn.execute(
            """SELECT r.id, r.created_at FROM recall_attempts r
               WHERE r.box_after > r.box_before AND r.created_at >= ?
                 AND NOT EXISTS (SELECT 1 FROM reward_events e WHERE e.ref = 'card:' || r.id)
               ORDER BY r.id""", (start,)).fetchall():
        _pay(conn, f"card:{r['id']}", "card_up", r["created_at"], xp=cfg.XP["card_up"])


def check_passed(conn, since: str, question_ids: list) -> bool:
    """A topic check is passed when her first attempts at its questions (in this session) won CHECK_PASS of the marks."""
    if not question_ids:
        return False
    got, mx = conn.execute(
        f"""SELECT COALESCE(SUM(marks_awarded), 0), COALESCE(SUM(max_marks), 0) FROM attempts
            WHERE parent_attempt_id IS NULL AND marks_awarded IS NOT NULL AND created_at >= ?
              AND question_id IN ({','.join('?' * len(question_ids))})""", [since] + list(question_ids)).fetchone()
    return mx > 0 and got >= cfg.CHECK_PASS * mx


def _award_sessions(conn, start: str, now: datetime) -> None:
    oldest = (session_plan.local_day(now) - timedelta(days=RECENT_SESSION_DAYS)).isoformat()
    for row in conn.execute("SELECT * FROM sessions WHERE day >= ? ORDER BY id", (oldest,)).fetchall():
        sid, plan = row["id"], json.loads(row["plan_json"])
        any_done = False
        for b, block in enumerate(plan["blocks"]):
            for st in block["steps"]:
                if st["key"] not in ACTIVITY_STEPS or st["status"] != "done" or not st.get("done_at"):
                    continue
                any_done = True
                if st["done_at"] < start:
                    continue
                _pay(conn, f"step:{sid}:{b}:{st['key']}", "step_done", st["done_at"], xp=cfg.XP["step_done"],
                     session_id=sid)
                ref = f"check:{sid}:{b}"
                # (a check the time-out cut short is not "passed": she didn't answer all of it)
                if (st["key"] == "check" and not st.get("cut") and not _paid(conn, ref)
                        and check_passed(conn, row["started_at"], st["items"])):
                    _pay(conn, ref, "check_passed", st["done_at"], xp=cfg.XP["check_passed"], session_id=sid)
        ended = row["ended_at"]
        if ended and ended >= start and any_done and not _paid(conn, f"session:{sid}"):
            paid_today = conn.execute("SELECT COUNT(*) FROM reward_events WHERE kind = 'session_done' AND day = ? "
                                      "AND shards > 0", (day_of(ended),)).fetchone()[0]
            shards = cfg.SHARDS_PER_SESSION if paid_today < cfg.SESSIONS_WITH_SHARDS_PER_DAY else 0
            _pay(conn, f"session:{sid}", "session_done", ended, xp=cfg.XP["session_done"], shards=shards,
                 session_id=sid)


# ---- Daily Commissions ---------------------------------------------------------------------------------

def _feasible(conn, key: str) -> bool:
    """A commission is only offered if it can be done: a topic check needs approved generated questions."""
    if key == "pass_check":
        return bool(session_plan.question_pool(conn, generated=True))
    return True


def choose_commissions(conn, day: date) -> list[str]:
    """The day's three: "finish_session" every day, then the rest of the pool in turn by date."""
    others = [k for k in cfg.COMMISSION_POOL if k != cfg.ALWAYS_OFFERED and _feasible(conn, k)]
    first = day.toordinal() % len(others) if others else 0
    count = min(cfg.COMMISSIONS_PER_DAY - 1, len(others))
    return [cfg.ALWAYS_OFFERED] + [others[(first + i) % len(others)] for i in range(count)]


def _day_keys(conn, day: date, create: bool) -> list[str]:
    keys = [r["key"] for r in conn.execute("SELECT key FROM commissions WHERE day = ? ORDER BY position",
                                           (day.isoformat(),))]
    if keys or not create:
        return keys
    keys = choose_commissions(conn, day)
    for i, key in enumerate(keys):
        conn.execute("INSERT OR IGNORE INTO commissions (day, key, position) VALUES (?, ?, ?)", (day.isoformat(), key, i))
    return keys


def _progress(conn, key: str, lo: str, hi: str) -> list[str]:
    """Time stamps of the events that count towards a commission between lo and hi, oldest first."""
    if key == "practise":
        sql = ("SELECT created_at FROM attempts WHERE parent_attempt_id IS NULL AND marks_awarded IS NOT NULL "
               "AND created_at >= ? AND created_at < ? ORDER BY created_at")
        return [r[0] for r in conn.execute(sql, (lo, hi))]
    kind = {"finish_session": "session_done", "one_rewrite": "rewrite_done",
            "pass_check": "check_passed", "cards_up": "card_up"}[key]
    return [r[0] for r in conn.execute(
        "SELECT at FROM reward_events WHERE kind = ? AND at >= ? AND at < ? ORDER BY at", (kind, lo, hi))]


def _window(day: date, start: str) -> tuple[str, str]:
    lo, hi = day_bounds(day)
    return max(lo, start), hi


def _award_commissions(conn, start: str, now: datetime) -> None:
    today = session_plan.local_day(now)
    for day in (today - timedelta(days=1), today):
        keys = _day_keys(conn, day, create=day == today)
        lo, hi = _window(day, start)
        done_at = []
        for key in keys:
            need = cfg.COMMISSION_POOL[key]["need"]
            events = _progress(conn, key, lo, hi)
            if len(events) < need:
                continue
            at = events[need - 1]      # the moment it was completed
            done_at.append(at)
            conn.execute("UPDATE commissions SET done_at = ? WHERE day = ? AND key = ? AND done_at IS NULL",
                         (at, day.isoformat(), key))
            _pay(conn, f"commission:{day.isoformat()}:{key}", "commission", at, shards=cfg.SHARDS_PER_COMMISSION,
                 day=day.isoformat())
        if keys and len(done_at) == len(keys):
            _pay(conn, f"commission_bonus:{day.isoformat()}", "commission_bonus", max(done_at),
                 shards=cfg.COMMISSIONS_BONUS, day=day.isoformat())


def commissions_today(conn, now=None) -> dict:
    """Today's three commissions with her progress, for the home screen."""
    now = now or session_plan.utc_now_dt()
    today = session_plan.local_day(now)
    keys = _day_keys(conn, today, create=True)
    conn.commit()
    lo, hi = _window(today, get_meta(conn, "started_at"))
    items = []
    for key in keys:
        spec = cfg.COMMISSION_POOL[key]
        have = len(_progress(conn, key, lo, hi))
        items.append({"key": key, "label": spec["label"], "need": spec["need"], "have": min(have, spec["need"]),
                      "done": have >= spec["need"]})
    all_done = bool(items) and all(i["done"] for i in items)
    return {"label": cfg.COMMISSIONS_LABEL, "shards_label": cfg.SHARDS_LABEL, "items": items, "all_done": all_done,
            "per_item": cfg.SHARDS_PER_COMMISSION, "bonus": cfg.COMMISSIONS_BONUS}


# ---- Rank ----------------------------------------------------------------------------------------------

def rank_for(xp: int) -> dict:
    """Rank number (1-based), title, and progress to the next rank."""
    i = max(k for k, (floor, _) in enumerate(cfg.RANKS) if xp >= floor)
    floor, title = cfg.RANKS[i]
    top = i == len(cfg.RANKS) - 1
    nxt = None if top else cfg.RANKS[i + 1][0]
    pct = 100 if top else round(100 * (xp - floor) / (nxt - floor))
    return {"rank": i + 1, "title": title, "xp": xp, "floor": floor, "next": nxt,
            "to_next": 0 if top else nxt - xp, "pct": pct, "top": top}


def rank_info(conn) -> dict:
    """Everything the rank bar shows."""
    t = totals(conn)
    badge = item(equipped(conn).get("badge", ""))
    return {**rank_for(t["xp"]), "shards": t["shards"], "label": cfg.RANK_LABEL, "xp_label": cfg.XP_LABEL,
            "shards_label": cfg.SHARDS_LABEL, "badge": badge["title"] if badge else ""}


def session_earnings(conn, s: dict, now=None) -> dict:
    """XP and Shards earned during a session (its steps, plus rewrites, cards and commissions done while it ran),
    and whether she ranked up. For the wrap-up screen."""
    now = now or session_plan.utc_now_dt()
    hi = s["ended_at"] or _stamp(now)
    row = conn.execute(
        """SELECT COALESCE(SUM(xp), 0) AS xp, COALESCE(SUM(shards), 0) AS shards FROM reward_events
           WHERE kind NOT IN ('purchase', 'hold', 'release') AND (session_id = ? OR (session_id IS NULL AND at >= ? AND at <= ?))""",
        (s["id"], s["started_at"], hi)).fetchone()
    total = totals(conn)["xp"]
    before, after = rank_for(total - row["xp"]), rank_for(total)
    new_items = [i for i in cfg.SHOP if before["rank"] < i["min_rank"] <= after["rank"]]
    return {"xp": row["xp"], "shards": row["shards"], "ranked_up": after["rank"] > before["rank"],
            "rank": after, "new_items": new_items, "xp_label": cfg.XP_LABEL, "shards_label": cfg.SHARDS_LABEL,
            "rank_label": cfg.RANK_LABEL, "session_xp": cfg.XP["session_done"], "session_shards": cfg.SHARDS_PER_SESSION}


# ---- Reward Shop ---------------------------------------------------------------------------------------

def item(item_id: str):
    return next((i for i in cfg.SHOP if i["id"] == item_id), None)


def owned(conn) -> set:
    return {r["item"] for r in conn.execute("SELECT item FROM reward_unlocks")}


def equipped(conn) -> dict:
    """{slot: item id} for what she has equipped."""
    return {r["slot"]: r["item"] for r in conn.execute("SELECT slot, item FROM reward_equipped")}


def shop_items(conn) -> list[dict]:
    have, on, info = owned(conn), equipped(conn), rank_info(conn)
    items = []
    for i in cfg.SHOP:
        state = ("equipped" if on.get(i["slot"]) == i["id"] else "owned" if i["id"] in have
                 else "locked" if info["rank"] < i["min_rank"] else "buy" if info["shards"] >= i["cost"] else "short")
        items.append({**i, "state": state, "short_by": max(0, i["cost"] - info["shards"])})
    return items


def buy(conn, item_id: str, now=None) -> str:
    """Spend Shards on a cosmetic. Returns "bought", "unknown", "owned", "locked" or "short" (not enough Shards).
    Checked here on the server; the page's buttons are only a convenience."""
    now = now or session_plan.utc_now_dt()
    it = item(item_id)
    if it is None:
        return "unknown"
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")    # two quick taps can't both spend the same Shards
    try:
        if item_id in owned(conn):
            result = "owned"
        elif rank_for(totals(conn)["xp"])["rank"] < it["min_rank"]:
            result = "locked"
        elif totals(conn)["shards"] < it["cost"]:
            result = "short"
        else:
            at = _stamp(now)
            conn.execute("INSERT INTO reward_unlocks (item, cost, bought_at) VALUES (?, ?, ?)", (item_id, it["cost"], at))
            _pay(conn, f"buy:{item_id}", "purchase", at, shards=-it["cost"])
            result = "bought"
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def equip(conn, item_id: str) -> bool:
    """Wear an item she owns (replacing whatever was in its slot)."""
    it = item(item_id)
    if it is None or item_id not in owned(conn):
        return False
    conn.execute("INSERT INTO reward_equipped (slot, item, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT (slot) DO UPDATE SET item = excluded.item, updated_at = excluded.updated_at",
                 (it["slot"], item_id, _stamp(session_plan.utc_now_dt())))
    conn.commit()
    return True


def unequip(conn, slot: str) -> None:
    conn.execute("DELETE FROM reward_equipped WHERE slot = ?", (slot,))
    conn.commit()


UPLOAD_PREFIX = "upload:"      # an uploaded scene is equipped as "upload:<image id>"


def equip_upload_scene(conn, image_id: int) -> bool:
    """Use one of the parent's uploaded scenes as the background (they are free: the parent chose them)."""
    if conn.execute("SELECT 1 FROM uploaded_images WHERE id = ? AND kind = 'scene'", (image_id,)).fetchone() is None:
        return False
    conn.execute("INSERT INTO reward_equipped (slot, item, updated_at) VALUES ('scene', ?, ?) "
                 "ON CONFLICT (slot) DO UPDATE SET item = excluded.item, updated_at = excluded.updated_at",
                 (f"{UPLOAD_PREFIX}{image_id}", _stamp(session_plan.utc_now_dt())))
    conn.commit()
    return True


def uploaded_scenes(conn) -> list[dict]:
    """The parent's uploaded scenes with the one in use marked: {"id", "label", "url", "equipped"}."""
    current = equipped(conn).get("scene", "")
    return [{"id": r["id"], "label": r["label"] or f"Scene {n}", "url": f"/uploads/{r['filename']}",
             "equipped": current == f"{UPLOAD_PREFIX}{r['id']}"}
            for n, r in enumerate(conn.execute("SELECT * FROM uploaded_images WHERE kind = 'scene' ORDER BY id"), 1)]


def cosmetics(conn) -> dict:
    """What every page needs for the theme: equipped frame and scene (CSS hooks) and the companion picture.
    An uploaded scene shows as scene "custom" plus its picture's address in scene_image."""
    on = equipped(conn)
    scene, scene_image = on.get("scene", ""), ""
    if scene.startswith(UPLOAD_PREFIX):
        row = conn.execute("SELECT filename FROM uploaded_images WHERE id = ? AND kind = 'scene'",
                           (scene[len(UPLOAD_PREFIX):] if scene[len(UPLOAD_PREFIX):].isdigit() else 0,)).fetchone()
        scene, scene_image = ("custom", f"/uploads/{row['filename']}") if row else ("", "")
    return {"frame": on.get("frame", ""), "scene": scene, "scene_image": scene_image,
            "companion": companion_image(conn), "companion_name": cfg.COMPANION_NAME}


# ---- Real-world rewards (the parent defines them; the ledger holds the Shards) ---------------------------
# Asking writes a `hold:<id>` ledger row (negative Shards), so the balance she can spend drops at once and a
# second request cannot spend the same Shards. Decline writes `release:<id>` (the Shards come back). Approve
# writes nothing more: the hold simply stays as the spend. The balance is always the sum of the ledger.

REAL_NAME_MAX, REAL_NOTE_MAX, REAL_COST_MAX = 60, 80, 10000


def real_items(conn, include_retired: bool = False) -> list[dict]:
    sql = "SELECT * FROM reward_items" + ("" if include_retired else " WHERE retired = 0") + " ORDER BY retired, cost, id"
    return [dict(r) for r in conn.execute(sql)]


def check_real_item(name: str, cost, note: str) -> tuple[dict, list[str]]:
    """(cleaned values, problems) for the add and edit forms."""
    name, note, problems = " ".join(str(name).split()), " ".join(str(note).split()), []
    text = str(cost).strip()
    if not name or len(name) > REAL_NAME_MAX:
        problems.append(f"The reward needs a name of up to {REAL_NAME_MAX} characters.")
    if len(note) > REAL_NOTE_MAX:
        problems.append(f"The note can be up to {REAL_NOTE_MAX} characters.")
    if not text.isdigit() or not 1 <= int(text) <= REAL_COST_MAX:
        problems.append(f"The cost must be a whole number of {cfg.SHARDS_LABEL} from 1 to {REAL_COST_MAX}.")
    return {"name": name, "cost": int(text) if text.isdigit() else 0, "note": note}, problems


def add_real_item(conn, name: str, cost, note: str = "") -> list[str]:
    values, problems = check_real_item(name, cost, note)
    if not problems:
        conn.execute("INSERT INTO reward_items (name, cost, note, created_at) VALUES (?, ?, ?, ?)",
                     (values["name"], values["cost"], values["note"], _stamp(session_plan.utc_now_dt())))
        conn.commit()
    return problems


def edit_real_item(conn, item_id: int, name: str, cost, note: str = "") -> list[str]:
    """Change a reward. Requests already made keep the name and cost they were made with."""
    values, problems = check_real_item(name, cost, note)
    if not problems:
        conn.execute("UPDATE reward_items SET name = ?, cost = ?, note = ? WHERE id = ?",
                     (values["name"], values["cost"], values["note"], item_id))
        conn.commit()
    return problems


def set_real_retired(conn, item_id: int, retired: bool) -> None:
    conn.execute("UPDATE reward_items SET retired = ? WHERE id = ?", (1 if retired else 0, item_id))
    conn.commit()


def available_shards(conn) -> int:
    """Shards she can spend: the ledger total, which already has pending requests taken off."""
    return totals(conn)["shards"]


def held_shards(conn) -> int:
    return conn.execute("SELECT COALESCE(SUM(cost), 0) FROM reward_requests WHERE status = 'pending'").fetchone()[0]


def pending_requests(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM reward_requests WHERE status = 'pending' ORDER BY id")]


def pending_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM reward_requests WHERE status = 'pending'").fetchone()[0]


def recent_decided(conn, limit: int = 10) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM reward_requests WHERE status != 'pending' ORDER BY decided_at DESC, id DESC LIMIT ?", (limit,))]


def real_shop(conn) -> list[dict]:
    """The reward list for her shop with what she can do about each: state "ask" or "short"."""
    have = available_shards(conn)
    return [{**i, "state": "ask" if have >= i["cost"] else "short", "short_by": max(0, i["cost"] - have)}
            for i in real_items(conn)]


def request_real(conn, item_id, now=None) -> str:
    """She asks for a real-world reward. Returns "requested", "unknown", "retired" or "short"."""
    now = now or session_plan.utc_now_dt()
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")      # two quick taps can't hold the same Shards twice
    try:
        row = conn.execute("SELECT * FROM reward_items WHERE id = ?", (item_id,)).fetchone() if str(item_id).isdigit() else None
        if row is None:
            result = "unknown"
        elif row["retired"]:
            result = "retired"
        elif available_shards(conn) < row["cost"]:
            result = "short"
        else:
            at = _stamp(now)
            cur = conn.execute("INSERT INTO reward_requests (item_id, name, cost, note, requested_at) VALUES (?, ?, ?, ?, ?)",
                               (row["id"], row["name"], row["cost"], row["note"], at))
            _pay(conn, f"hold:{cur.lastrowid}", "hold", at, shards=-row["cost"])
            result = "requested"
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def decide_request(conn, request_id, approve: bool, now=None) -> str:
    """The parent approves (the held Shards stay spent) or declines (they are released). Returns "approved",
    "declined" or "gone" (no such request, or it was decided already)."""
    now = now or session_plan.utc_now_dt()
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM reward_requests WHERE id = ? AND status = 'pending'",
                           (request_id,)).fetchone() if str(request_id).isdigit() else None
        if row is None:
            result = "gone"
        else:
            at = _stamp(now)
            conn.execute("UPDATE reward_requests SET status = ?, decided_at = ? WHERE id = ?",
                         ("approved" if approve else "declined", at, row["id"]))
            if not approve:
                _pay(conn, f"release:{row['id']}", "release", at, shards=row["cost"])
            result = "approved" if approve else "declined"
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


# ---- Region map -----------------------------------------------------------------------------------------

STATES = ("not_started", "learning", "secure")
STATE_LABELS = {"not_started": "Not started", "learning": "Learning", "secure": "Secure"}


def topic_state(*, active: bool, has_questions: bool, good_questions: int, has_cards: bool, avg_box: float) -> str:
    """not_started: nothing done yet. secure: the question test (if it has questions) and the card test (if it has
    cards) both hold. Otherwise learning."""
    if not active:
        return "not_started"
    if not (has_questions or has_cards):
        return "learning"
    q_ok = not has_questions or good_questions >= cfg.SECURE_MIN_QUESTIONS
    c_ok = not has_cards or avg_box >= cfg.SECURE_CARD_BOX
    return "secure" if q_ok and c_ok else "learning"


def topic_states(conn) -> dict:
    """{topic: state} for every topic with questions, cards or activity. Every level counts, whatever the study
    level: switching level must never change a topic's state or any reward."""
    has_q = {r[0] for r in conn.execute(
        f"SELECT DISTINCT q.topic FROM questions q WHERE {practice_filter(conn, 'all')}")}
    tried = {r[0] for r in conn.execute(
        "SELECT DISTINCT q.topic FROM attempts a JOIN questions q ON q.id = a.question_id "
        "WHERE a.parent_attempt_id IS NULL AND a.marks_awarded IS NOT NULL")}
    good = {r[0]: r[1] for r in conn.execute(
        """SELECT q.topic, COUNT(DISTINCT a.question_id) FROM attempts a JOIN questions q ON q.id = a.question_id
           WHERE a.parent_attempt_id IS NULL AND a.marks_awarded IS NOT NULL AND a.max_marks > 0
             AND a.marks_awarded >= ? * a.max_marks GROUP BY q.topic""", (cfg.SECURE_SCORE,))}
    boxes = {r[0]: r[1] for r in conn.execute(
        "SELECT c.topic, AVG(COALESCE(p.box, 1)) FROM revision_cards c "
        "LEFT JOIN card_progress p ON p.card_id = c.id GROUP BY c.topic")}
    recalled = {r[0] for r in conn.execute(
        "SELECT DISTINCT c.topic FROM recall_attempts r JOIN revision_cards c ON c.id = r.card_id")}
    return {t: topic_state(active=t in tried or t in recalled, has_questions=t in has_q,
                           good_questions=good.get(t, 0), has_cards=t in boxes, avg_box=boxes.get(t, 1.0))
            for t in has_q | tried | set(boxes) | recalled}


def trail(n: int) -> dict:
    """Positions for n waypoints on a winding trail (rows of MAP_COLUMNS, alternate rows run right to left),
    in a 100-wide SVG box. Returns {"points": [(x, y)], "path": "M ... L ...", "height": h}."""
    pts = []
    for i in range(n):
        row, col = divmod(i, MAP_COLUMNS)
        if row % 2:
            col = MAP_COLUMNS - 1 - col
        pts.append((10 + col * 16, 10 + row * 16))
    path = " ".join(("M" if i == 0 else "L") + f" {x} {y}" for i, (x, y) in enumerate(pts))
    rows = (n + MAP_COLUMNS - 1) // MAP_COLUMNS
    return {"points": pts, "path": path, "height": max(20, rows * 16 + 4)}


def map_view(conn) -> list[dict]:
    """One region per subject that has taught topics; each taught topic is a waypoint with its state."""
    taught, states = taught_topics(conn), topic_states(conn)
    regions = []
    for subject in session_plan.CHOICES:
        topics = [dict(r) for r in conn.execute("SELECT id, title FROM topics WHERE subject = ? ORDER BY sort_order",
                                                (subject,)) if r["id"] in taught]
        if not topics:
            continue
        layout = trail(len(topics))
        waypoints = [{"topic": t["id"], "title": t["title"], "n": k + 1,
                      "state": states.get(t["id"], "not_started"),
                      "state_label": STATE_LABELS[states.get(t["id"], "not_started")],
                      "x": layout["points"][k][0], "y": layout["points"][k][1]} for k, t in enumerate(topics)]
        counts = {s: sum(w["state"] == s for w in waypoints) for s in STATES}
        regions.append({"subject": subject, "label": subject_label(subject), "waypoints": waypoints,
                        "path": layout["path"], "height": layout["height"], "counts": counts})
    return regions
