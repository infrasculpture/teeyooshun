"""Accountability for sessions: activity and idle gaps, how a session ended, per-step counts, short answers.

Pure rules plus database reads and small writes; no web code and no API calls. The page pings and the idle
log write their own columns on the sessions table (see schema_v18.sql), never plan_json, so a ping can't
overwrite a step that another request has just saved.

Active time (what the time-out measures) = clock time since the session started, minus idle gaps of more than
GAP_SECONDS, minus the break screens between blocks.
"""

import json
from datetime import datetime, timedelta, timezone

from app import revise
from app.session_plan import LOCAL_TZ, STAMP, TITLES, stamp

CARD_STEPS = ("warmup", "learn", "quickfire")
QUESTION_STEPS = ("practise", "check", "review")

GAP_SECONDS = 180                    # no sign of activity for longer than this is logged as idle
ABANDONED_AFTER = timedelta(hours=2)  # an unfinished session with no activity for this long is abandoned
SHORT_WORDS = 5                      # an answer under this many words ...
SHORT_MIN_MARKS = 2                  # ... to a question worth at least this many marks is "short"
IDLE_FLAG_SHARE = 1 / 3              # flag a session that was idle for more than this share of its time
SPIKE_MIN = 3                        # flag a session with at least this many short answers ...
SPIKE_SHARE = 1 / 3                  # ... making up at least this share of its multi-mark answers

STATE_LABELS = {"finished": "Finished", "timed_out": "Timed out", "continued": "Kept going after time-out",
                "abandoned": "Abandoned", "running": "In progress"}


def parse(text: str) -> datetime:
    return datetime.strptime(text, STAMP).replace(tzinfo=timezone.utc)


def _json(text, default):
    try:
        value = json.loads(text) if text else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


# ---- Activity and idle gaps -------------------------------------------------------------------------------

def touch(conn, session_id, now: datetime | None = None) -> int:
    """She is active on this session now (a page request or a ping). If the last sign of activity was more than
    GAP_SECONDS ago, the gap is logged as idle and its length in seconds is returned (0 otherwise). A gap while she is
    on the break screen is a break, not idle. An abandoned session that she comes back to is running again."""
    if not session_id:
        return 0
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT started_at, ended_at, last_active_at, end_state, break_from FROM sessions WHERE id = ?",
                       (session_id,)).fetchone()
    if row is None or row["ended_at"]:
        return 0
    last = parse(row["last_active_at"] or row["started_at"])
    gap = (now - last).total_seconds()
    sets, params, idle = ["last_active_at = ?"], [stamp(now)], 0
    if not row["break_from"] and gap > GAP_SECONDS:
        idle = int(gap)
        sets += ["idle_seconds = idle_seconds + ?", "idle_json = json_insert(idle_json, '$[#]', json(?))"]
        params += [idle, json.dumps({"from": stamp(last), "to": stamp(now), "seconds": idle})]
    if row["end_state"] == "abandoned":
        sets.append("end_state = NULL")
    conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", params + [session_id])
    conn.commit()
    return idle


def start_break(conn, session_id: int, now: datetime) -> None:
    """The break screen is up. Safe to call again: the break keeps its first start time."""
    conn.execute("UPDATE sessions SET break_from = COALESCE(break_from, ?), last_active_at = ? WHERE id = ?",
                 (stamp(now), stamp(now), session_id))
    conn.commit()


def end_break(conn, session_id: int, now: datetime) -> None:
    row = conn.execute("SELECT break_from FROM sessions WHERE id = ?", (session_id,)).fetchone()
    seconds = int((now - parse(row["break_from"])).total_seconds()) if row and row["break_from"] else 0
    conn.execute("UPDATE sessions SET break_from = NULL, break_seconds = break_seconds + ?, last_active_at = ? WHERE id = ?",
                 (max(seconds, 0), stamp(now), session_id))
    conn.commit()


def count_blank(conn, session_id) -> None:
    """She pressed Submit with nothing written."""
    if session_id:
        conn.execute("UPDATE sessions SET blank_submits = blank_submits + 1 WHERE id = ? AND ended_at IS NULL", (session_id,))
        conn.commit()


def active_seconds(s, until: datetime) -> float:
    """Study time up to `until`: clock time minus idle gaps minus breaks. `s` is a sessions row or dict."""
    end = parse(s["ended_at"]) if s["ended_at"] and parse(s["ended_at"]) < until else until
    total = (end - parse(s["started_at"])).total_seconds()
    on_break = (end - parse(s["break_from"])).total_seconds() if s["break_from"] else 0
    return max(0.0, total - s["idle_seconds"] - s["break_seconds"] - max(on_break, 0))


def is_overtime(s: dict, now: datetime) -> bool:
    """She has used up the planned length. Only once per session: after the time-out (or after "Keep going")
    it is never true again. `s` is a session from session.get()."""
    if s["ended_at"] or s["plan"].get("timeout"):
        return False
    return active_seconds(s, now) > s["length_planned"] * 60


def sweep_abandoned(conn, now: datetime) -> None:
    """Record unfinished sessions with no activity for ABANDONED_AFTER as abandoned (coming back clears it)."""
    conn.execute("UPDATE sessions SET end_state = 'abandoned' WHERE ended_at IS NULL AND end_state IS NULL "
                 "AND COALESCE(last_active_at, started_at) < ?", (stamp(now - ABANDONED_AFTER),))
    conn.commit()


# ---- Counts ---------------------------------------------------------------------------------------------------

def step_counts(conn, started_at: str, step: dict) -> dict:
    """planned / done / skipped / cut for one step. Planned is the number of questions or cards; done is how many
    she did; skipped is what "Not now" left undone; cut is what the time-out left undone."""
    items = step.get("items") or []
    done = 0
    if step.get("key") in QUESTION_STEPS and items:
        done = conn.execute(
            f"""SELECT COUNT(DISTINCT question_id) FROM attempts WHERE parent_attempt_id IS NULL AND created_at >= ?
                AND question_id IN ({','.join('?' * len(items))})""", [started_at] + items).fetchone()[0]
    elif step.get("key") in CARD_STEPS and step.get("set_id"):
        revise_set = revise.get_set(conn, step["set_id"])
        if revise_set:
            done = sum(1 for c in revise_set["card_ids"] if revise.set_attempt(conn, revise_set["id"], c))
    done = min(done, len(items))
    skipped = len(items) - done if step.get("status") == "skipped" else 0
    return {"planned": len(items), "done": done, "skipped": skipped, "cut": step.get("cut", 0) or 0}


def _question_ids(plan: dict) -> list:
    return [q for block in plan.get("blocks", []) for st in block.get("steps", [])
            if st.get("key") in QUESTION_STEPS for q in st.get("items") or []]


def answer_counts(conn, row, plan: dict) -> tuple[int, int]:
    """(answers to questions worth 2 marks or more, how many of them were short) among her first attempts."""
    ids = _question_ids(plan)
    if not ids:
        return 0, 0
    params = [row["started_at"]] + ids
    until = ""
    if row["ended_at"]:
        until, params = " AND created_at <= ?", params + [row["ended_at"]]
    multi, short = 0, 0
    for r in conn.execute(
            f"""SELECT answer_text FROM attempts WHERE parent_attempt_id IS NULL AND max_marks >= {SHORT_MIN_MARKS}
                AND created_at >= ? AND question_id IN ({','.join('?' * len(ids))}){until}""", params):
        multi += 1
        short += len((r["answer_text"] or "").split()) < SHORT_WORDS
    return multi, short


# ---- The session log -----------------------------------------------------------------------------------------

def state_of(row, now: datetime) -> str:
    """finished / timed_out / continued / abandoned / running."""
    if row["end_state"]:
        return row["end_state"]
    if row["ended_at"]:
        return "finished"
    stale = parse(row["last_active_at"] or row["started_at"]) < now - ABANDONED_AFTER
    return "abandoned" if stale else "running"


def read_counts(conn, session_id: int) -> dict:
    """How often she opened 'Read about this' in this session: {'before': n, 'after': n, 'revise': n}.
    Before submitting counts as help; the others do not."""
    counts = {"before": 0, "after": 0, "revise": 0}
    for r in conn.execute("SELECT stage, COUNT(*) AS n FROM reference_uses WHERE session_id = ? GROUP BY stage",
                          (session_id,)):
        counts[r["stage"]] = r["n"]
    return counts


def describe(conn, row, now: datetime) -> dict:
    """Everything the log page shows for one session (row is a sessions row)."""
    plan = _json(row["plan_json"], {})
    stopped = parse(row["ended_at"]) if row["ended_at"] else min(now, parse(row["last_active_at"] or row["started_at"]))
    active, idle = active_seconds(row, stopped), row["idle_seconds"]
    steps = []
    for block in plan.get("blocks", []):
        for step in block.get("steps", []):
            if step.get("key") in CARD_STEPS + QUESTION_STEPS:
                steps.append({"title": TITLES.get(step["key"], step.get("title", step["key"])), "status": step.get("status", "pending"),
                              **step_counts(conn, row["started_at"], step)})
    multi, short = answer_counts(conn, row, plan)
    flags = []
    if active + idle and idle / (active + idle) > IDLE_FLAG_SHARE:
        flags.append(f"Idle for {round(100 * idle / (active + idle))}% of the session")
    if short >= SPIKE_MIN and multi and short / multi >= SPIKE_SHARE:
        flags.append(f"{short} of {multi} multi-mark answers were very short")
    gaps = _json(row["idle_json"], [])
    state = state_of(row, now)
    return {
        "id": row["id"], "started_at": row["started_at"], "kind": row["kind"], "subject": row["subject"],
        "started_local": parse(row["started_at"]).astimezone(LOCAL_TZ).strftime("%a %d %b %Y, %H:%M"),
        "planned_min": row["length_planned"], "active_min": round(active / 60), "idle_min": round(idle / 60, 1),
        "idle_seconds": idle, "idle_gaps": len(gaps), "state": state, "state_label": STATE_LABELS[state],
        "steps": steps, "multi_answers": multi, "short_answers": short, "blank_submits": row["blank_submits"],
        "flags": flags, "reads": read_counts(conn, row["id"]),
    }


def rows_between(conn, start: str, end: str, now: datetime, limit: int | None = None) -> list[dict]:
    """Sessions that started in [start, end), newest first."""
    sql = "SELECT * FROM sessions WHERE started_at >= ? AND started_at < ? ORDER BY started_at DESC, id DESC"
    params = [start, end]
    if limit:
        sql, params = sql + " LIMIT ?", params + [limit]
    return [describe(conn, r, now) for r in conn.execute(sql, params).fetchall()]


def summary(sessions: list[dict]) -> dict:
    """The dashboard's view of a list of described sessions."""
    n = len(sessions)
    counts = {k: sum(1 for s in sessions if s["state"] == k) for k in STATE_LABELS}
    share = lambda k: round(100 * counts[k] / n) if n else 0  # noqa: E731
    return {
        "n": n, "counts": counts, "shares": {k: share(k) for k in STATE_LABELS},
        "idle_min": round(sum(s["idle_seconds"] for s in sessions) / 60),
        "short_answers": sum(s["short_answers"] for s in sessions), "blank_submits": sum(s["blank_submits"] for s in sessions),
        "flagged": [s for s in sessions if s["flags"]],
    }
