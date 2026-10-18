"""Generated questions in the staging tables (docs/GENERATION_SPEC.md section 6).

They are staged like ingested questions, under one synthetic source per subject and level
("generated:<subject>:<level>", with a single file of type "generated"), so the review page,
approve and reject work unchanged. Ingestion, rebuilding and matching skip that source."""

import json
import re

from app.db import utc_now
from app.generate.plan import norm
from app.ingest.staging import GENERATED_PATH_PREFIX, GENERATED_TYPE
from app.revision_cards import get_card

FILENAME = "generated"
LIVE = ("pending", "approved", "rejected")  # a reviewed question still counts as "done" for its card version
_SLOT = re.compile(r"-q(\d+)$")


def generated_file_id(conn, subject: str, level: str) -> int:
    """The file row that holds this subject and level's generated questions, created on first use."""
    path = f"{GENERATED_PATH_PREFIX}{subject}:{level}"
    now = utc_now()
    row = conn.execute("SELECT id FROM sources WHERE path = ?", (path,)).fetchone()
    if row:
        source_id = row["id"]
    else:
        source_id = conn.execute(
            "INSERT INTO sources (path, slug, title, subject, level, config_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, '{}', ?, ?)",
            (path, f"generated-{subject}-{level}", f"Generated questions ({subject}, {level})", subject, level,
             now, now)).lastrowid
    file = conn.execute("SELECT id FROM source_files WHERE source_id = ? AND filename = ?",
                        (source_id, FILENAME)).fetchone()
    if file:
        file_id = file["id"]
    else:
        file_id = conn.execute(
            "INSERT INTO source_files (source_id, filename, sha256, version, material_type, unit, status, created_at) "
            "VALUES (?, ?, '', 1, ?, '', 'done', ?)", (source_id, FILENAME, GENERATED_TYPE, now)).lastrowid
    conn.commit()
    return file_id


def stage_question(conn, file_id: int, record: dict, warnings: list, raw: dict) -> int:
    """Stage one generated question as pending. Returns the staged item id. Does not commit."""
    now = utc_now()
    return conn.execute(
        """INSERT INTO staged_items (file_id, kind, item_id, unit, qnum, part, status, record_json,
                                     warnings_json, page_refs_json, raw_json, created_at, updated_at)
           VALUES (?, 'question', ?, '', '', '', 'pending', ?, ?, '[]', ?, ?, ?)""",
        (file_id, record["id"], json.dumps(record, ensure_ascii=False), json.dumps(warnings),
         json.dumps(raw, ensure_ascii=False), now, now)).lastrowid


def _generated_rows(conn):
    return conn.execute(
        """SELECT i.id, i.item_id, i.status, i.raw_json FROM staged_items i
           JOIN source_files f ON f.id = i.file_id
           WHERE f.material_type = ? AND i.kind = 'question' AND i.status != 'superseded' ORDER BY i.id""",
        (GENERATED_TYPE,)).fetchall()


def card_items(conn) -> dict:
    """{card id: [{"id", "status", "version", "slot"}]} for every generated question that is not superseded."""
    found = {}
    for r in _generated_rows(conn):
        raw = json.loads(r["raw_json"]) if r["raw_json"] else {}
        if not raw.get("origin_card_id"):
            continue
        slot = raw.get("slot") or (int(m.group(1)) if (m := _SLOT.search(r["item_id"])) else 0)
        found.setdefault(raw["origin_card_id"], []).append(
            {"id": r["id"], "status": r["status"], "version": raw.get("origin_card_version", 0), "slot": slot})
    return found


def has_questions_for_version(items: list, version: int) -> bool:
    """True if this card already has a generated question (pending, approved or rejected) for this version."""
    return any(i["version"] == version and i["status"] in LIVE for i in items)


def supersede_pending(conn, card_id: str) -> int:
    """Set aside the card's pending generated questions (before a regenerate). Approved ones stay."""
    ids = [i["id"] for i in card_items(conn).get(card_id, []) if i["status"] == "pending"]
    now = utc_now()
    for staged_id in ids:
        conn.execute("UPDATE staged_items SET status = 'superseded', updated_at = ? WHERE id = ?", (now, staged_id))
    conn.commit()
    return len(ids)


def existing_questions(conn, topic: str, skip_card: str = "") -> list:
    """[(id, normalised question text)] of approved and pending questions in the topic, for the duplicate check.
    Questions generated from skip_card are left out (a card is not a duplicate of itself)."""
    prefix = f"gen-{skip_card}-q" if skip_card else None
    found = {}
    for r in conn.execute("SELECT id, record_json FROM questions WHERE topic = ?", (topic,)):
        found[r["id"]] = json.loads(r["record_json"]).get("question", "")
    for r in conn.execute("SELECT item_id, record_json FROM staged_items WHERE kind = 'question' "
                          "AND status = 'pending'"):
        record = json.loads(r["record_json"])
        if record.get("topic") == topic:
            found.setdefault(r["item_id"], record.get("question", ""))
    return [(qid, norm(text)) for qid, text in found.items() if not (prefix and qid.startswith(prefix))]


def origin_of(conn, record: dict):
    """(card, stale) for a generated question: its current card, and whether the card has changed since."""
    card = get_card(conn, record.get("origin_card_id") or "")
    if card is None:
        return None, False
    return card, card["version"] != record.get("origin_card_version")
