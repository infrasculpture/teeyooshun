"""A running session (docs/STUDENT_UI_SPEC.md sections 3 and 11): create, resume, move through the steps,
skip, swap the focus topic, break between 60-minute blocks, and the wrap-up summary.

State lives in the sessions table; plan_json holds the blocks and each step's status and time stamps.
A step is finished by what she does (every card recalled, every question answered), not by a timer.
"""

import json
import math
from datetime import timedelta
from urllib.parse import quote

from app import app_settings, practice, revise, session_plan
from app.session_plan import BLOCK_MINUTES, stamp

SCOPE_PREFIX = "@s:"   # carried in the existing `scope` parameter of the question pages
CARD_STEPS = ("warmup", "learn")
QUESTION_STEPS = ("practise", "check", "review")
ACTIVITY_STEPS = CARD_STEPS + QUESTION_STEPS


def scope_for(session_id: int) -> str:
    return f"{SCOPE_PREFIX}{session_id}"


def session_of(scope: str):
    """The session id in a question page's scope, or None for an ordinary topic scope."""
    if scope and scope.startswith(SCOPE_PREFIX) and scope[len(SCOPE_PREFIX):].isdigit():
        return int(scope[len(SCOPE_PREFIX):])
    return None


# ---- Loading and saving --------------------------------------------------------------------

def get(conn, session_id: int):
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return {**dict(row), "plan": json.loads(row["plan_json"])} if row else None


def save(conn, s: dict) -> None:
    conn.execute("UPDATE sessions SET plan_json = ?, block = ?, block_count = ?, ended_at = ?, focus_topic = ? "
                 "WHERE id = ?", (json.dumps(s["plan"]), s["block"], s["block_count"], s["ended_at"],
                                  s["focus_topic"], s["id"]))
    conn.commit()


def current_block(s: dict) -> dict:
    return s["plan"]["blocks"][s["block"]]


def _has_activity(block: dict) -> bool:
    return any(st["key"] in ACTIVITY_STEPS for st in block["steps"])


def _insert(conn, kind, day, subject, now, minutes, block_count, plan, focus) -> int:
    cur = conn.execute(
        """INSERT INTO sessions (kind, day, subject, started_at, length_planned, block_count, plan_json, focus_topic)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (kind, day, subject, stamp(now), minutes, block_count, json.dumps(plan), focus))
    conn.commit()
    return cur.lastrowid


# ---- Creating ----------------------------------------------------------------------------------

def blocks_needed(minutes: int) -> int:
    return max(1, math.ceil(minutes / BLOCK_MINUTES))


def preview(conn, minutes: int, now=None, subject: str = "") -> dict:
    """Today's plan as it would be built now (not saved): the block and its plain-words description."""
    now = now or session_plan.utc_now_dt()
    chosen = session_plan.pick_subject(conn, session_plan.local_day(now), subject)
    block = session_plan.build_block(conn, subject=chosen, minutes=min(minutes, BLOCK_MINUTES), now=now)
    return {"block": block, "text": session_plan.describe(conn, block), "has_work": _has_activity(block)}


def create_daily(conn, minutes: int, now=None, subject: str = "", focus: str = ""):
    """Start today's session. Returns the session id, or None if there is nothing to study."""
    now = now or session_plan.utc_now_dt()
    day = session_plan.local_day(now)
    chosen = session_plan.pick_subject(conn, day, subject)
    count = blocks_needed(minutes)
    block = session_plan.build_block(conn, subject=chosen, minutes=min(minutes, BLOCK_MINUTES), now=now,
                                     focus=focus, wrapup=count == 1)
    if not _has_activity(block):
        return None
    return _insert(conn, "daily", day.isoformat(), chosen, now, minutes, count,
                   {"blocks": [block], "log": []}, block["focus_topic"])


def create_year_end(conn, minutes: int = 30, now=None):
    """Start a year-end review. Returns the session id, or None if no approved generated question is available."""
    now = now or session_plan.utc_now_dt()
    block = session_plan.build_year_end(conn, minutes=minutes, now=now)
    if not _has_activity(block):
        return None
    return _insert(conn, "year_end", session_plan.local_day(now).isoformat(), "", now, minutes, 1,
                   {"blocks": [block], "log": []}, "")


def open_today(conn, now=None):
    """Her unfinished session from today, if any (offered as "Resume session")."""
    now = now or session_plan.utc_now_dt()
    row = conn.execute("SELECT id FROM sessions WHERE day = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
                       (session_plan.local_day(now).isoformat(),)).fetchone()
    return get(conn, row["id"]) if row else None


# ---- Where she is ---------------------------------------------------------------------------

def _answered_all(conn, s: dict, step: dict) -> bool:
    ids = step["items"]
    if not ids:
        return True
    found = conn.execute(
        f"""SELECT COUNT(DISTINCT question_id) FROM attempts
            WHERE parent_attempt_id IS NULL AND created_at >= ? AND question_id IN ({','.join('?' * len(ids))})""",
        [s["started_at"]] + ids).fetchone()[0]
    return found >= len(ids)


def _cards_recalled(conn, step: dict) -> bool:
    if not step["set_id"]:
        return False
    revise_set = revise.get_set(conn, step["set_id"])
    return revise_set is not None and all(revise.set_attempt(conn, revise_set["id"], c) for c in revise_set["card_ids"])


def refresh(conn, s: dict, now=None) -> dict:
    """Mark steps done when she has finished their work. Saves if anything changed."""
    now = now or session_plan.utc_now_dt()
    changed = False
    for step in current_block(s)["steps"]:
        if step["status"] != "pending" or not step["started_at"]:
            continue
        finished = (step["key"] in CARD_STEPS and _cards_recalled(conn, step)) or \
                   (step["key"] in QUESTION_STEPS and _answered_all(conn, s, step))
        if finished:
            step["status"], step["done_at"] = "done", stamp(now)
            changed = True
    if changed:
        save(conn, s)
    return s


def position(s: dict) -> dict:
    """{"state": "step", step, index, n} / {"state": "break"} / {"state": "ended"}."""
    if s["ended_at"]:
        return {"state": "ended"}
    steps = current_block(s)["steps"]
    for i, step in enumerate(steps):
        if step["status"] == "pending":
            return {"state": "step", "step": step, "index": i, "n": len(steps)}
    return {"state": "break"} if s["block"] < s["block_count"] - 1 else {"state": "ended"}


def bar(conn, session_id) -> dict | None:
    """What the question pages need to show "Step 3 of 6" and the "Not now" button, or None."""
    s = get(conn, session_id) if session_id else None
    if s is None or s["ended_at"]:
        return None
    pos = position(refresh(conn, s))
    if pos["state"] != "step":
        return None
    return {"session_id": s["id"], "index": pos["index"] + 1, "n": pos["n"], "title": pos["step"]["title"],
            "skippable": pos["step"]["key"] != "wrapup"}


# ---- Moving through steps ---------------------------------------------------------------------------

def start_step(conn, s: dict, now=None) -> None:
    """She starts the current step: stamp it and, for card steps, make the Revise set."""
    now = now or session_plan.utc_now_dt()
    pos = position(s)
    if pos["state"] != "step":
        return
    step = pos["step"]
    if step["started_at"] is None:
        step["started_at"] = stamp(now)
    if step["key"] in CARD_STEPS and not step["set_id"]:
        topics = [current_block(s)["focus_topic"]] if step["key"] == "learn" else []
        step["set_id"] = revise.create_set(
            conn, card_ids=step["items"], subject=current_block(s)["subject"], topics=topics,
            recall_timing=app_settings.get_all(conn)["recall_timing"], session_id=s["id"], now=now)
    save(conn, s)


def finish_step(conn, s: dict, status: str, now=None) -> None:
    """Mark the current step done ("Got it", "Let's go") or skipped ("Not now"). Finishing the wrap-up ends the session."""
    now = now or session_plan.utc_now_dt()
    pos = position(s)
    if pos["state"] != "step" or status not in ("done", "skipped"):
        return
    step = pos["step"]
    if step["key"] == "wrapup" and status == "skipped":
        return
    step["started_at"] = step["started_at"] or stamp(now)
    step["status"], step["done_at"] = status, stamp(now)
    if status == "skipped":
        s["plan"]["log"].append({"event": "skip", "step": step["key"], "block": s["block"], "at": stamp(now)})
    if step["key"] == "wrapup":
        s["ended_at"] = stamp(now)
    save(conn, s)


def swap_focus(conn, s: dict, topic: str, now=None) -> bool:
    """Change the focus topic to one of the three suggestions and re-plan the steps after it."""
    now = now or session_plan.utc_now_dt()
    pos = position(s)
    if pos["state"] != "step" or pos["step"]["key"] != "focus":
        return False
    block, step = current_block(s), pos["step"]
    if topic not in {a["topic"] for a in step["alternatives"]}:
        return False
    earlier = s["plan"]["blocks"][:s["block"]]
    steps, i = block["steps"], pos["index"]
    before, wrap = steps[:i], [st for st in steps[i + 1:] if st["key"] == "wrapup"]
    budget = block["minutes"] - sum(st["est_min"] for st in before if st["status"] != "skipped")
    used = {x for b in earlier + [{"steps": before}] for st in b["steps"] for x in st["items"]}
    ranking = session_plan.rank_topics(conn, block["subject"], now, exclude=[b["focus_topic"] for b in earlier])
    new = session_plan.focus_steps(conn, block["subject"], topic, session_plan.alternatives_for(ranking, topic),
                                   budget, now, used)
    if not new:
        return False
    steps[i:] = new + wrap
    s["plan"]["log"].append({"event": "swap", "from": block["focus_topic"], "to": topic, "at": stamp(now)})
    block["focus_topic"] = s["focus_topic"] = topic
    save(conn, s)
    return True


def extend(conn, s: dict, minutes: int) -> bool:
    """"Take a break, then keep going": at the wrap-up, add one more block of 30 or 60 minutes.
    The wrap-up moves to the end of the new block; the break screen comes first."""
    pos = position(s)
    if (s["kind"] != "daily" or minutes not in session_plan.LENGTHS or pos["state"] != "step"
            or pos["step"]["key"] != "wrapup" or s["block"] != s["block_count"] - 1):
        return False
    current_block(s)["steps"].remove(pos["step"])
    s["block_count"] += 1
    s["length_planned"] += minutes
    s["plan"]["next_minutes"] = minutes
    conn.execute("UPDATE sessions SET length_planned = ? WHERE id = ?", (s["length_planned"], s["id"]))
    save(conn, s)
    return True


def next_block(conn, s: dict, now=None) -> None:
    """After the break: plan the next block from where she is now, on the next-best topic."""
    now = now or session_plan.utc_now_dt()
    if position(s)["state"] != "break":
        return
    k = s["block"] + 1
    earlier = s["plan"]["blocks"]
    last = k == s["block_count"] - 1
    minutes = s["plan"].pop("next_minutes", None) or min(BLOCK_MINUTES, s["length_planned"] - BLOCK_MINUTES * k)
    block = session_plan.build_block(
        conn, subject=s["subject"], minutes=minutes, now=now,
        exclude_topics=[b["focus_topic"] for b in earlier],
        exclude_ids={x for b in earlier for st in b["steps"] for x in st["items"]}, wrapup=last)
    if not _has_activity(block) and not last:  # nothing left to offer: finish with the wrap-up
        block["steps"].append(session_plan._step("wrapup"))
        s["block_count"] = k + 1
    s["plan"]["blocks"].append(block)
    s["block"] = k
    s["focus_topic"] = block["focus_topic"]
    save(conn, s)


# ---- Where to send her --------------------------------------------------------------------------

def next_question_url(conn, s: dict, step: dict) -> str:
    done = {r[0] for r in conn.execute(
        f"""SELECT DISTINCT question_id FROM attempts WHERE parent_attempt_id IS NULL AND created_at >= ?
            AND question_id IN ({','.join('?' * len(step['items']))})""", [s["started_at"]] + step["items"])}
    qid = next((q for q in step["items"] if q not in done), step["items"][0])
    return f"/q/{quote(qid)}?scope={quote(scope_for(s['id']), safe='')}"


def cards_url(conn, step: dict) -> str:
    """Back into the step's Revise set at the first card she hasn't recalled."""
    revise_set = revise.get_set(conn, step["set_id"])
    base = f"/revise/sets/{revise_set['id']}"
    todo = [i for i, c in enumerate(revise_set["card_ids"]) if not revise.set_attempt(conn, revise_set["id"], c)]
    if not todo:
        return f"{base}/summary"
    if len(todo) == len(revise_set["card_ids"]):
        return f"{base}/study/0"
    return f"{base}/{'study' if revise_set['recall_timing'] == 'after_each' else 'recall'}/{todo[0]}"


# ---- Wrap-up ---------------------------------------------------------------------------------

def summary(conn, s: dict, now=None) -> dict:
    """What went well, one thing to work on, and what tomorrow covers. All from code, no API call."""
    now = now or session_plan.utc_now_dt()
    attempts = conn.execute(
        """SELECT a.marks_awarded AS got, a.max_marks AS mx, q.topic, q.record_json FROM attempts a
           JOIN questions q ON q.id = a.question_id
           WHERE a.parent_attempt_id IS NULL AND a.marks_awarded IS NOT NULL AND a.created_at >= ?""",
        (s["started_at"],)).fetchall()
    cards = conn.execute("SELECT box_after FROM recall_attempts WHERE created_at >= ?", (s["started_at"],)).fetchall()
    by_topic, by_word = {}, {}
    for a in attempts:
        word = (json.loads(a["record_json"]).get("command_word") or "").lower()
        for table, key in ((by_topic, a["topic"]), (by_word, word)):
            if key:
                got, mx = table.get(key, (0, 0))
                table[key] = (got + a["got"], mx + a["mx"])
    pct = lambda pair: round(100 * pair[0] / pair[1]) if pair[1] else 0  # noqa: E731
    went_well = ""
    if by_topic:
        best = max(by_topic, key=lambda t: pct(by_topic[t]))
        went_well = f"You scored {pct(by_topic[best])}% on {session_plan.topic_title(conn, best)}."
    elif cards:
        went_well = f"You recalled {len(cards)} card{'s' if len(cards) != 1 else ''}, and {sum(c[0] > 1 for c in cards)} moved up a box."
    work_on = ""
    weak = [w for w in by_word if pct(by_word[w]) < 100]
    if weak:
        word = min(weak, key=lambda w: pct(by_word[w]))
        work_on = f"Answers that start with '{word}': {pct(by_word[word])}% of the marks. Read what the word asks for before you write."
    elif by_topic and min(pct(v) for v in by_topic.values()) < 100:
        low = min(by_topic, key=lambda t: pct(by_topic[t]))
        work_on = f"Come back to {session_plan.topic_title(conn, low)} ({pct(by_topic[low])}%)."
    tomorrow_day = session_plan.local_day(now) + timedelta(days=1)
    subject = session_plan.pick_subject(conn, tomorrow_day)
    ranking = session_plan.rank_topics(conn, subject, now) if subject else []
    tomorrow = (f"{practice.subject_label(subject)}" + (f": {ranking[0]['title']}" if ranking else "")) if subject else ""
    skipped = [st["title"] for b in s["plan"]["blocks"] for st in b["steps"] if st["status"] == "skipped"]
    return {"questions": len(attempts), "marks": sum(a["got"] for a in attempts), "max_marks": sum(a["mx"] for a in attempts),
            "cards": len(cards), "went_well": went_well, "work_on": work_on, "tomorrow": tomorrow, "skipped": skipped}
