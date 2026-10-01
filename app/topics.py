"""The topic tree: docs/topics.yaml loaded into the topics table.

Loaded on every start (and by the ingest command). Topics are added or updated
by id and never deleted, because questions and attempts refer to them.
"""

from pathlib import Path

import yaml

TOPICS_FILE = Path(__file__).resolve().parent.parent / "docs" / "topics.yaml"


class TopicsError(ValueError):
    pass


def read_topics(path=None) -> list[dict]:
    """Parse and check topics.yaml. Returns one dict per topic, in file order."""
    path = Path(path or TOPICS_FILE)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise TopicsError(f"{path}: file not found") from None
    except yaml.YAMLError as e:
        raise TopicsError(f"{path}: not valid YAML: {e}") from None
    if not isinstance(data, dict):
        raise TopicsError(f"{path}: top level must map each subject to a list of topics")

    topics, seen, problems = [], set(), []
    for subject, entries in data.items():
        if not isinstance(entries, list):
            problems.append(f"subject '{subject}' must have a list of topics")
            continue
        for order, t in enumerate(entries):
            if not isinstance(t, dict) or not t.get("id") or not t.get("title"):
                problems.append(f"subject '{subject}' entry {order + 1} needs an id and a title")
                continue
            if t["id"] in seen:
                problems.append(f"duplicate topic id '{t['id']}'")
            seen.add(t["id"])
            topics.append({
                "id": str(t["id"]), "subject": str(subject), "title": str(t["title"]),
                "theme": str(t.get("theme", "")), "likely_level": str(t.get("likely_level", "")),
                "school_added": 1 if t.get("school_added") else 0, "sort_order": order,
            })
    if problems:
        raise TopicsError(f"{path}: " + "; ".join(problems))
    return topics


def load_topics(conn, path=None) -> int:
    """Add or update every topic from topics.yaml. Returns how many were loaded."""
    topics = read_topics(path)
    for t in topics:
        conn.execute(
            """INSERT INTO topics (id, subject, title, theme, likely_level, school_added, sort_order)
               VALUES (:id, :subject, :title, :theme, :likely_level, :school_added, :sort_order)
               ON CONFLICT (id) DO UPDATE SET
                   subject = excluded.subject, title = excluded.title, theme = excluded.theme,
                   likely_level = excluded.likely_level, school_added = excluded.school_added,
                   sort_order = excluded.sort_order""",
            t,
        )
    conn.commit()
    return len(topics)


def topic_ids(conn, subject=None) -> set:
    if subject:
        rows = conn.execute("SELECT id FROM topics WHERE subject = ?", (subject,)).fetchall()
    else:
        rows = conn.execute("SELECT id FROM topics").fetchall()
    return {r["id"] for r in rows}


def topics_for_subject(conn, subject: str) -> list:
    return conn.execute("SELECT * FROM topics WHERE subject = ? ORDER BY sort_order", (subject,)).fetchall()


def unknown_question_topics(conn) -> list[tuple]:
    """(question id, topic) for questions whose topic isn't in the topic tree."""
    rows = conn.execute(
        """SELECT q.id, q.topic FROM questions q LEFT JOIN topics t ON t.id = q.topic
           WHERE t.id IS NULL AND q.topic != 'unassigned' ORDER BY q.id"""
    ).fetchall()
    return [(r["id"], r["topic"]) for r in rows]
