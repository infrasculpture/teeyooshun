"""Approved revision cards: stored by id, with a version history (like questions)."""

import hashlib
import json

from app.db import utc_now


def import_cards(conn, cards, sort_keys=None) -> dict:
    """Insert new cards and update changed ones, matched by id. Returns counts.

    A changed card gets the next version number; earlier versions are kept in
    revision_card_versions. sort_keys: {card id: book-order key}, stored whenever given
    (a card with none sorts by its id).
    """
    counts = {"added": 0, "updated": 0, "unchanged": 0}
    now = utc_now()
    for card in cards:
        counts[_import_one(conn, card, now)] += 1
        key = (sort_keys or {}).get(card["id"])
        if key:
            conn.execute("UPDATE revision_cards SET sort_key = ? WHERE id = ?", (key, card["id"]))
        else:
            conn.execute("UPDATE revision_cards SET sort_key = id WHERE id = ? AND sort_key = ''", (card["id"],))
    conn.commit()
    return counts


def _import_one(conn, card: dict, now: str) -> str:
    """Store one card. Returns "added", "updated" or "unchanged"."""
    record_json = json.dumps(card, sort_keys=True, ensure_ascii=False)
    content_hash = hashlib.sha256(record_json.encode("utf-8")).hexdigest()
    row = conn.execute("SELECT content_hash, version FROM revision_cards WHERE id = ?", (card["id"],)).fetchone()
    values = (card["subject"], card["level"], card["topic"], card.get("section", ""),
              card["answer"]["format"], card["recall"], record_json, content_hash)
    if row is None:
        version, outcome = 1, "added"
        conn.execute(
            """INSERT INTO revision_cards (subject, level, topic, section, format, recall, record_json,
                                           content_hash, id, version, imported_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values + (card["id"], version, now, now))
    elif row["content_hash"] != content_hash:
        version, outcome = row["version"] + 1, "updated"
        conn.execute(
            """UPDATE revision_cards SET subject = ?, level = ?, topic = ?, section = ?, format = ?, recall = ?,
                   record_json = ?, content_hash = ?, version = ?, updated_at = ? WHERE id = ?""",
            values + (version, now, card["id"]))
    else:
        return "unchanged"
    conn.execute("INSERT INTO revision_card_versions (card_id, version, record_json, content_hash, created_at) "
                 "VALUES (?, ?, ?, ?, ?)", (card["id"], version, record_json, content_hash, now))
    return outcome


def get_card(conn, card_id: str):
    """The current version of a card, with its version number under "version"."""
    row = conn.execute("SELECT record_json, version FROM revision_cards WHERE id = ?", (card_id,)).fetchone()
    return {**json.loads(row["record_json"]), "version": row["version"]} if row else None


def get_card_version(conn, card_id: str, version: int):
    row = conn.execute("SELECT record_json FROM revision_card_versions WHERE card_id = ? AND version = ?",
                       (card_id, version)).fetchone()
    return {**json.loads(row["record_json"]), "version": version} if row else None
