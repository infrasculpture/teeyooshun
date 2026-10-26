"""Parent's schedule settings (task 5b): session length and subject per weekday, "focus this week" per subject,
and the K-pop celebration switch.

They are stored in the same app_settings table as the other settings, under their own keys, so the existing
settings (app_settings.DEFAULTS) are untouched. No web code and no API calls. Dates are Singapore dates
(YYYY-MM-DD), passed in by the caller so the rules are easy to test.
"""

import json
from datetime import date, timedelta

from app.db import utc_now
from app.topics import taught_topics

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")   # date.weekday() order
LENGTHS = (30, 60)
SUBJECTS = ("science", "geography", "history")
MIX = "mix"
SUBJECT_CHOICES = ("",) + SUBJECTS + (MIX,)      # "" = automatic (the rotation decides)
FOCUS_DEFAULT_DAYS = 7
FOCUS_MAX_DAYS = 366

# Until the parent changes it, every day is 30 minutes and the subject rotation decides, as before.
DEFAULT_DAY = {"minutes": 30, "subject": ""}


def _get(conn, key: str, default):
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def _put(conn, key: str, value) -> None:
    conn.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                 (key, json.dumps(value), utc_now()))
    conn.commit()


# ---- Per-weekday length and subject -------------------------------------------------------------------

def get_schedule(conn) -> list[dict]:
    """Seven rows, Monday first: {"day", "minutes", "subject"}."""
    saved = _get(conn, "schedule", {})
    rows = []
    for name in WEEKDAYS:
        entry = saved.get(name) if isinstance(saved, dict) else None
        entry = entry if isinstance(entry, dict) else {}
        minutes = entry.get("minutes") if entry.get("minutes") in LENGTHS else DEFAULT_DAY["minutes"]
        subject = entry.get("subject") if entry.get("subject") in SUBJECT_CHOICES else DEFAULT_DAY["subject"]
        rows.append({"day": name, "minutes": minutes, "subject": subject})
    return rows


def for_day(conn, day: date) -> dict:
    """The default length and subject for a date: {"minutes": 30 or 60, "subject": a subject, "mix" or ""}."""
    row = get_schedule(conn)[day.weekday()]
    return {"minutes": row["minutes"], "subject": row["subject"]}


def validate_schedule(form: dict) -> tuple[dict, list[str]]:
    """(the schedule as {day: {"minutes", "subject"}}, problems) from posted fields minutes_<Day> and subject_<Day>."""
    values, problems = {}, []
    for name in WEEKDAYS:
        minutes, subject = str(form.get(f"minutes_{name}", "")).strip(), str(form.get(f"subject_{name}", "")).strip()
        if not minutes.isdigit() or int(minutes) not in LENGTHS:
            problems.append(f"{name}: session length must be 30 or 60 minutes.")
        if subject not in SUBJECT_CHOICES:
            problems.append(f"{name}: subject must be Science, Geography, History, Mix or automatic.")
        values[name] = {"minutes": int(minutes) if minutes.isdigit() else minutes, "subject": subject}
    return values, problems


def save_schedule(conn, form: dict) -> list[str]:
    values, problems = validate_schedule(form)
    if not problems:
        _put(conn, "schedule", values)
    return problems


# ---- Focus this week ----------------------------------------------------------------------------------

def get_focus(conn) -> dict:
    """{subject: {"topic", "until"}} as saved (expired entries included; use active_focus to apply the rules)."""
    saved = _get(conn, "focus", {})
    return {s: e for s, e in saved.items() if s in SUBJECTS and isinstance(e, dict)} if isinstance(saved, dict) else {}


def taught_by_subject(conn, level=None) -> dict:
    """{subject: [{"id", "title"}]} of taught topics, in topic-tree order (the choices for a focus topic).
    `level` is None for the parent's Study level, or 'all' for the dashboard, which shows every level."""
    taught = taught_topics(conn, level)
    out = {s: [] for s in SUBJECTS}
    for r in conn.execute("SELECT id, subject, title FROM topics ORDER BY subject, sort_order, id"):
        if r["id"] in taught and r["subject"] in out:
            out[r["subject"]].append({"id": r["id"], "title": r["title"] or r["id"]})
    return out


def active_focus(conn, subject: str, today: date) -> str:
    """The parent's focus topic for a subject if one is set, hasn't passed its end date (that day included) and is
    still a taught topic of that subject. Otherwise "" (the planner picks as usual). Expiry needs no clean-up job."""
    entry = get_focus(conn).get(subject)
    if not entry:
        return ""
    try:
        until = date.fromisoformat(entry.get("until", ""))
    except (TypeError, ValueError):
        return ""
    topic = entry.get("topic", "")
    if until < today or topic not in {t["id"] for t in taught_by_subject(conn).get(subject, [])}:
        return ""
    return topic


def default_until(today: date) -> str:
    return (today + timedelta(days=FOCUS_DEFAULT_DAYS)).isoformat()


def save_focus(conn, subject: str, topic: str, until: str, today: date) -> list[str]:
    """Set (or, with a blank topic, clear) the focus for one subject. A blank end date means 7 days from today."""
    if subject not in SUBJECTS:
        return ["Pick a subject."]
    focus = {s: e for s, e in get_focus(conn).items() if s != subject}   # also drops the entry being replaced
    if topic:
        if topic not in {t["id"] for t in taught_by_subject(conn)[subject]}:
            return ["Pick a taught topic from this subject."]
        try:
            end = date.fromisoformat(until.strip()) if until.strip() else today + timedelta(days=FOCUS_DEFAULT_DAYS)
        except ValueError:
            return ["The end date must look like 2026-10-27."]
        if end < today or end > today + timedelta(days=FOCUS_MAX_DAYS):
            return ["The end date must be today or later, within a year."]
        focus[subject] = {"topic": topic, "until": end.isoformat()}
    focus = {s: e for s, e in focus.items() if _alive(e, today)}   # tidy away expired entries while we are here
    _put(conn, "focus", focus)
    return []


def _alive(entry: dict, today: date) -> bool:
    try:
        return date.fromisoformat(entry.get("until", "")) >= today
    except (TypeError, ValueError):
        return False


# ---- K-pop celebrations --------------------------------------------------------------------------------

def kpop_enabled(conn) -> bool:
    return _get(conn, "kpop_celebrations", False) is True


def set_kpop(conn, on: bool) -> None:
    _put(conn, "kpop_celebrations", bool(on))
