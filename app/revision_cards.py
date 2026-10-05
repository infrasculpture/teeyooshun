"""Approved revision cards: stored by id, with a version history (like questions)."""

import hashlib
import json

from app.db import utc_now


def import_cards(conn, cards) -> dict:
    """Insert new cards and update changed ones, matched by id. Returns counts.

    A changed card gets the next version number; earlier versions are kept in
    revision_card_versions.
    """
    counts = {"added": 0, "updated": 0, "unchanged": 0}
    now = utc_now()
    for card in cards:
        record_json = json.dumps(card, sort_keys=True, ensure_ascii=False)
        content_hash = hashlib.sha256(record_json.encode("utf-8")).hexdigest()
        row = conn.execute("SELECT content_hash, version FROM revision_cards WHERE id = ?", (card["id"],)).fetchone()
        values = (card["subject"], card["level"], card["topic"], card.get("section", ""),
                  card["answer"]["format"], card["recall"], record_json, content_hash)
        if row is None:
            version = 1
            conn.execute(
                """INSERT INTO revision_cards (subject, level, topic, section, format, recall, record_json,
                                               content_hash, id, version, imported_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values + (card["id"], version, now, now))
            counts["added"] += 1
        elif row["content_hash"] != content_hash:
            version = row["version"] + 1
            conn.execute(
                """UPDATE revision_cards SET subject = ?, level = ?, topic = ?, section = ?, format = ?, recall = ?,
                       record_json = ?, content_hash = ?, version = ?, updated_at = ? WHERE id = ?""",
                values + (version, now, card["id"]))
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1
            continue
        conn.execute("INSERT INTO revision_card_versions (card_id, version, record_json, content_hash, created_at) "
                     "VALUES (?, ?, ?, ?, ?)", (card["id"], version, record_json, content_hash, now))
    conn.commit()
    return counts


def get_card(conn, card_id: str):
    """The current version of a card, with its version number under "version"."""
    row = conn.execute("SELECT record_json, version FROM revision_cards WHERE id = ?", (card_id,)).fetchone()
    return {**json.loads(row["record_json"]), "version": row["version"]} if row else None


def get_card_version(conn, card_id: str, version: int):
    row = conn.execute("SELECT record_json FROM revision_card_versions WHERE card_id = ? AND version = ?",
                       (card_id, version)).fetchone()
    return {**json.loads(row["record_json"]), "version": version} if row else None
