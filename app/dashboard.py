"""The parent dashboard (docs/PARENT_DASHBOARD_SPEC.md): read-only figures over the existing tables.

No web code and no API calls. `build()` returns everything the page shows, section by section.
Times are Singapore time (a fixed +8 offset, no daylight saving). Scores use first attempts only
(parent_attempt_id IS NULL); a rewrite is counted separately in the exam-technique section.
A score % is total marks won / total marks available, so a 4-mark question counts for more than a 1-mark one.
"""

import calendar
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from app import charts, rewards, schedule, session_plan, session_track
from app.config import get_settings
from app.pricing import estimate_usd, unpriced_models
from app.session_plan import LOCAL_TZ, STAMP, TITLES
from app.topics import level_label, taught_topics, topic_level_sets

PERIODS = {"7": 7, "30": 30, "90": 90, "all": None}
PERIOD_LABELS = {"7": "7 days", "30": "30 days", "90": "90 days", "all": "All time"}
TARGET_PCT, START_PCT = 75, 45
TREND_STEP = 5.0                 # points of change before a topic shows ▲ or ▼
FLAG_MIN_ATTEMPTS = 3
FLAG_COUNT = 3
IDLE_DAYS = 14
SHORT_WORDS_PER_MARK = 6         # an answer this brief per mark, that also lost marks, is "too short"
LONG_WORDS_PER_MARK = 40         # this many words per mark or more is "far too long"
KNOWN_BOX = 3
MAX_SECONDS_PER_ATTEMPT = 3600   # longer than this is a page left open, not time spent
AMBER_AT, RED_AT = 0.8, 1.0
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# The Cost section's features, in display order, and the api_calls.purpose values that belong to each.
FEATURES = (
    ("marking", "Marking", ("marking",)),
    ("revise_checks", "Revise checks", ("recall",)),
    ("teaching", "Teaching explanations", ("tutor_explanation",)),
    ("hints", "Hints (Help me)", ("question_help_1", "question_help_2")),
    ("ask", "Ask about this", ("ask_about_this",)),
    ("remember", "Help me remember", ("card_help", "card_help_followup")),
    ("mcq", "MCQ explanations", ("mcq_explanation",)),
    ("ingestion", "Ingestion", ("ingest",)),
    ("generation", "Generation", ("generation",)),
)
OTHER_FEATURE = ("other", "Other (e.g. calibration)")
HINT_TIERS = {1: "Explain the question", 2: "Nudge", 3: "Show the card", 4: "Asked before submitting",
              5: "Read about this (before submitting)"}


# ---- Time ----------------------------------------------------------------------------------------

def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(STAMP)


def _day_start(day: date) -> str:
    """The UTC stamp of midnight Singapore time at the start of a local date."""
    return _stamp(datetime.combine(day, time.min, tzinfo=LOCAL_TZ))


def _local(stamp: str) -> datetime:
    return datetime.strptime(stamp, STAMP).replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)


def _json(text, default):
    try:
        value = json.loads(text) if text else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


@dataclass
class Period:
    key: str
    label: str
    start: str               # UTC stamps; a row is in the period when start <= created_at < end
    end: str
    prev_start: str | None   # the period of the same length just before (None for "all")
    prev_end: str | None
    start_day: date          # local dates, both included
    today: date
    days: int


def make_period(conn, key: str, now: datetime | None = None) -> Period:
    now = now or session_plan.utc_now_dt()
    key = key if key in PERIODS else "7"
    today = session_plan.local_day(now)
    end = _day_start(today + timedelta(days=1))
    n = PERIODS[key]
    if n:
        start_day = today - timedelta(days=n - 1)
        return Period(key, PERIOD_LABELS[key], _day_start(start_day), end,
                      _day_start(start_day - timedelta(days=n)), _day_start(start_day), start_day, today, n)
    first = conn.execute(
        "SELECT MIN(t) FROM (SELECT MIN(created_at) AS t FROM attempts UNION ALL "
        "SELECT MIN(created_at) FROM recall_attempts UNION ALL SELECT MIN(started_at) FROM sessions)").fetchone()[0]
    start_day = _local(first).date() if first else today
    return Period(key, PERIOD_LABELS[key], "", end, None, None, start_day, today, (today - start_day).days + 1)


# ---- First attempts, loaded once per period ------------------------------------------------------------

def _first_attempts(conn, start: str, end: str) -> list[dict]:
    """Marked first attempts in [start, end), with the question's fields and the parsed marking JSON."""
    rows = []
    for r in conn.execute(
            """SELECT a.id, a.created_at, a.marks_awarded, a.max_marks, a.valid_points, a.counted_points,
                      a.level_awarded, a.with_help, a.seconds_taken, a.answer_text, a.result_json, a.tutor_json,
                      q.subject, q.topic, q.marking_mode, q.record_json
               FROM attempts a JOIN questions q ON q.id = a.question_id
               WHERE a.parent_attempt_id IS NULL AND a.marks_awarded IS NOT NULL
                 AND a.created_at >= ? AND a.created_at < ? ORDER BY a.created_at""", (start, end)):
        row = dict(r)
        record = _json(row.pop("record_json"), {})
        result = _json(row.pop("result_json"), {})
        row["detail"] = result.get("detail") if isinstance(result.get("detail"), dict) else {}
        row["tutor"] = _json(row.pop("tutor_json"), {})
        row["question"] = record.get("question", "")
        row["command_word"] = (record.get("command_word") or "").strip().lower()
        row["words"] = len(row["answer_text"].split())
        row["local"] = _local(row["created_at"])
        rows.append(row)
    return rows


def _pct(rows: list[dict]):
    """Marks won as a percentage of marks available, or None for no rows."""
    available = sum(r["max_marks"] for r in rows)
    return round(100 * sum(r["marks_awarded"] for r in rows) / available, 1) if available else None


def _arrow(now_pct, before_pct) -> str:
    if now_pct is None or before_pct is None:
        return ""
    diff = now_pct - before_pct
    return "▲" if diff >= TREND_STEP else "▼" if diff <= -TREND_STEP else "●"


def _in(column: str) -> str:
    return f"{column} >= ? AND {column} < ?"


# ---- 2.1 At a glance --------------------------------------------------------------------------------

def _step_minutes(plan: dict) -> float:
    total = 0.0
    for block in plan.get("blocks", []):
        for step in block.get("steps", []):
            if step.get("status") == "done" and step.get("started_at") and step.get("done_at"):
                seconds = (_local(step["done_at"]) - _local(step["started_at"])).total_seconds()
                total += min(max(seconds, 0), MAX_SECONDS_PER_ATTEMPT)
    return total / 60


def glance(conn, p: Period, cur: list[dict], prev: list[dict] | None) -> dict:
    sessions = conn.execute(f"SELECT * FROM sessions WHERE {_in('started_at')}", (p.start, p.end)).fetchall()
    minutes = sum(_step_minutes(_json(s["plan_json"], {})) for s in sessions)
    recalled = conn.execute(f"SELECT COUNT(*) FROM recall_attempts WHERE {_in('created_at')}", (p.start, p.end)).fetchone()[0]
    checks = conn.execute(f"SELECT COUNT(*) FROM reward_events WHERE kind = 'check_passed' AND {_in('at')}",
                          (p.start, p.end)).fetchone()[0]
    active = conn.execute(
        f"""SELECT COUNT(*) FROM (SELECT date(created_at, '+8 hours') AS d FROM attempts WHERE {_in('created_at')}
            UNION SELECT date(created_at, '+8 hours') FROM recall_attempts WHERE {_in('created_at')})""",
        (p.start, p.end, p.start, p.end)).fetchone()[0]
    now_pct = _pct(cur)
    before = _pct(prev) if prev is not None else None
    return {
        "sessions_started": len(sessions), "sessions_completed": sum(1 for s in sessions if s["ended_at"]),
        "minutes": round(minutes), "questions": len(cur), "avg_pct": now_pct, "prev_pct": before,
        "change": round(now_pct - before, 1) if now_pct is not None and before is not None else None,
        "cards_recalled": recalled, "checks_passed": checks, "days_active": active, "days_total": p.days,
    }


# ---- 2.2 Score trend --------------------------------------------------------------------------------

def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def trend(p: Period, cur: list[dict]) -> dict:
    weeks, d = [], _week_start(p.start_day)
    while d <= p.today:
        weeks.append(d)
        d += timedelta(days=7)
    labels = [w.strftime("%d %b") for w in weeks]

    def weekly(rows):
        by_week = defaultdict(list)
        for r in rows:
            by_week[_week_start(r["local"].date())].append(r)
        return [_pct(by_week[w]) if by_week.get(w) else None for w in weeks]

    subject_series = [{"name": s.title(), "values": weekly([r for r in cur if r["subject"] == s])}
                      for s in schedule.SUBJECTS]
    mode_series = []
    for subject in ("history", "geography"):
        for mode, label in (("points", "points"), ("lorms", "LORMS")):
            values = weekly([r for r in cur if r["subject"] == subject and r["marking_mode"] == mode])
            if any(v is not None for v in values):
                mode_series.append({"name": f"{subject.title()} {label}", "values": values})
    refs = [{"label": f"Target {TARGET_PCT}%", "value": TARGET_PCT, "dash": "6 3"},
            {"label": f"Start {START_PCT}%", "value": START_PCT, "dash": "2 3"}]
    return {
        "labels": labels, "subjects": subject_series, "modes": mode_series,
        "subjects_svg": charts.line_chart(labels, subject_series, refs),
        "modes_svg": charts.line_chart(labels, mode_series, refs) if mode_series else "",
        "subjects_legend": charts.legend(subject_series), "modes_legend": charts.legend(mode_series),
    }


# ---- 2.3 Progress by topic ------------------------------------------------------------------------------

LEVEL_FILTERS = (("all", "All"), ("sec1", "Sec 1"), ("sec2", "Sec 2"))


def topics(conn, p: Period, cur: list[dict], prev: list[dict] | None, now: datetime, level: str = "all") -> dict:
    """{subject: [row]} for taught topics at every level (or just `level`: sec1 / sec2, which also keeps topics
    that suit both levels), with the weakest three flagged. The student's Study level never applies here."""
    level_sets, offered = topic_level_sets(conn), taught_topics(conn)   # offered: at her current study level
    by_topic, prev_by_topic = defaultdict(list), defaultdict(list)
    for r in cur:
        by_topic[r["topic"]].append(r)
    for r in prev or []:
        prev_by_topic[r["topic"]].append(r)
    recall = {r["topic"]: (r["cov"], r["tot"]) for r in conn.execute(
        f"""SELECT c.topic, SUM(r.points_covered) AS cov, SUM(r.points_total) AS tot
            FROM recall_attempts r JOIN revision_cards c ON c.id = r.card_id
            WHERE {_in('r.created_at')} GROUP BY c.topic""", (p.start, p.end))}
    due = {r[0]: r[1] for r in conn.execute(
        "SELECT c.topic, COUNT(*) FROM card_progress cp JOIN revision_cards c ON c.id = cp.card_id "
        "WHERE cp.due_at <= ? GROUP BY c.topic", (_stamp(now),))}
    last = {r[0]: r[1] for r in conn.execute(
        """SELECT topic, MAX(t) FROM (
               SELECT q.topic AS topic, a.created_at AS t FROM attempts a JOIN questions q ON q.id = a.question_id
               UNION ALL SELECT c.topic, r.created_at FROM recall_attempts r JOIN revision_cards c ON c.id = r.card_id)
           GROUP BY topic""")}
    states = rewards.topic_states(conn)
    idle_before = _stamp(now - timedelta(days=IDLE_DAYS))
    out = {}
    for subject, taught in schedule.taught_by_subject(conn, "all").items():
        rows = []
        for t in taught:
            levels = level_sets.get(t["id"], {"both"})
            if level in ("sec1", "sec2") and level not in levels and "both" not in levels:
                continue
            attempts = by_topic.get(t["id"], [])
            avg = _pct(attempts)
            cov, tot = recall.get(t["id"], (0, 0))
            seen = last.get(t["id"])
            rows.append({
                "subject": subject, "topic": t["id"], "title": t["title"], "level": level_label(levels),
                "offered": t["id"] in offered,
                "n": len(attempts), "avg": avg,
                "state": rewards.STATE_LABELS[states.get(t["id"], "not_started")],
                "trend": _arrow(avg, _pct(prev_by_topic.get(t["id"], []))) if prev is not None else "",
                "recall": round(100 * cov / tot) if tot else None, "due": due.get(t["id"], 0),
                "last": _local(seen).strftime("%d %b %Y") if seen else "never",
                # idle = touched before but not in the last 14 days; a topic never started shows as "Not started"
                "idle": seen is not None and seen < idle_before, "flag": "",
            })
        candidates = []
        for row in rows:
            if row["n"] >= FLAG_MIN_ATTEMPTS:
                candidates.append((row["avg"], row, "low average"))
            elif row["idle"]:
                candidates.append((0.0, row, f"no activity in {IDLE_DAYS} days"))
        for _, row, reason in sorted(candidates, key=lambda c: (c[0], c[1]["title"]))[:FLAG_COUNT]:
            row["flag"] = reason
        out[subject] = rows
    return out


# ---- 2.4 Exam technique -----------------------------------------------------------------------------------

def technique(conn, p: Period, cur: list[dict]) -> dict:
    points_rows = [r for r in cur if r["marking_mode"] == "points"]

    words = defaultdict(list)
    for r in cur:
        if r["command_word"]:
            words[r["command_word"]].append(r)
    command_words = sorted(({"word": w, "n": len(rows), "pct": _pct(rows)} for w, rows in words.items()),
                           key=lambda c: (c["pct"] or 0, c["word"]))

    term_count, term_topics = Counter(), defaultdict(Counter)
    for r in points_rows:
        for term in r["detail"].get("missing_key_terms") or []:
            key = str(term).strip().lower()
            if key:
                term_count[key] += 1
                term_topics[key][r["topic"]] += 1
    missed_terms = [{"term": t, "n": n, "topic": term_topics[t].most_common(1)[0][0]}
                    for t, n in sorted(term_count.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]

    reversed_all = []
    for r in points_rows:
        for point in r["detail"].get("points") or []:
            if point.get("reversed"):
                reversed_all.append({"when": r["created_at"], "question": r["question"],
                                     "phrase": point.get("evidence_quote") or point.get("reason") or ""})
    reversed_all.sort(key=lambda x: x["when"], reverse=True)

    filler = [sum(len(str(q).split()) for q in r["detail"].get("filler_quotes") or []) for r in points_rows]
    multi = [r for r in points_rows if r["max_marks"] >= 2]
    too_short = [r for r in multi if (r["counted_points"] or 0) < r["max_marks"]
                 and r["words"] <= SHORT_WORDS_PER_MARK * r["max_marks"]]
    too_long = [r for r in multi if r["words"] >= LONG_WORDS_PER_MARK * r["max_marks"]]

    window = (p.start, p.end)
    offered = conn.execute(
        f"""SELECT COUNT(*) FROM attempts WHERE parent_attempt_id IS NULL AND marks_awarded IS NOT NULL
            AND max_marks >= 2 AND marks_awarded < max_marks AND {_in('created_at')}""", window).fetchone()[0]
    done = conn.execute(
        f"""SELECT COUNT(*), AVG(r.marks_awarded - a.marks_awarded) FROM attempts a
            JOIN attempts r ON r.parent_attempt_id = a.id
            WHERE a.parent_attempt_id IS NULL AND r.marks_awarded IS NOT NULL AND {_in('a.created_at')}""", window).fetchone()
    skipped = conn.execute(
        f"""SELECT COUNT(*) FROM attempt_events e JOIN attempts a ON a.id = e.attempt_id
            WHERE e.event = 'rewrite_skipped' AND {_in('a.created_at')}""", window).fetchone()[0]

    lorms_rows = [r for r in cur if r["marking_mode"] == "lorms"]
    levels = Counter(r["level_awarded"] for r in lorms_rows if r["level_awarded"] is not None)
    needs = Counter()
    for r in lorms_rows:
        text = ((r["tutor"].get("lorms") or {}).get("next_level_needs") or "").strip()
        if text:
            needs[text.rstrip(".").lower()] += 1
    return {
        "command_words": command_words, "missed_terms": missed_terms,
        "reversed_count": len(reversed_all), "reversed_recent": reversed_all[:5],
        "waffle_share": round(100 * sum(1 for f in filler if f) / len(filler)) if filler else None,
        "waffle_words": round(sum(filler) / len(filler), 1) if filler else None, "points_answers": len(filler),
        "multi_mark": len(multi), "too_short": len(too_short), "too_long": len(too_long),
        "rewrites": {"offered": offered, "done": done[0], "skipped": skipped,
                     "avg_gain": round(done[1], 2) if done[1] is not None else None},
        "levels": sorted(levels.items()), "lorms_answers": len(lorms_rows),
        "next_level_needs": needs.most_common(5),
    }


# ---- 2.5 Help and discussion ----------------------------------------------------------------------

def help_section(conn, p: Period, cur: list[dict]) -> dict:
    window = (p.start, p.end)
    tiers = defaultdict(lambda: defaultdict(int))
    for r in conn.execute(
            f"""SELECT q.subject, h.tier, COUNT(*) AS n FROM hint_uses h JOIN questions q ON q.id = h.question_id
                WHERE {_in('h.created_at')} GROUP BY q.subject, h.tier""", window):
        tiers[r["subject"]][r["tier"]] = r["n"]
    helped = [r for r in cur if r["with_help"]]
    alone = [r for r in cur if not r["with_help"]]
    reads = defaultdict(lambda: {"before": 0, "after": 0, "revise": 0})   # "Read about this", every open
    for r in conn.execute(
            f"SELECT subject, stage, COUNT(*) AS n FROM reference_uses WHERE {_in('created_at')} GROUP BY subject, stage",
            window):
        reads[r["subject"]][r["stage"]] = r["n"]
    ask =conn.execute(f"SELECT COUNT(*), COALESCE(SUM(student_messages), 0) FROM discussions WHERE {_in('created_at')}", window).fetchone()
    top = conn.execute(
        f"""SELECT COALESCE(q.topic, c.topic) AS topic, COUNT(*) AS n
            FROM discussions d LEFT JOIN questions q ON d.kind = 'question' AND q.id = d.scope_id
            LEFT JOIN revision_cards c ON d.kind = 'card' AND c.id = d.scope_id
            WHERE {_in('d.created_at')} GROUP BY 1 ORDER BY n DESC, 1 LIMIT 5""", window).fetchall()
    wrong = conn.execute(
        """SELECT e.attempt_id FROM attempt_events e WHERE e.event = 'marking_looks_wrong'
             AND NOT EXISTS (SELECT 1 FROM attempt_events x WHERE x.attempt_id = e.attempt_id
                             AND x.event IN ('parent_agree', 'parent_disagree'))
           ORDER BY e.id DESC""").fetchall()
    return {
        "tiers": [{"subject": s, "counts": [tiers[s].get(t, 0) for t in HINT_TIERS]} for s in sorted(tiers)],
        "tier_names": list(HINT_TIERS.values()),
        "reads": [{"subject": s, **reads[s]} for s in sorted(reads)],
        "with_help_share": round(100 * len(helped) / len(cur)) if cur else None,
        "with_help_pct": _pct(helped), "without_help_pct": _pct(alone), "helped": len(helped),
        "conversations": ask[0], "messages": ask[1], "top_topics": [(r["topic"] or "unknown", r["n"]) for r in top],
        "looks_wrong": len(wrong), "looks_wrong_ids": [r["attempt_id"] for r in wrong[:5]],
    }


# ---- 2.6 Habits and timing -------------------------------------------------------------------------------

def habits(conn, p: Period, cur: list[dict]) -> dict:
    sessions = conn.execute(f"SELECT kind, started_at, plan_json FROM sessions WHERE {_in('started_at')}",
                            (p.start, p.end)).fetchall()
    grid = [[0] * 24 for _ in range(7)]
    steps = defaultdict(lambda: {"done": 0, "skipped": 0})
    skipped = Counter()
    for s in sessions:
        started = _local(s["started_at"])
        grid[started.weekday()][started.hour] += 1
        for block in _json(s["plan_json"], {}).get("blocks", []):
            for step in block.get("steps", []):
                if step.get("status") in ("done", "skipped"):
                    steps[s["kind"]][step["status"]] += 1
                if step.get("status") == "skipped":
                    skipped[step.get("key", "")] += 1
    per_mark = defaultdict(lambda: [0, 0])
    for r in cur:
        if r["seconds_taken"] is not None and 5 <= r["seconds_taken"] <= MAX_SECONDS_PER_ATTEMPT:
            per_mark[r["subject"]][0] += r["seconds_taken"]
            per_mark[r["subject"]][1] += r["max_marks"]
    labels = [f"{h:02d}" for h in range(24)]
    top_skipped = skipped.most_common(1)
    return {
        "grid": grid, "sessions": len(sessions), "heatmap_svg": charts.heatmap(grid, list(WEEKDAYS), labels),
        "steps": [{"kind": k, **v} for k, v in sorted(steps.items())],
        "most_skipped": (TITLES.get(top_skipped[0][0], top_skipped[0][0]), top_skipped[0][1]) if top_skipped else None,
        "minutes_per_mark": [{"subject": s, "minutes": round(sec / 60 / marks, 1)}
                             for s, (sec, marks) in sorted(per_mark.items()) if marks],
    }


# ---- 2.6b How sessions ended, idle time, short answers ---------------------------------------------------------

def sessions_section(conn, p: Period, now: datetime) -> dict:
    """Sessions that started in the period: shares by how they ended, idle time, and the flagged ones."""
    session_track.sweep_abandoned(conn, now)
    return session_track.summary(session_track.rows_between(conn, p.start, p.end, now))


def session_log(conn, now: datetime | None = None, limit: int = 100) -> list[dict]:
    """The Session log page: the latest sessions, newest first, with their figures."""
    now = now or session_plan.utc_now_dt()
    session_track.sweep_abandoned(conn, now)
    return session_track.rows_between(conn, "", "9999-12-31T00:00:00Z", now, limit)


# ---- 2.7 Revise ----------------------------------------------------------------------------------------------

def revise_section(conn, p: Period, now: datetime) -> dict:
    window = (p.start, p.end)
    seen, known, due = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(box >= ?), 0), COALESCE(SUM(due_at <= ?), 0) FROM card_progress",
        (KNOWN_BOX, _stamp(now))).fetchone()
    methods = {r["method"]: r for r in conn.execute(
        f"""SELECT method, COUNT(*) AS n, SUM(points_covered) AS cov, SUM(points_total) AS tot
            FROM recall_attempts WHERE {_in('created_at')} GROUP BY method""", window)}
    quick = conn.execute(
        f"""SELECT COUNT(*) AS n, SUM(r.points_covered) AS cov, SUM(r.points_total) AS tot
            FROM recall_attempts r JOIN revise_sets s ON s.id = r.set_id
            WHERE s.self_tick = 1 AND {_in('r.created_at')}""", window).fetchone()

    def rate(row):
        return round(100 * row["cov"] / row["tot"]) if row and row["tot"] else None

    return {
        "seen": seen, "known": known, "due": due,
        "methods": [{"method": m, "n": methods[m]["n"], "rate": rate(methods[m])} for m in ("typed", "paper", "parent_quiz") if m in methods],
        "quickfire_n": quick["n"], "quickfire_rate": rate(quick),
    }


# ---- 2.8 Rewards -----------------------------------------------------------------------------------------------

def rewards_section(conn, p: Period) -> dict:
    rank = rewards.rank_info(conn)
    done = conn.execute(f"SELECT COUNT(*) FROM commissions WHERE done_at IS NOT NULL AND {_in('done_at')}",
                        (p.start, p.end)).fetchone()[0]
    return {"rank": rank["rank"], "title": rank["title"], "shards": rewards.available_shards(conn),
            "held": rewards.held_shards(conn), "pending": rewards.pending_count(conn), "commissions": done}


# ---- 2.9 Cost --------------------------------------------------------------------------------------------------

def budget_colour(share) -> str:
    """green below 80% of the budget, amber from 80%, red from 100%."""
    if share is None:
        return "green"
    return "red" if share >= RED_AT else "amber" if share >= AMBER_AT else "green"


def cost(conn, now: datetime, settings=None) -> dict:
    """This calendar month (Singapore time) so far, split by feature, and the projection to month end.
    The period filter does not apply: the budget is monthly."""
    settings = settings or get_settings()
    today = session_plan.local_day(now)
    first = today.replace(day=1)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    start, end = _day_start(first), _day_start(first + timedelta(days=days_in_month))
    feature_of = {purpose: key for key, _, purposes in FEATURES for purpose in purposes}
    usd = defaultdict(float)
    calls = defaultdict(int)
    unpriced = 0
    for r in conn.execute(
            f"""SELECT purpose, model, COUNT(*) AS calls, SUM(input_tokens) AS i, SUM(output_tokens) AS o,
                       SUM(cache_creation_input_tokens) AS cw, SUM(cache_read_input_tokens) AS cr
                FROM api_calls WHERE {_in('created_at')} GROUP BY purpose, model""", (start, end)):
        key = feature_of.get(r["purpose"], OTHER_FEATURE[0])
        calls[key] += r["calls"]
        tokens = {"input_tokens": r["i"], "output_tokens": r["o"],
                  "cache_creation_input_tokens": r["cw"], "cache_read_input_tokens": r["cr"]}
        price = estimate_usd(r["model"], tokens)
        if price is None:
            if sum(tokens.values()):
                unpriced += r["calls"]      # served by a model with no price (e.g. a fallback model)
        else:
            usd[key] += price
    rate = settings.usd_sgd_rate
    lines = [{"key": key, "label": label, "calls": calls[key], "usd": round(usd[key], 4), "sgd": round(usd[key] * rate, 2)}
             for key, label, _ in FEATURES + ((OTHER_FEATURE[0], OTHER_FEATURE[1], ()),)]
    total_usd = sum(usd.values())
    api_sgd = total_usd * rate
    projected_api = api_sgd / today.day * days_in_month
    projected_total = projected_api + settings.subscription_sgd
    share = projected_total / settings.monthly_budget_sgd if settings.monthly_budget_sgd else None
    return {
        "month": first.strftime("%B %Y"), "lines": lines, "total_usd": round(total_usd, 2), "total_sgd": round(api_sgd, 2),
        "rate": rate, "subscription": settings.subscription_sgd, "budget": settings.monthly_budget_sgd,
        "so_far_sgd": round(api_sgd + settings.subscription_sgd, 2), "projected_api_sgd": round(projected_api, 2),
        "projected_total_sgd": round(projected_total, 2), "share": round(share * 100) if share is not None else None,
        "colour": budget_colour(share), "unpriced_calls": unpriced, "unpriced_models": unpriced_models(settings),
        "days_elapsed": today.day, "days_in_month": days_in_month,
    }


# ---- The whole page --------------------------------------------------------------------------------------------

def build(conn, period_key: str = "7", now: datetime | None = None, settings=None, level: str = "all") -> dict:
    now = now or session_plan.utc_now_dt()
    level = level if level in dict(LEVEL_FILTERS) else "all"
    p = make_period(conn, period_key, now)
    cur = _first_attempts(conn, p.start, p.end)
    prev = _first_attempts(conn, p.prev_start, p.prev_end) if p.prev_start is not None else None
    return {
        "period": p, "periods": [(k, PERIOD_LABELS[k]) for k in PERIODS], "subjects": schedule.SUBJECTS,
        "glance": glance(conn, p, cur, prev), "trend": trend(p, cur),
        "level": level, "levels": LEVEL_FILTERS,
        "topics": topics(conn, p, cur, prev, now, level), "technique": technique(conn, p, cur),
        "help": help_section(conn, p, cur), "habits": habits(conn, p, cur), "sessions": sessions_section(conn, p, now),
        "revise": revise_section(conn, p, now), "rewards": rewards_section(conn, p),
        "cost": cost(conn, now, settings),
        "generated": now.astimezone(LOCAL_TZ).strftime("%d %b %Y, %H:%M"),
    }
