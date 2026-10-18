"""The student practice loop: choosing questions, first attempts, rewrites,
skips, events and first-attempt scores.

Rules (enforced here, not just hidden in the page):
- A failed marking call saves nothing, so a failure can never become her first attempt.
- A rewrite is allowed once, only on a first attempt that was marked, is worth
  2+ marks, lost marks, and hasn't been skipped.
- Only first attempts (parent_attempt_id IS NULL) count towards scores.
- Events are only ever added, never changed or deleted.
"""

import json
import re
import sqlite3
import time

from app.db import utc_now
from app.importer import get_question, get_question_version
from app.llm import LLMError
from app.marking import marker
from app.marking.mcq import mark_mcq
from app.topics import TAUGHT_FILTER

MAX_ANSWER_CHARS = 3000
MAX_NOTE_CHARS = 1000
MAX_SECONDS = 6 * 3600  # longer than this is a page left open, not time spent

EVENTS = ("rewrite_skipped", "parent_agree", "parent_disagree", "marking_looks_wrong")


class MarkingFailed(Exception):
    """The marking call gave no result. Nothing was saved."""


class NotAllowed(Exception):
    """The action isn't allowed for this attempt (e.g. a second rewrite)."""


# ---- Labels --------------------------------------------------------------------

_SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "its", "of", "on", "or", "the", "to", "within"}
_CODE_PART = re.compile(r"^[a-z]{0,3}\d+[a-z]?$")  # 01, u1, tq1, s1


def topic_label(topic_id: str) -> str:
    """'sci-01-scientific-endeavour' -> 'Scientific Endeavour' (until the topic tree exists)."""
    parts = topic_id.split("-")[1:] or [topic_id]
    while len(parts) > 1 and _CODE_PART.match(parts[0]):
        parts = parts[1:]
    words = [w if (i and w in _SMALL_WORDS) else w.capitalize() for i, w in enumerate(parts)]
    return " ".join(words)


def subject_label(subject: str) -> str:
    return subject.replace("_", " ").title()


# ---- Answers and timing -----------------------------------------------------------

def clean_answer(text: str) -> str:
    """Strip the answer and check its length. Raises ValueError with a message for her."""
    answer = (text or "").strip()
    if not answer:
        raise ValueError("Write an answer before submitting.")
    if len(answer) > MAX_ANSWER_CHARS:
        raise ValueError(f"That answer is too long (over {MAX_ANSWER_CHARS} characters). Try to be more concise.")
    return answer


def seconds_since(shown_at, now=None):
    """Seconds between the page appearing and submitting, or None if it doesn't make sense."""
    try:
        shown = int(shown_at)
    except (TypeError, ValueError):
        return None
    now = time.time() if now is None else now
    seconds = int(now) - shown
    return seconds if 0 <= seconds <= MAX_SECONDS else None


# ---- Questions -------------------------------------------------------------------

# Kept in the bank but left out of typed practice: questions answered by drawing or labelling
# a diagram, and worked examples (their page shows the solution). Table questions are typed.
# Topics that aren't taught yet (see app.topics.TAUGHT_FILTER) are left out everywhere, in Practise and Revise.
PRACTICE_FILTER = ("q.answer_format != 'draw_or_label' AND q.worked_example = 0 "
                   f"AND q.topic {TAUGHT_FILTER}")


def list_subjects(conn) -> list[dict]:
    rows = conn.execute(f"SELECT q.subject, COUNT(*) AS n FROM questions q WHERE {PRACTICE_FILTER} "
                        "GROUP BY q.subject ORDER BY q.subject").fetchall()
    scores = first_attempt_scores(conn)
    return [{"subject": r["subject"], "label": subject_label(r["subject"]), "count": r["n"],
             "score": scores.get(r["subject"])} for r in rows]


def list_topics(conn, subject: str) -> list[dict]:
    """Topics with practice questions, titled from the topic tree (or from the id if unknown)."""
    rows = conn.execute(
        f"""SELECT q.topic, t.title, COUNT(*) AS n
            FROM questions q LEFT JOIN topics t ON t.id = q.topic
            WHERE q.subject = ? AND {PRACTICE_FILTER}
            GROUP BY q.topic ORDER BY COALESCE(t.sort_order, 999), q.topic""",
        (subject,),
    ).fetchall()
    return [{"topic": r["topic"], "label": r["title"] or topic_label(r["topic"]), "count": r["n"]} for r in rows]


def next_question_id(conn, subject: str, topic=None):
    """Questions she hasn't tried come first, then the ones she tried longest ago."""
    row = conn.execute(
        f"""SELECT q.id, MAX(a.created_at) AS last_tried
           FROM questions q
           LEFT JOIN attempts a ON a.question_id = q.id AND a.parent_attempt_id IS NULL
           WHERE q.subject = ? AND (? IS NULL OR q.topic = ?) AND {PRACTICE_FILTER}
           GROUP BY q.id
           ORDER BY last_tried IS NOT NULL, last_tried, q.id
           LIMIT 1""",
        (subject, topic, topic),
    ).fetchone()
    return row["id"] if row else None


def question_for_student(question: dict) -> dict:
    """Only what the student may see before answering. Never the mark scheme."""
    view = {
        "id": question["id"],
        "subject": question["subject"],
        "topic": question["topic"],
        "context": question.get("context", ""),
        "question": question["question"],
        "marks": question["marks"],
        "command_word": question.get("command_word", ""),
        "options": None,
        "label": question.get("question_label", ""),  # e.g. Q5(b)(ii), to find it on the page image
        # How many page images to show (question pages only); worked examples show none.
        "images": 0 if question.get("worked_example") else len(question.get("page_images") or []),
    }
    if question["marking_mode"] == "mcq":  # labels and text only, never correct_option
        view["options"] = [{"label": o["label"], "text": o["text"]} for o in question["options"]]
    return view


def clean_choice(question: dict, choice: str) -> str:
    """The chosen MCQ option label. Raises ValueError with a message for her."""
    choice = (choice or "").strip()
    if choice not in {o["label"] for o in question["options"]}:
        raise ValueError("Pick one of the options before submitting.")
    return choice


# ---- Attempts --------------------------------------------------------------------

def get_attempt(conn, attempt_id: int):
    return conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()


def question_for_attempt(conn, attempt) -> dict:
    """The question exactly as she answered it."""
    return get_question_version(conn, attempt["question_id"], attempt["question_version"])


def _mark_and_save(conn, question, answer, parent_attempt_id, seconds_taken) -> int:
    if question["marking_mode"] == "mcq":
        result = mark_mcq(question, answer)  # code only, no API call
    else:
        try:
            result = marker.mark_answer(conn, question, answer)
        except LLMError as e:  # fatal errors (bad key, bad model name): still nothing saved
            raise MarkingFailed(str(e)) from None
    if result["marks_awarded"] is None:
        raise MarkingFailed(result.get("error") or "marking failed")
    try:
        return marker.save_attempt(conn, question, answer, result,
                                   parent_attempt_id=parent_attempt_id, seconds_taken=seconds_taken)
    except sqlite3.IntegrityError:  # a second rewrite racing the first
        conn.rollback()
        raise NotAllowed("This answer has already been rewritten.") from None


def submit_first_attempt(conn, question_id: str, answer: str, seconds_taken=None) -> int:
    question = get_question(conn, question_id)
    if question is None:
        raise NotAllowed("Unknown question.")
    return _mark_and_save(conn, question, answer, None, seconds_taken)


def get_rewrite(conn, attempt_id: int):
    return conn.execute("SELECT * FROM attempts WHERE parent_attempt_id = ?", (attempt_id,)).fetchone()


def _has_event(conn, attempt_id: int, event: str) -> bool:
    return conn.execute("SELECT 1 FROM attempt_events WHERE attempt_id = ? AND event = ?",
                        (attempt_id, event)).fetchone() is not None


def rewrite_status(conn, attempt) -> str:
    """not_needed / available / rewritten / skipped."""
    if attempt["parent_attempt_id"] is not None:
        return "not_needed"  # this is itself a rewrite: one rewrite only
    if attempt["marks_awarded"] is None or attempt["max_marks"] < 2:
        return "not_needed"
    if json.loads(attempt["result_json"]).get("mode") == "mcq":
        return "not_needed"
    if attempt["marks_awarded"] >= attempt["max_marks"]:
        return "not_needed"
    if get_rewrite(conn, attempt["id"]) is not None:
        return "rewritten"
    if _has_event(conn, attempt["id"], "rewrite_skipped"):
        return "skipped"
    return "available"


def submit_rewrite(conn, first_attempt_id: int, answer: str, seconds_taken=None) -> int:
    first = get_attempt(conn, first_attempt_id)
    if first is None:
        raise NotAllowed("Unknown attempt.")
    if rewrite_status(conn, first) != "available":
        raise NotAllowed("A rewrite isn't available for this answer.")
    question = question_for_attempt(conn, first)  # same version she first answered
    return _mark_and_save(conn, question, answer, first_attempt_id, seconds_taken)


def skip_rewrite(conn, attempt_id: int) -> None:
    attempt = get_attempt(conn, attempt_id)
    if attempt is None or rewrite_status(conn, attempt) != "available":
        raise NotAllowed("There's no rewrite to skip for this answer.")
    add_event(conn, attempt_id, "rewrite_skipped", "student")


# ---- Events ----------------------------------------------------------------------

def add_event(conn, attempt_id: int, event: str, actor: str, note: str = "") -> None:
    if event not in EVENTS:
        raise ValueError(f"unknown event {event}")
    if get_attempt(conn, attempt_id) is None:
        raise NotAllowed("Unknown attempt.")
    try:
        conn.execute(
            "INSERT INTO attempt_events (attempt_id, event, actor, note, created_at) VALUES (?, ?, ?, ?, ?)",
            (attempt_id, event, actor, (note or "").strip()[:MAX_NOTE_CHARS], utc_now()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise NotAllowed("Already recorded.") from None


def events_for(conn, attempt_id: int) -> list:
    return conn.execute("SELECT * FROM attempt_events WHERE attempt_id = ? ORDER BY id",
                        (attempt_id,)).fetchall()


def parent_verdict(conn, attempt_id: int):
    """The latest agree/disagree row, or None."""
    return conn.execute(
        """SELECT * FROM attempt_events
           WHERE attempt_id = ? AND event IN ('parent_agree', 'parent_disagree')
           ORDER BY id DESC LIMIT 1""",
        (attempt_id,),
    ).fetchone()


# ---- Scores ----------------------------------------------------------------------

def first_attempt_scores(conn) -> dict:
    """{subject: {"marks": x, "max": y, "pct": z}} from first attempts only."""
    rows = conn.execute(
        """SELECT q.subject, SUM(a.marks_awarded) AS marks, SUM(a.max_marks) AS max
           FROM attempts a JOIN questions q ON q.id = a.question_id
           WHERE a.parent_attempt_id IS NULL AND a.marks_awarded IS NOT NULL
           GROUP BY q.subject""",
    ).fetchall()
    return {r["subject"]: {"marks": r["marks"], "max": r["max"],
                           "pct": round(100 * r["marks"] / r["max"]) if r["max"] else 0} for r in rows}


def attempt_result(attempt) -> dict:
    return json.loads(attempt["result_json"])
