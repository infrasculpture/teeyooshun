"""The session planner (docs/STUDENT_UI_SPEC.md section 3): what goes into a session and in what order.

Pure rules plus database reads; no web code and no API calls. A *block* is up to 60 minutes of
steps. Order: warm-up, focus topic, learn, practise, topic check, wrap-up. A step with nothing to
offer is left out silently and its time passes to the next step.
"""

import json
import statistics
from datetime import datetime, timedelta, timezone

from app import app_settings, revise
from app.practice import PRACTICE_FILTER, subject_label, topic_label
from app.revision_cards import get_card
from app.topics import TAUGHT_FILTER

STAMP = "%Y-%m-%dT%H:%M:%SZ"
LOCAL_TZ = timezone(timedelta(hours=8))   # Singapore: no daylight saving, so a fixed offset is exact
BLOCK_MINUTES = 60
LENGTHS = (30, 60)                        # what she can pick; longer sessions come from the parent's setting later

QUICKFIRE_MAX = 5                         # one-point cards bundled into the Quick-fire step
CHECK_MIN, CHECK_MAX = 3, 5               # (warm-up and Learn sizes are parent settings: warmup_max, learn_count)
DEFAULT_CARD_MIN = 1.5                    # minutes per card until her history says otherwise
DEFAULT_MARK_MIN = 1.2                    # minutes per mark of a question
MIN_SAMPLES = 5
RECENT_DAYS = 14
YEAR_END_NEUTRAL = 0.5                    # a topic with no attempts yet sorts as if she scored 50%

# Science twice as often as Geography and History.
ROTATION = ("science", "geography", "science", "history")
CHOICES = ("science", "geography", "history")   # the subject chips on the home screen, plus MIX
MIX = "mix"

TITLES = {"warmup": "Warm-up", "focus": "Today's focus", "learn": "Learn", "quickfire": "Quick-fire",
          "practise": "Practise",
          "check": "Topic check", "review": "Year-end review", "wrapup": "Wrap-up"}


def stamp(now: datetime) -> str:
    return now.strftime(STAMP)


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def local_day(now: datetime):
    return now.astimezone(LOCAL_TZ).date()


def topic_title(conn, topic: str) -> str:
    row = conn.execute("SELECT title FROM topics WHERE id = ?", (topic,)).fetchone()
    return row["title"] if row else topic_label(topic)


# ---- Time estimates (her logged history, else the defaults) --------------------------

def time_estimates(conn) -> tuple[float, float]:
    """(minutes per card, minutes per mark of a question)."""
    secs = [r[0] for r in conn.execute(
        "SELECT seconds_taken FROM recall_attempts WHERE seconds_taken BETWEEN 5 AND 1800 ORDER BY id DESC LIMIT 200")]
    card = statistics.median(secs) / 60 if len(secs) >= MIN_SAMPLES else DEFAULT_CARD_MIN
    per_mark = [r[0] for r in conn.execute(
        """SELECT seconds_taken * 1.0 / max_marks FROM attempts
           WHERE parent_attempt_id IS NULL AND max_marks > 0 AND seconds_taken BETWEEN 5 AND 3600
           ORDER BY id DESC LIMIT 200""")]
    mark = statistics.median(per_mark) / 60 if len(per_mark) >= MIN_SAMPLES else DEFAULT_MARK_MIN
    return card, mark


# ---- Subject and focus topic ----------------------------------------------------------------

def subjects_with_content(conn) -> list[str]:
    rows = conn.execute(
        f"""SELECT q.subject FROM questions q WHERE {PRACTICE_FILTER}
            UNION SELECT c.subject FROM revision_cards c WHERE c.topic {TAUGHT_FILTER}""").fetchall()
    return sorted(r["subject"] for r in rows)


def pick_subject(conn, day, override: str = "") -> str:
    """Today's subject: the parent's choice if it has content, else the rotation (skipping empty subjects)."""
    available = set(subjects_with_content(conn))
    if override in available:
        return override
    start = day.toordinal() % len(ROTATION)
    for k in range(len(ROTATION)):
        subject = ROTATION[(start + k) % len(ROTATION)]
        if subject in available:
            return subject
    return sorted(available)[0] if available else ""


def recent_scores(conn, subject: str, now: datetime) -> dict:
    """{topic: share of marks won} on her first attempts in the last 14 days."""
    since = stamp(now - timedelta(days=RECENT_DAYS))
    return {r["topic"]: r["marks"] / r["mx"] for r in conn.execute(
        """SELECT q.topic, SUM(a.marks_awarded) AS marks, SUM(a.max_marks) AS mx
           FROM attempts a JOIN questions q ON q.id = a.question_id
           WHERE a.parent_attempt_id IS NULL AND a.marks_awarded IS NOT NULL AND a.created_at >= ? AND q.subject = ?
           GROUP BY q.topic""", (since, subject)) if r["mx"]}


def weakest_subject(conn, day, now: datetime) -> str:
    """For "Mix": the subject holding her weakest recent topic. With no recent scores, the day's rotation subject."""
    suggested = pick_subject(conn, day)
    best, best_score = suggested, None
    for subject in sorted(set(subjects_with_content(conn)), key=lambda s: s != suggested):  # suggested wins ties
        topics = {t["topic"] for t in rank_topics(conn, subject, now)}
        scores = [v for k, v in recent_scores(conn, subject, now).items() if k in topics]
        if scores and (best_score is None or min(scores) < best_score):
            best, best_score = subject, min(scores)
    return best


def resolve_subject(conn, day, now: datetime, choice: str = "") -> str:
    """The subject a choice stands for: "mix" is the weakest-topic subject, anything else goes through pick_subject."""
    return weakest_subject(conn, day, now) if choice == MIX else pick_subject(conn, day, choice)


def rank_topics(conn, subject: str, now: datetime, exclude=()) -> list[dict]:
    """Taught topics with something to do, best focus first: lowest first-attempt score in the last
    14 days; then the rest, most recently marked taught first, then topic-tree order."""
    exclude = set(exclude)
    topics = [dict(r) for r in conn.execute(
        f"""SELECT x.topic, COALESCE(t.title, x.topic) AS title, COALESCE(t.sort_order, 999) AS sort_order
            FROM (SELECT q.topic FROM questions q WHERE q.subject = ? AND {PRACTICE_FILTER}
                  UNION SELECT c.topic FROM revision_cards c WHERE c.subject = ? AND c.topic {TAUGHT_FILTER}) x
            LEFT JOIN topics t ON t.id = x.topic ORDER BY sort_order, x.topic""", (subject, subject))
        if r["topic"] not in exclude]
    for t in topics:
        if t["title"] == t["topic"]:
            t["title"] = topic_label(t["topic"])
    scores = recent_scores(conn, subject, now)
    taught_at = {r["topic"]: r["updated_at"] for r in conn.execute(
        "SELECT topic, updated_at FROM topic_status WHERE status = 'taught'")}
    scored = sorted((t for t in topics if t["topic"] in scores), key=lambda t: (scores[t["topic"]], t["sort_order"]))
    rest = [t for t in topics if t["topic"] not in scores]  # already in topic-tree order
    rest.sort(key=lambda t: taught_at.get(t["topic"], ""), reverse=True)  # stable: ties keep tree order
    return scored + rest


# ---- Choosing items ------------------------------------------------------------------------

def command_word_scores(conn) -> dict:
    """{command word: share of marks won} from her first attempts."""
    totals = {}
    for r in conn.execute(
            """SELECT q.record_json, a.marks_awarded, a.max_marks FROM attempts a
               JOIN questions q ON q.id = a.question_id
               WHERE a.parent_attempt_id IS NULL AND a.marks_awarded IS NOT NULL"""):
        word = (json.loads(r["record_json"]).get("command_word") or "").lower()
        if word and r["max_marks"]:
            got, out_of = totals.get(word, (0, 0))
            totals[word] = (got + r["marks_awarded"], out_of + r["max_marks"])
    return {w: got / out_of for w, (got, out_of) in totals.items()}


def question_pool(conn, *, topic: str = "", generated: bool) -> list[dict]:
    """Questions she can be given, as {"id", "marks", "word", "topic", "last_tried"}.
    Real questions are every source but 'generated'; the topic check and year-end review use only generated ones."""
    where, params = (f"q.source {'=' if generated else '!='} 'generated' AND {PRACTICE_FILTER}"), []
    if topic:
        where += " AND q.topic = ?"
        params.append(topic)
    rows = conn.execute(
        f"""SELECT q.id, q.marks, q.topic, q.record_json, MAX(a.created_at) AS last_tried
            FROM questions q LEFT JOIN attempts a ON a.question_id = q.id AND a.parent_attempt_id IS NULL
            WHERE {where} GROUP BY q.id ORDER BY q.id""", params).fetchall()
    return [{"id": r["id"], "marks": r["marks"], "topic": r["topic"], "last_tried": r["last_tried"],
             "word": (json.loads(r["record_json"]).get("command_word") or "").lower()} for r in rows]


def _mix(rows: list[dict]) -> list[dict]:
    """Alternate 1-mark and multi-mark questions, starting with a 1-mark one."""
    ones, multi, out = [r for r in rows if r["marks"] == 1], [r for r in rows if r["marks"] > 1], []
    while ones or multi:
        if ones:
            out.append(ones.pop(0))
        if multi:
            out.append(multi.pop(0))
    return out


def _take(items: list[tuple], budget: float, limit: int) -> list[tuple]:
    """Items (id, minutes) in order while they fit the budget (never nothing if there is any time left)."""
    chosen, spent = [], 0.0
    for item in items:
        if len(chosen) >= limit:
            break
        if spent + item[1] <= budget or (not chosen and budget > 0):
            chosen.append(item)
            spent += item[1]
    return chosen


def _step(key: str, items=(), est: float = 0.0, **extra) -> dict:
    return {"key": key, "title": TITLES[key], "items": list(items), "est_min": round(est, 1), "status": "pending",
            "started_at": None, "done_at": None, "set_id": None, **extra}


# ---- Building a block ---------------------------------------------------------------------

def focus_steps(conn, subject: str, focus: str, alternatives: list, budget: float, now: datetime,
                exclude_ids=()) -> list[dict]:
    """The focus, learn, practise and check steps for one focus topic within a time budget (minutes).
    Steps with nothing to offer are left out; unused time simply flows to the next step."""
    card_min, mark_min = time_estimates(conn)
    exclude_ids = set(exclude_ids)
    steps = [_step("focus", topic=focus, topic_title=topic_title(conn, focus), alternatives=alternatives)]

    # Learn prefers cards with two or more points or rows. One-point cards (definitions, single facts) are
    # bundled into one Quick-fire step instead: self-ticked, one after another, counted as a single step.
    multi, single = [], []
    for card_id in revise.learn_cards(conn, focus, exclude_ids, 200):
        card = get_card(conn, card_id)
        (multi if card and len(revise.check_units(card)) >= 2 else single).append(card_id)
    learn = _take([(i, card_min) for i in multi], budget, app_settings.get_all(conn)["learn_count"])
    if learn:
        steps.append(_step("learn", [i for i, _ in learn], sum(m for _, m in learn)))
        budget -= sum(m for _, m in learn)
    quick = _take([(i, card_min) for i in single], budget, QUICKFIRE_MAX)
    if quick:  # the bundle is one item: one step, one estimate
        steps.append(_step("quickfire", [i for i, _ in quick], sum(m for _, m in quick)))
        budget -= sum(m for _, m in quick)

    # Keep room for the topic check if it has anything to offer.
    words = command_word_scores(conn)
    generated = sorted(question_pool(conn, topic=focus, generated=True),
                       key=lambda r: (r["last_tried"] is not None, r["last_tried"] or "", r["id"]))
    check_pool = [(r["id"], r["marks"] * mark_min) for r in generated]
    reserve = sum(m for _, m in check_pool[:CHECK_MIN])

    real = question_pool(conn, topic=focus, generated=False)
    real.sort(key=lambda r: (r["last_tried"] is not None, words.get(r["word"], 0.5), r["last_tried"] or "", r["id"]))
    real_pool = [(r["id"], r["marks"] * mark_min) for r in _mix(real)]
    practise_budget = budget - reserve
    if practise_budget <= 0 < budget and real_pool:
        practise_budget = min(budget, real_pool[0][1])
    practise = _take(real_pool, practise_budget, 99)
    if practise:
        steps.append(_step("practise", [i for i, _ in practise], sum(m for _, m in practise)))
        budget -= sum(m for _, m in practise)

    check = _take(check_pool, budget, CHECK_MAX)
    if len(check) >= 1:
        steps.append(_step("check", [i for i, _ in check], sum(m for _, m in check)))
    return steps if len(steps) > 1 else []  # a focus with nothing to do is left out too


def alternatives_for(ranking: list[dict], focus: str) -> list[dict]:
    """The three next-best topics she can swap to."""
    return [{"topic": t["topic"], "title": t["title"]} for t in ranking if t["topic"] != focus][:3]


def build_block(conn, *, subject: str, minutes: int, now: datetime, focus: str = "", exclude_topics=(),
                exclude_ids=(), wrapup: bool = True) -> dict:
    """One block of a daily session. `focus` is a parent's choice for the week, if any."""
    card_min, _ = time_estimates(conn)
    exclude_ids = set(exclude_ids)
    steps, budget = [], float(minutes)

    warmup_max = app_settings.get_all(conn)["warmup_max"]
    warm = _take([(i, card_min) for i in revise.due_cards(conn, subject, warmup_max, now) if i not in exclude_ids],
                 budget, warmup_max)
    if warm:
        steps.append(_step("warmup", [i for i, _ in warm], sum(m for _, m in warm), subject=subject))
        budget -= sum(m for _, m in warm)

    ranking = rank_topics(conn, subject, now, exclude_topics)
    chosen = next((t["topic"] for t in ranking if t["topic"] == focus), "") or (ranking[0]["topic"] if ranking else "")
    if chosen:
        steps += focus_steps(conn, subject, chosen, alternatives_for(ranking, chosen), budget, now,
                             exclude_ids | {i for s in steps for i in s["items"]})
    if wrapup:
        steps.append(_step("wrapup"))
    return {"minutes": minutes, "subject": subject, "focus_topic": chosen, "steps": steps}


def build_year_end(conn, *, minutes: int, now: datetime) -> dict:
    """Year-end review: approved generated questions from taught topics, weakest topics first.
    No new generation. Topics with no attempts yet sort as 50%."""
    _, mark_min = time_estimates(conn)
    pool = question_pool(conn, generated=True)
    topic_scores = {r["topic"]: r["marks"] / r["mx"] for r in conn.execute(
        """SELECT q.topic, SUM(a.marks_awarded) AS marks, SUM(a.max_marks) AS mx
           FROM attempts a JOIN questions q ON q.id = a.question_id
           WHERE a.parent_attempt_id IS NULL AND a.marks_awarded IS NOT NULL GROUP BY q.topic""") if r["mx"]}
    pool.sort(key=lambda r: (topic_scores.get(r["topic"], YEAR_END_NEUTRAL), r["topic"],
                             r["last_tried"] is not None, r["last_tried"] or "", r["id"]))
    chosen = _take([(r["id"], r["marks"] * mark_min) for r in pool], float(minutes), 99)
    steps = []
    if chosen:
        steps.append(_step("review", [i for i, _ in chosen], sum(m for _, m in chosen)))
    steps.append(_step("wrapup"))
    return {"minutes": minutes, "subject": "", "focus_topic": "", "steps": steps}


# ---- Plain-words description --------------------------------------------------------------

def describe(conn, block: dict) -> str:
    """e.g. "30 minutes: warm-up with 6 Science cards → Separation techniques: 4 questions → quick topic check." """
    by_key = {s["key"]: s for s in block["steps"]}
    parts = []
    if "warmup" in by_key:
        n = len(by_key["warmup"]["items"])
        parts.append(f"warm-up with {n} {subject_label(block['subject'])} card{'s' if n != 1 else ''}")
    if "review" in by_key:
        n = len(by_key["review"]["items"])
        parts.append(f"{n} review question{'s' if n != 1 else ''} from topics she has learned, weakest first")
    if "focus" in by_key:
        title = by_key["focus"]["topic_title"]
        bits = []
        if "learn" in by_key:
            n = len(by_key["learn"]["items"])
            bits.append(f"{n} card{'s' if n != 1 else ''} to learn")
        if "quickfire" in by_key:
            bits.append("a quick-fire round")
        if "practise" in by_key:
            n = len(by_key["practise"]["items"])
            bits.append(f"{n} question{'s' if n != 1 else ''}")
        parts.append(f"{title}: {' and '.join(bits)}" if bits else title)
    if "check" in by_key:
        parts.append("quick topic check")
    if not parts:
        return "There is nothing to study yet. A parent needs to add questions or cards."
    return f"{block['minutes']} minutes: " + " → ".join(parts) + "."
