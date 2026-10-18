"""Made-up content for session tests. No real source material, no API calls."""

from datetime import datetime, timezone

from app import practice
from app.importer import import_questions


def now():
    return datetime.now(timezone.utc)


def mk(qid, topic, *, subject="science", source="assessment_book", marks=1, level="sec1", command="state"):
    """A points-mode question worth `marks`. Answering with its key terms (kw1 kw2 ...) earns full marks."""
    return {"id": qid, "subject": subject, "level": level, "topic": topic, "source": source,
            "question": f"Made-up question {qid}?", "marks": marks, "command_word": command, "marking_mode": "points",
            "mark_points": [{"id": f"MP{i}", "text": f"kw{i}", "key_terms": [f"kw{i}"]} for i in range(1, marks + 1)]}


def add_questions(conn, questions) -> None:
    import_questions(conn, questions)


def add_topic(conn, topic_id, subject="science", sort_order=0, likely_level="sec1", title=None) -> None:
    conn.execute("INSERT OR REPLACE INTO topics (id, subject, title, theme, likely_level, school_added, sort_order) "
                 "VALUES (?, ?, ?, '', ?, 0, ?)", (topic_id, subject, title or topic_id, likely_level, sort_order))
    conn.commit()


def answer(conn, question, good: bool) -> int:
    """Her first attempt at a question, marked by the fake marker (use the fake_marker fixture)."""
    text = " ".join(f"kw{i}" for i in range(1, question["marks"] + 1)) if good else "nothing relevant"
    return practice.submit_first_attempt(conn, question["id"], text)


def keys(block) -> list:
    return [s["key"] for s in block["steps"]]


def step(block, key):
    return next(s for s in block["steps"] if s["key"] == key)
