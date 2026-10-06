"""'Help me remember' for revision cards (docs/REVISE_SPEC.md section 11).

Four short parts per card (why it matters, where the marks are, the common slip, a memory hook),
written by LIGHT_MODEL the first time and stored in card_help, one row per (card, card version).
A parent's edit is kept until the card changes. A failed call stores nothing, so it can be tried again.

Also the "I still don't get..." follow-up (not stored; limited) and a command-line run that writes
help for every approved card that lacks it:  python -m app.revise_help <topic id or source folder>
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

import anthropic

from app import revise
from app.config import get_settings
from app.db import connect, init_db, utc_now
from app.llm import LLMError, structured_call
from app.revision_cards import get_card

PARTS = ("why", "marks_in", "common_slip", "hook")
FOLLOWUP_LIMIT = 3
FOLLOWUP_MAX_CHARS = 300
STAMP = revise.STAMP

SYSTEM = (
    "You help a Singapore Secondary student aged 13 remember one revision card before she tries to recall it."
    " You are given only the card: its prompt, command word, answer points, key terms, examples and notes,"
    " plus the topic, subject and level. Add no facts beyond the card. Use the card's key terms exactly as written."
    " Write plain, encouraging language for a 13-year-old and speak to her as \"you\"."
    " Keep each part to two sentences or fewer."
    " why: why this matters, in one or two sentences of plain context."
    " marks_in: which points and key terms an examiner looks for, and what would be padding."
    " common_slip: one likely mistake, such as a reversal, a vague word instead of the key term,"
    " or an answer too short or too long for the command word."
    " hook: one way to remember it (an acronym, chunking, a short story, a cause-and-effect chain, or a link to"
    " the command word). If the card has only one point, leave hook empty."
    " Return only the JSON."
)

SCHEMA = {
    "type": "object",
    "properties": {p: {"type": "string"} for p in PARTS},
    "required": list(PARTS),
    "additionalProperties": False,
}

FOLLOWUP_SYSTEM = (
    "You help a Singapore Secondary student aged 13 who is revising one card. You are given the card, the help she"
    " has already been shown, and her question. Answer in two or three short sentences. Add no facts beyond the card,"
    " use the card's key terms exactly as written, write plain encouraging language and speak to her as \"you\"."
    " If the card does not answer her question, say so and suggest she ask a parent or teacher."
    " Return only the JSON."
)

FOLLOWUP_SCHEMA = {
    "type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"],
    "additionalProperties": False,
}


class FollowupLimit(Exception):
    """She has used her follow-up questions for this card."""


def _now(now=None) -> datetime:
    return now or datetime.now(timezone.utc)


# ---- Stored help ---------------------------------------------------------------------

def get(conn, card_id: str, version: int):
    """The stored help for this card version, or None. Keys: why, marks_in, common_slip, hook, edited_by_parent."""
    row = conn.execute("SELECT help_json, edited_by_parent FROM card_help WHERE card_id = ? AND card_version = ?",
                       (card_id, version)).fetchone()
    if row is None:
        return None
    help_ = json.loads(row["help_json"])
    return {**{p: help_.get(p, "") for p in PARTS}, "edited_by_parent": bool(row["edited_by_parent"])}


def _clean(output: dict, card: dict):
    """Tidy the model's reply. None if a required part is missing. One-point cards get no hook."""
    help_ = {p: (output.get(p) or "").strip() for p in PARTS}
    if not (help_["why"] and help_["marks_in"] and help_["common_slip"]):
        return None
    if len(revise.check_units(card)) <= 1:
        help_["hook"] = ""
    return help_


def _topic_title(conn, topic_id: str) -> str:
    row = conn.execute("SELECT title FROM topics WHERE id = ?", (topic_id,)).fetchone()
    return row["title"] if row else topic_id


def _card_block(conn, card: dict) -> str:
    shown = {"subject": card["subject"], "level": card["level"], "topic": _topic_title(conn, card["topic"]),
             "command_word": card.get("command_word", ""), "prompt": card.get("prompt", ""),
             "answer": card.get("answer"), "examples": card.get("examples") or [], "notes": card.get("notes", "")}
    return "<card>\n" + json.dumps(shown, sort_keys=True, ensure_ascii=False, indent=1) + "\n</card>"


def generate(conn, card: dict, *, client=None):
    """The help for this card version: from the database, or written once and stored.

    Returns None if the API call fails or the reply is unusable (nothing is stored, so it can be tried again).
    """
    version = card.get("version", 1)
    cached = get(conn, card["id"], version)
    if cached:
        return cached
    help_, info = _write(conn, card, client=client)
    if help_ is None:
        return None
    conn.execute(
        """INSERT OR IGNORE INTO card_help (card_id, card_version, help_json, model, api_call_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (card["id"], version, json.dumps(help_, ensure_ascii=False), info["model"], info["api_call_id"], utc_now()))
    conn.commit()
    return get(conn, card["id"], version)


def regenerate(conn, card: dict, *, client=None):
    """Write the help again and replace the stored text, unless a parent has edited it (then it stays).

    Returns the help now stored, or None if the call failed (any earlier help is left as it was).
    """
    version = card.get("version", 1)
    existing = get(conn, card["id"], version)
    if existing and existing["edited_by_parent"]:
        return existing
    help_, info = _write(conn, card, client=client)
    if help_ is None:
        return None
    conn.execute(
        """INSERT INTO card_help (card_id, card_version, help_json, model, api_call_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (card_id, card_version) DO UPDATE SET help_json = excluded.help_json,
               model = excluded.model, api_call_id = excluded.api_call_id, created_at = excluded.created_at
           WHERE edited_by_parent = 0""",
        (card["id"], version, json.dumps(help_, ensure_ascii=False), info["model"], info["api_call_id"], utc_now()))
    conn.commit()
    return get(conn, card["id"], version)


def _write(conn, card: dict, *, client=None):
    """One LIGHT_MODEL call. Returns (help, info), or (None, None) if it failed."""
    settings = get_settings()
    content = [{"type": "text", "text": _card_block(conn, card)}]
    try:
        output, info = structured_call(
            conn, model=settings.light_model, effort=settings.light_effort,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            content=content, schema=SCHEMA, purpose="card_help", question_id=card["id"],
            fallback=settings.refusal_fallback, max_tokens=2000, client=client)
    except (LLMError, anthropic.AnthropicError):
        return None, None
    help_ = _clean(output, card)
    return (help_, info) if help_ else (None, None)


def save_edit(conn, card: dict, values: dict) -> bool:
    """A parent's edit of the help for the card's current version. False if a required part is empty."""
    help_ = {p: (values.get(p) or "").strip() for p in PARTS}
    if not (help_["why"] and help_["marks_in"] and help_["common_slip"]):
        return False
    conn.execute(
        """INSERT INTO card_help (card_id, card_version, help_json, model, edited_by_parent, created_at)
           VALUES (?, ?, ?, 'parent', 1, ?)
           ON CONFLICT (card_id, card_version) DO UPDATE SET help_json = excluded.help_json, edited_by_parent = 1""",
        (card["id"], card.get("version", 1), json.dumps(help_, ensure_ascii=False), utc_now()))
    conn.commit()
    return True


# ---- Views, follow-ups and flags ------------------------------------------------------------

def record_view(conn, card: dict, set_id=None, now=None) -> int:
    """Note that she opened the help. This is what marks a later recall attempt 'with help'."""
    cur = conn.execute("INSERT INTO help_views (card_id, card_version, set_id, opened_at) VALUES (?, ?, ?, ?)",
                       (card["id"], card.get("version", 1), set_id, _now(now).strftime(STAMP)))
    conn.commit()
    return cur.lastrowid


def _scope(card_id: str, set_id, now):
    """SQL for the views that share a follow-up allowance: this card in this set, or on its own page today."""
    if set_id is not None:
        return "card_id = ? AND set_id = ?", [card_id, set_id]
    return ("card_id = ? AND set_id IS NULL AND opened_at >= ?",
            [card_id, (_now(now) - timedelta(hours=24)).strftime(STAMP)])


def followups_left(conn, card_id: str, set_id=None, now=None) -> int:
    where, args = _scope(card_id, set_id, now)
    used = conn.execute(f"SELECT COALESCE(SUM(followups_used), 0) FROM help_views WHERE {where}", args).fetchone()[0]
    return max(0, FOLLOWUP_LIMIT - used)


def followup(conn, card: dict, question: str, *, set_id=None, now=None, client=None):
    """A short reply to 'I still don't get...', using the card and its stored help. Not stored.

    Raises FollowupLimit when the allowance is used up. Returns None if there is no help yet, the question
    is empty or the call fails (a failed call doesn't use up an allowance).
    """
    question = (question or "").strip()[:FOLLOWUP_MAX_CHARS]
    help_ = get(conn, card["id"], card.get("version", 1))
    if not question or help_ is None:
        return None
    if followups_left(conn, card["id"], set_id, now) <= 0:
        raise FollowupLimit()
    settings = get_settings()
    shown_help = {p: help_[p] for p in PARTS}
    content = [
        {"type": "text", "text": _card_block(conn, card) + "\n<help_shown>\n"
         + json.dumps(shown_help, sort_keys=True, ensure_ascii=False, indent=1) + "\n</help_shown>",
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "Her question: " + question},
    ]
    try:
        output, _ = structured_call(
            conn, model=settings.light_model, effort=settings.light_effort,
            system=[{"type": "text", "text": FOLLOWUP_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            content=content, schema=FOLLOWUP_SCHEMA, purpose="card_help_followup", question_id=card["id"],
            fallback=settings.refusal_fallback, max_tokens=1000, client=client)
    except (LLMError, anthropic.AnthropicError):
        return None
    reply = (output.get("reply") or "").strip()
    if not reply:
        return None
    where, args = _scope(card["id"], set_id, now)
    row = conn.execute(f"SELECT id FROM help_views WHERE {where} ORDER BY id DESC LIMIT 1", args).fetchone()
    view_id = row["id"] if row else record_view(conn, card, set_id, now)
    conn.execute("UPDATE help_views SET followups_used = followups_used + 1 WHERE id = ?", (view_id,))
    conn.commit()
    return reply


def add_flag(conn, card: dict, note: str, flagged_by="student", now=None) -> None:
    conn.execute("INSERT INTO help_flags (card_id, card_version, note, flagged_by, created_at) VALUES (?, ?, ?, ?, ?)",
                 (card["id"], card.get("version", 1), (note or "").strip()[:1000], flagged_by,
                  _now(now).strftime(STAMP)))
    conn.commit()


def open_flags(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM help_flags WHERE resolved_at IS NULL ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def resolve_flags(conn, card_id: str) -> None:
    conn.execute("UPDATE help_flags SET resolved_at = ? WHERE card_id = ? AND resolved_at IS NULL",
                 (utc_now(), card_id))
    conn.commit()


# ---- Command line: help for every approved card that lacks it -------------------------------------

def cards_for(conn, target: str) -> list[str]:
    """Approved card ids for a topic id, or for a source folder (its path or slug), in book order."""
    rows = conn.execute("SELECT id FROM revision_cards WHERE topic = ? ORDER BY sort_key, id", (target,)).fetchall()
    if rows:
        return [r["id"] for r in rows]
    rows = conn.execute(
        """SELECT DISTINCT c.id, c.sort_key FROM revision_cards c
           JOIN staged_items i ON i.item_id = c.id AND i.kind = 'card' AND i.status = 'approved'
           JOIN source_files f ON f.id = i.file_id JOIN sources s ON s.id = f.source_id
           WHERE s.path = ? OR s.slug = ? OR s.path LIKE ? ORDER BY c.sort_key, c.id""",
        (target, target, "%/" + target.strip("/"))).fetchall()
    return [r["id"] for r in rows]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Write 'Help me remember' text for approved cards that lack it.")
    parser.add_argument("target", help="a topic id (e.g. a topic from the syllabus map) or a source folder")
    args = parser.parse_args(argv)

    init_db()
    conn = connect()
    try:
        ids = cards_for(conn, args.target)
        if not ids:
            print(f"No approved cards found for '{args.target}'.", file=sys.stderr)
            return 1
        made = skipped = failed = 0
        for card_id in ids:
            card = get_card(conn, card_id)
            if get(conn, card_id, card["version"]):
                skipped += 1
            elif generate(conn, card):
                made += 1
            else:
                failed += 1
                print(f"{card_id}: no help written (the call failed); run again to retry", file=sys.stderr)
        print(f"{len(ids)} cards: {made} written, {skipped} already had help, {failed} failed")
        return 1 if failed else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
