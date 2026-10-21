"""The student's approved revision cards for a topic, as context for notes-aware checks.

Used by Revise typed checks and generated questions: a correct point that isn't on the card being
checked can still be credited if another approved card in the same topic supports it ("in_notes").
The rendered text depends only on the topic's cards, so it is identical for every check in the
topic and the prompt cache is shared across them.
"""

import json

MAX_CARDS = 80  # keeps the cached block a sensible size for a very large topic


def topic_notes(conn, topic: str) -> list[dict]:
    """[{"card_id", "prompt", "points"}] for the topic's approved cards, in book order."""
    from app.revise import check_units  # imported here: revise imports the marker, which imports this

    rows = conn.execute("SELECT id, record_json FROM revision_cards WHERE topic = ? ORDER BY sort_key, id LIMIT ?",
                        (topic, MAX_CARDS)).fetchall()
    notes = []
    for row in rows:
        card = json.loads(row["record_json"])
        points = [u["text"] + (" (" + "; ".join(u["detail"]) + ")" if u["detail"] else "")
                  for u in check_units(card)]
        notes.append({"card_id": row["id"], "prompt": card.get("prompt", ""), "points": points})
    return notes


def render_notes(notes: list[dict]) -> str:
    """The notes as the model sees them (sorted keys, no times or counters, so it caches)."""
    body = json.dumps({"cards": notes}, sort_keys=True, ensure_ascii=False, indent=1)
    return f"<topic_notes>\n{body}\n</topic_notes>"


def classification_totals(conn, start: str, end: str) -> dict:
    """How often each kind of point was logged between two timestamps (start inclusive, end exclusive).

    Reads the counts stored with every notes-aware result: Revise typed checks (recall_attempts)
    and generated-question attempts (attempts). For the parent dashboard.
    """
    totals = {"on_card": 0, "in_notes": 0, "unverified": 0, "incorrect": 0}
    for table in ("recall_attempts", "attempts"):
        for row in conn.execute(
                f"SELECT result_json FROM {table} WHERE created_at >= ? AND created_at < ? "
                "AND result_json LIKE '%classification_counts%'", (start, end)):
            counts = (json.loads(row["result_json"]).get("detail") or {}).get("classification_counts") or {}
            for kind in totals:
                totals[kind] += int(counts.get(kind) or 0)
    return totals
