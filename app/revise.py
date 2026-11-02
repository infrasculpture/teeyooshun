"""Revise mode rules (docs/REVISE_SPEC.md): check units per card format, typed checks through
the points engine, self-tick, the five-box spacing, building a set of cards, and the
underline step. No web code here.

A card is a record from revision_cards in the shape of docs/card_format_example.json.
"""

import json
import re
from datetime import datetime, timedelta, timezone

from app.llm import LLMError
from app.marking import marker
from app.revision_cards import get_card
from app.topics import taught_filter

BOXES = 5
STAMP = "%Y-%m-%dT%H:%M:%SZ"


class CheckFailed(Exception):
    """The typed check gave no result. Nothing was saved."""


class NotAllowed(Exception):
    """The action isn't allowed (e.g. a second attempt at a card in the same set)."""


def _now(now=None) -> datetime:
    return now or datetime.now(timezone.utc)


# ---- Check units: one "point" per unit (spec section 4) -----------------------------------

def check_units(card: dict) -> list[dict]:
    """The card's answer as units that are each covered or not.

    bullets / list: each top-level point; grouped: each group heading; table: each row;
    timeline: each event; image: the whole card.
    Each unit: {"id", "text", "detail": [sub lines], "key_terms", "any_of": bool}.
    """
    answer = card.get("answer") or {}
    fmt = answer.get("format")
    units = []
    if fmt in ("bullets", "list"):
        for p in answer.get("points") or []:
            units.append({"text": p.get("text", ""), "detail": [s.get("text", "") for s in p.get("sub") or []],
                          "key_terms": list(p.get("key_terms") or [])})
    elif fmt == "grouped":
        for g in answer.get("groups") or []:
            # The heading idea, or any one point under it, counts; the rest is the stretch target.
            units.append({"text": g.get("heading", ""), "detail": [p.get("text", "") for p in g.get("points") or []],
                          "key_terms": [], "any_of": True})
    elif fmt == "table":
        table = answer.get("table") or {}
        headers, terms = table.get("headers") or [], answer.get("key_terms") or []
        for row in table.get("rows") or []:
            cells = [f"{h}: {c}" if h else str(c) for h, c in zip(headers + [""] * len(row), row)]
            joined = " ".join(str(c) for c in row).lower()
            units.append({"text": "; ".join(cells), "detail": [], "cells": row,
                          "key_terms": [t for t in terms if t.lower() in joined]})
    elif fmt == "timeline":
        for e in answer.get("events") or []:
            units.append({"text": f"{e.get('date', '')}: {e.get('text', '')}", "detail": [],
                          "key_terms": list(e.get("key_terms") or [])})
    elif fmt == "image":
        units.append({"text": answer.get("alt") or "The picture", "detail": [], "key_terms": []})
    for i, unit in enumerate(units, 1):
        unit["id"] = f"P{i}"
        unit.setdefault("any_of", False)
    return units


def is_self_tick_only(card: dict) -> bool:
    return card.get("recall") == "self_tick_only" or (card.get("answer") or {}).get("format") == "image"


def recalls_by_self_tick(revise_set: dict, card: dict) -> bool:
    """Self-tick only: an image card, or any card in a Quick-fire set (no typed check, no API call)."""
    return bool(revise_set.get("self_tick")) or is_self_tick_only(card)


def card_as_question(card: dict, units: list) -> dict:
    """The card as a points-mode question for the marking engine: one mark point per check unit."""
    mark_points = []
    for u in units:
        if u["any_of"]:
            point = {"id": u["id"], "text": f"{u['text']} (naming this idea, or any one of the points listed as "
                                             f"alternatives, earns this mark)"}
            if u["detail"]:
                point["alternatives"] = u["detail"]
        else:
            text = u["text"] + (" (" + "; ".join(u["detail"]) + ")" if u["detail"] else "")
            point = {"id": u["id"], "text": text}
            if u["key_terms"]:
                point["key_terms"] = u["key_terms"]
        mark_points.append(point)
    return {
        "id": card["id"], "subject": card["subject"], "level": card["level"], "topic": card["topic"],
        "source": "assessment_book", "context": f"Revision card: {card.get('section', '')}".strip(),
        "question": card.get("prompt", ""), "marks": len(units), "command_word": card.get("command_word", ""),
        "marking_mode": "points", "mark_points": mark_points,
        "notes_check": True,  # the recall rubric (app.marking.prompts), not exam marking
        "card": card,         # the whole card (examples and notes too) goes to the marker
    }


def typed_check(conn, card: dict, text: str, client=None, *, clarification="", purpose="recall") -> dict:
    """Check a typed recall against the whole card with RECALL_MODEL and the recall rubric, through the points
    engine (quote check, token logging). Raises CheckFailed if the call gives no result.

    clarification: her own words from an Ask-about-this chat (Mark again), added as context."""
    units = check_units(card)
    try:
        result = marker.mark_answer(conn, card_as_question(card, units), text, purpose=purpose,
                                    client=client, clarification=clarification)
    except LLMError as e:
        raise CheckFailed(str(e)) from None
    if result["marks_awarded"] is None:
        raise CheckFailed(result.get("error") or "the check failed")
    points = result["detail"]["points"]
    return {
        "covered_ids": [p["id"] for p in points if p["awarded"]],  # points she fully covered
        "partial_ids": [p["id"] for p in points if p["partial"]],
        # Covered = 1, partial = 0.5 (never more than the card has points).
        "covered_count": result["detail"]["score"],
        "key_terms_missed": result["detail"].get("missing_key_terms") or [],
        "result": result,
        "api_call_id": result.get("api_call_id"),
    }


def tick_check(units: list, ticked) -> list[str]:
    """Self-tick: the unit ids she ticked, keeping only ones the card has."""
    valid = {u["id"] for u in units}
    return [uid for uid in dict.fromkeys(ticked or []) if uid in valid]


# ---- Command-word notes: fixed text, shown only when relevant (no API) -------------------------

_REASON_WORDS = re.compile(r"\b(because|so|therefore|since|as a result|due to|this means|which means|leads? to|"
                           r"causes?|hence|thus)\b", re.IGNORECASE)
_COMPARE_WORDS = re.compile(r"\b(whereas|while|but|both|than|however|unlike|similar|same|different|in contrast|"
                            r"on the other hand|more|less|fewer|higher|lower|larger|smaller|longer|shorter)\b",
                            re.IGNORECASE)


def _sentences(text: str) -> int:
    return len([s for s in re.split(r"[.!?]+", text) if s.strip()])


def command_note(command_word: str, text: str, units_total: int) -> str:
    """A one-line note about the command word, or "" when her answer gives no reason to show one."""
    word = (command_word or "").strip().lower()
    text = text or ""
    words, sentences = len(text.split()), _sentences(text)
    if word == "define" and sentences > 2:
        return "Define: one precise sentence is enough."
    if word in ("state", "name", "identify", "give", "list") and words > 15 * max(units_total, 1):
        return f"{word.capitalize()}: keep it short. A few words for each point is enough."
    if word == "explain" and not _REASON_WORDS.search(text):
        return "Explain: give the reason, not just what happens. Use a word like 'because', 'so' or 'therefore'."
    if word == "describe" and words < 4 * max(units_total, 1):
        return "Describe: say what it is like or what happens, in full sentences."
    if word == "compare" and not _COMPARE_WORDS.search(text):
        return "Compare: say how the two are alike or different, and name both."
    if word == "outline" and sentences > units_total + 2:
        return "Outline: give the main points only, in order."
    return ""


# ---- Boxes -----------------------------------------------------------------------

def move_for(covered: float, total: int, up_pct: int, down_pct: int) -> str:
    """"up", "stay" or "down" for a score: at least up_pct of the points covered goes up, under down_pct goes down,
    in between stays. Covered can be a half point (a partly covered point counts as half)."""
    if total <= 0:
        return "down"
    pct = covered * 100 / total
    if pct >= up_pct:
        return "up"
    return "down" if pct < down_pct else "stay"


def next_box(box_before: int, move: str) -> int:
    """Up one box (to the top, box 5), down one (to the bottom, box 1), or the same box."""
    if move == "up":
        return min(BOXES, box_before + 1)
    if move == "down":
        return max(1, box_before - 1)
    return box_before


def box_move(covered: float, total: int, settings: dict) -> str:
    return move_for(covered, total, settings["recall_up_pct"], settings["recall_down_pct"])


def get_progress(conn, card_id: str):
    return conn.execute("SELECT * FROM card_progress WHERE card_id = ?", (card_id,)).fetchone()


HELP_WINDOW = timedelta(hours=24)


def help_opened_recently(conn, card_id: str, now: datetime) -> bool:
    """Was this card's help opened (any version, in a set or on its own page) in the 24 hours before `now`?"""
    row = conn.execute("SELECT 1 FROM help_views WHERE card_id = ? AND opened_at >= ? AND opened_at <= ? LIMIT 1",
                       (card_id, (now - HELP_WINDOW).strftime(STAMP), now.strftime(STAMP))).fetchone()
    return row is not None


def record_attempt(conn, card: dict, *, method: str, covered_ids: list, units_total: int, settings: dict,
                   set_id=None, key_terms_missed=(), typed_text=None, result=None, api_call_id=None,
                   seconds_taken=None, command_note_text="", now=None, points_covered=None) -> int:
    """Save a recall attempt and move the card's box. Returns the attempt id.

    points_covered: how many points count as covered when that is more than the card's own
    covered_ids (typed checks credit points that another card in the topic supports).
    """
    now = _now(now)
    covered = len(covered_ids) if points_covered is None else min(points_covered, units_total)
    progress = get_progress(conn, card["id"])
    box_before = progress["box"] if progress else 1
    box_after = next_box(box_before, box_move(covered, units_total, settings))
    due = now + timedelta(days=settings["box_intervals"][box_after - 1])
    cur = conn.execute(
        """INSERT INTO recall_attempts
           (card_id, card_version, set_id, method, points_total, points_covered, covered_ids_json,
            key_terms_missed_json, typed_text, command_word, command_note, result_json, api_call_id,
            seconds_taken, box_before, box_after, with_help, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (card["id"], card.get("version", 1), set_id, method, units_total, covered,
         json.dumps(covered_ids), json.dumps(list(key_terms_missed)), typed_text,
         card.get("command_word", ""), command_note_text,
         json.dumps(result, ensure_ascii=False) if result is not None else None, api_call_id,
         seconds_taken, box_before, box_after, 1 if help_opened_recently(conn, card["id"], now) else 0,
         now.strftime(STAMP)))
    conn.execute(
        """INSERT INTO card_progress (card_id, box, last_attempt_at, due_at) VALUES (?, ?, ?, ?)
           ON CONFLICT (card_id) DO UPDATE SET box = excluded.box, last_attempt_at = excluded.last_attempt_at,
               due_at = excluded.due_at""",
        (card["id"], box_after, now.strftime(STAMP), due.strftime(STAMP)))
    conn.commit()
    return cur.lastrowid


# ---- Mark again -------------------------------------------------------------------

def chat_clarification(conn, set_id: int, card_id: str) -> str:
    """Her own messages from the "Ask about this" chat about this card, after she had answered it in this set
    (one line each, oldest first). The tutor's replies are left out on purpose: only what she says counts."""
    lines = []
    for row in conn.execute("SELECT messages_json FROM discussions WHERE kind = 'card' AND scope_id = ? AND set_id = ? "
                            "AND stage = 'after' ORDER BY id", (card_id, set_id)):
        lines += [f"- {m['text']}" for m in json.loads(row["messages_json"]) if m["role"] == "student"]
    return "\n".join(lines)


def can_remark(attempt, clarification: str) -> bool:
    """A typed attempt can be marked again once, and only after she has said something in the chat."""
    return bool(attempt["method"] == "typed" and attempt["typed_text"] and not attempt["remark_count"]
                and clarification.strip())


def remark_url(conn, set_id: int, card_id: str) -> str:
    """Where the "Mark again" button posts, or "" when this attempt can't be marked again."""
    revise_set, attempt = get_set(conn, set_id), set_attempt(conn, set_id, card_id)
    if revise_set is None or attempt is None or card_id not in revise_set["card_ids"]:
        return ""
    if not can_remark(attempt, chat_clarification(conn, set_id, card_id)):
        return ""
    return f"/revise/sets/{set_id}/remark/{revise_set['card_ids'].index(card_id)}"


def claim_remark(conn, attempt_id: int) -> bool:
    """Take the one re-mark of an attempt. False if it was already taken (the limit is one, even for two clicks)."""
    cur = conn.execute("UPDATE recall_attempts SET remark_count = 1 WHERE id = ? AND remark_count = 0", (attempt_id,))
    conn.commit()
    return cur.rowcount == 1


def release_remark(conn, attempt_id: int) -> None:
    """Give the re-mark back when the second check failed and nothing changed."""
    conn.execute("UPDATE recall_attempts SET remark_count = 0 WHERE id = ? AND first_result_json IS NULL", (attempt_id,))
    conn.commit()


def apply_remark(conn, attempt, check: dict, settings: dict) -> None:
    """Replace the attempt's score and box move with the second marking, keeping the first result.

    box_before stays as it was; box_after follows from the new score. The card's progress follows only while this is
    still the card's latest attempt (a later attempt has moved it on already), counting its days from the attempt."""
    total = attempt["points_total"]
    covered = min(check["covered_count"], total)
    box_after = next_box(attempt["box_before"], box_move(covered, total, settings))
    conn.execute(
        """UPDATE recall_attempts SET first_result_json = result_json, remark_count = 1, points_covered = ?,
               covered_ids_json = ?, key_terms_missed_json = ?, result_json = ?, api_call_id = ?, box_after = ?
           WHERE id = ?""",
        (covered, json.dumps(check["covered_ids"]), json.dumps(list(check["key_terms_missed"])),
         json.dumps(check["result"], ensure_ascii=False), check["api_call_id"], box_after, attempt["id"]))
    later = conn.execute("SELECT 1 FROM recall_attempts WHERE card_id = ? AND id > ? LIMIT 1",
                         (attempt["card_id"], attempt["id"])).fetchone()
    if later is None:
        marked_at = datetime.strptime(attempt["created_at"], STAMP).replace(tzinfo=timezone.utc)
        due = marked_at + timedelta(days=settings["box_intervals"][box_after - 1])
        conn.execute("UPDATE card_progress SET box = ?, due_at = ? WHERE card_id = ?",
                     (box_after, due.strftime(STAMP), attempt["card_id"]))
    conn.commit()


# ---- Choosing cards ---------------------------------------------------------------

def list_subjects(conn, now=None, level=None) -> list[dict]:
    """Subjects with approved cards in topics that aren't switched off and are at the study level
    (`level`: None for the parent's setting, or sec1 / sec2 / both / all)."""
    stamp = _now(now).strftime(STAMP)
    rows = conn.execute(
        f"""SELECT c.subject, COUNT(*) AS cards, COALESCE(SUM(p.due_at <= ?), 0) AS due,
                   COALESCE(SUM(p.card_id IS NULL), 0) AS new
            FROM revision_cards c LEFT JOIN card_progress p ON p.card_id = c.id
            WHERE c.topic {taught_filter(conn, level)} GROUP BY c.subject ORDER BY c.subject""", (stamp,)).fetchall()
    return [dict(r) for r in rows]


def list_topics(conn, subject: str, now=None, level=None) -> list[dict]:
    stamp = _now(now).strftime(STAMP)
    rows = conn.execute(
        f"""SELECT c.topic, t.title, COUNT(*) AS cards, COALESCE(SUM(p.due_at <= ?), 0) AS due,
                   COALESCE(SUM(p.card_id IS NULL), 0) AS new
            FROM revision_cards c LEFT JOIN topics t ON t.id = c.topic LEFT JOIN card_progress p ON p.card_id = c.id
            WHERE c.subject = ? AND c.topic {taught_filter(conn, level)}
            GROUP BY c.topic ORDER BY COALESCE(t.sort_order, 999), c.topic""", (stamp, subject)).fetchall()
    return [dict(r) for r in rows]


def _topic_clause(topics) -> tuple[str, list]:
    topics = list(topics or [])
    if not topics:
        return "", []
    return f" AND c.topic IN ({','.join('?' * len(topics))})", topics


LONG_CARD_POINTS = 4   # a card with more top-level points than this is shown alone in its set
LOOKAHEAD = 20         # extra candidates fetched, so skipping a long card still leaves a full set


def is_long_card(card: dict | None) -> bool:
    return card is not None and len(check_units(card)) > LONG_CARD_POINTS


def fit_set(conn, candidates: list, n: int) -> list[str]:
    """Choose a set of up to n cards from candidates (in order). A long card (more than LONG_CARD_POINTS top-level
    points) is shown alone: if it is first, the set is just that card; otherwise it is skipped and waits for a set
    where it comes first."""
    chosen = []
    for card_id in candidates:
        if is_long_card(get_card(conn, card_id)):
            if not chosen:
                return [card_id]
            continue
        chosen.append(card_id)
        if len(chosen) == n:
            break
    return chosen


def build_set(conn, subject, topics, settings: dict, exam_mode=None, now=None, level=None) -> list[str]:
    """Card ids for the next set: due cards first (longest overdue first), then new cards in book order.

    Exam mode ignores due dates and takes the weakest cards: lowest box, then lowest recall rate,
    then longest since last seen. subject None means every subject (the parent's "her due cards").
    Topics a parent has switched off are never included. A card with more than 4 top-level points is
    shown alone (see fit_set).
    """
    n = settings["cards_per_set"]
    want = n + LOOKAHEAD
    exam = settings["exam_mode"] if exam_mode is None else exam_mode
    where, params = f"c.topic {taught_filter(conn, level)}", []
    if subject:
        where += " AND c.subject = ?"
        params.append(subject)
    clause, topic_params = _topic_clause(topics)
    where += clause
    params += topic_params

    if exam:
        rows = conn.execute(
            f"""SELECT c.id FROM revision_cards c LEFT JOIN card_progress p ON p.card_id = c.id
                WHERE {where}
                ORDER BY COALESCE(p.box, 0),
                         COALESCE((SELECT AVG(a.points_covered * 1.0 / MAX(a.points_total, 1))
                                   FROM recall_attempts a WHERE a.card_id = c.id), 0),
                         COALESCE(p.last_attempt_at, ''), c.sort_key, c.id
                LIMIT ?""", params + [want]).fetchall()
        return fit_set(conn, [r["id"] for r in rows], n)

    stamp = _now(now).strftime(STAMP)
    due = conn.execute(
        f"""SELECT c.id FROM revision_cards c JOIN card_progress p ON p.card_id = c.id
            WHERE {where} AND p.due_at <= ? ORDER BY p.due_at, c.sort_key, c.id LIMIT ?""",
        params + [stamp, want]).fetchall()
    ids = [r["id"] for r in due]
    if len(ids) < want:
        new = conn.execute(
            f"""SELECT c.id FROM revision_cards c LEFT JOIN card_progress p ON p.card_id = c.id
                WHERE {where} AND p.card_id IS NULL ORDER BY c.sort_key, c.id LIMIT ?""",
            params + [want - len(ids)]).fetchall()
        ids += [r["id"] for r in new]
    return fit_set(conn, ids, n)


def due_only(conn, settings: dict, now=None, fit_long=True) -> list[str]:
    """Her due cards across every subject (for the parent quiz), longest overdue first.
    fit_long False gives every due card up to cards_per_set, without the long-card rule (for counting)."""
    stamp = _now(now).strftime(STAMP)
    n = settings["cards_per_set"]
    rows = conn.execute(
        f"""SELECT c.id FROM revision_cards c JOIN card_progress p ON p.card_id = c.id
            WHERE c.topic {taught_filter(conn)} AND p.due_at <= ? ORDER BY p.due_at, c.sort_key, c.id LIMIT ?""",
        (stamp, n + LOOKAHEAD if fit_long else n)).fetchall()
    ids = [r["id"] for r in rows]
    return fit_set(conn, ids, n) if fit_long else ids


def due_cards(conn, subject: str, limit: int, now=None) -> list[str]:
    """One subject's due cards (taught topics only), longest overdue first. For a session warm-up."""
    rows = conn.execute(
        f"""SELECT c.id FROM revision_cards c JOIN card_progress p ON p.card_id = c.id
            WHERE c.subject = ? AND c.topic {taught_filter(conn)} AND p.due_at <= ?
            ORDER BY p.due_at, c.sort_key, c.id LIMIT ?""",
        (subject, _now(now).strftime(STAMP), limit)).fetchall()
    return [r["id"] for r in rows]


def learn_cards(conn, topic: str, exclude=(), limit: int = 4) -> list[str]:
    """A topic's cards she hasn't mastered (new, or in box 3 or lower): new first, then lowest box, in book order."""
    exclude = list(exclude)
    skip = f" AND c.id NOT IN ({','.join('?' * len(exclude))})" if exclude else ""
    rows = conn.execute(
        f"""SELECT c.id FROM revision_cards c LEFT JOIN card_progress p ON p.card_id = c.id
            WHERE c.topic = ? AND (p.card_id IS NULL OR p.box <= 3){skip}
            ORDER BY COALESCE(p.box, 0), c.sort_key, c.id LIMIT ?""", [topic] + exclude + [limit]).fetchall()
    return [r["id"] for r in rows]


def next_due(conn, subject, topics, level=None):
    """When the next card in this choice falls due (a timestamp), or None."""
    clause, params = _topic_clause(topics)
    row = conn.execute(
        f"""SELECT MIN(p.due_at) AS due FROM revision_cards c JOIN card_progress p ON p.card_id = c.id
            WHERE c.subject = ? AND c.topic {taught_filter(conn, level)}{clause}""", [subject] + params).fetchone()
    return row["due"] if row else None


# ---- Sets -----------------------------------------------------------------------

def create_set(conn, *, card_ids: list, subject="", topics=(), exam_mode=False, kind="student",
               recall_timing="after_set", session_id=None, self_tick=False, now=None) -> int:
    cur = conn.execute(
        """INSERT INTO revise_sets (kind, subject, topics_json, card_ids_json, exam_mode, recall_timing,
                                    session_id, self_tick, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (kind, subject or "", json.dumps(list(topics or [])), json.dumps(card_ids), 1 if exam_mode else 0,
         recall_timing, session_id, 1 if self_tick else 0, _now(now).strftime(STAMP)))
    conn.commit()
    return cur.lastrowid


def get_set(conn, set_id: int):
    row = conn.execute("SELECT * FROM revise_sets WHERE id = ?", (set_id,)).fetchone()
    if row is None:
        return None
    return {**dict(row), "card_ids": json.loads(row["card_ids_json"]), "topics": json.loads(row["topics_json"])}


def set_attempt(conn, set_id: int, card_id: str):
    return conn.execute("SELECT * FROM recall_attempts WHERE set_id = ? AND card_id = ? ORDER BY id LIMIT 1",
                        (set_id, card_id)).fetchone()


def set_summary(conn, revise_set: dict) -> dict:
    """Cards in a set by how the attempt scored: known (up a box), staying (same box), revisit (down a box)."""
    from app import app_settings  # imported here: keeps this module's imports as they were

    settings = app_settings.get_all(conn)
    known, stay, revisit, not_done = [], [], [], []
    for card_id in revise_set["card_ids"]:
        card = get_card(conn, card_id)
        attempt = set_attempt(conn, revise_set["id"], card_id)
        entry = {"id": card_id, "prompt": card["prompt"] if card else card_id, "attempt": attempt}
        if attempt is None:
            not_done.append(entry)
            continue
        move = box_move(attempt["points_covered"], attempt["points_total"], settings)
        {"up": known, "stay": stay, "down": revisit}[move].append(entry)
    return {"known": known, "stay": stay, "revisit": revisit, "not_done": not_done}


def siblings(conn, card: dict):
    """For a card split from a long answer: (k, n, [sibling card ids in order]). None otherwise."""
    if not card.get("split_from"):
        return None
    rows = conn.execute("SELECT id, record_json FROM revision_cards WHERE id LIKE ? ORDER BY id",
                        (card["id"][:-1] + "_",)).fetchall()
    ids = [r["id"] for r in rows if json.loads(r["record_json"]).get("split_from") == card["split_from"]]
    if card["id"] not in ids:
        return None
    return ids.index(card["id"]) + 1, len(ids), ids


# ---- Underline step (no API) -------------------------------------------------------------

def study_lines(card: dict) -> list[dict]:
    """Every line of the answer as shown in the study view: {"text", "key_terms"}."""
    answer = card.get("answer") or {}
    fmt = answer.get("format")
    lines = []

    def add_points(points):
        for p in points or []:
            lines.append({"text": p.get("text", ""), "key_terms": p.get("key_terms") or []})
            for s in p.get("sub") or []:
                lines.append({"text": s.get("text", ""), "key_terms": s.get("key_terms") or []})

    if fmt in ("bullets", "list"):
        add_points(answer.get("points"))
    elif fmt == "grouped":
        for g in answer.get("groups") or []:
            add_points(g.get("points"))
    elif fmt == "table":
        terms = answer.get("key_terms") or []
        for row in (answer.get("table") or {}).get("rows") or []:
            text = " | ".join(str(c) for c in row)
            lines.append({"text": text, "key_terms": [t for t in terms if t.lower() in text.lower()]})
    elif fmt == "timeline":
        for e in answer.get("events") or []:
            lines.append({"text": f"{e.get('date', '')} {e.get('text', '')}".strip(), "key_terms": e.get("key_terms") or []})
    return lines


def _plain(word: str) -> str:
    return re.sub(r"[^0-9a-z]", "", word.lower())


def underline_words(card: dict) -> list[list[str]]:
    """The answer as lines of words she can tap. A word's address is "line-word", counting from 0."""
    return [line["text"].split() for line in study_lines(card)]


def underline_result(card: dict, tapped) -> dict:
    """Compare the words she tapped ("line-word" addresses) with the card's key terms.

    A key term counts as spotted if she tapped any of its words.
    Returns {"hits": [terms], "misses": [terms], "extras": [words that aren't in a key term]}.
    """
    tapped = set(tapped or [])
    hits, misses, key_addresses = [], [], set()
    lines = study_lines(card)
    for li, line in enumerate(lines):
        words = [_plain(w) for w in line["text"].split()]
        for term in line["key_terms"]:
            term_words = [w for w in (_plain(t) for t in term.split()) if w]
            addresses = set()
            for start in range(len(words) - len(term_words) + 1):
                if term_words and words[start:start + len(term_words)] == term_words:
                    addresses = {f"{li}-{start + k}" for k in range(len(term_words))}
                    break
            key_addresses |= addresses
            (hits if addresses & tapped else misses).append(term)
    extras = []
    for address in sorted(tapped - key_addresses, key=lambda a: tuple(int(x) for x in a.split("-") if x.isdigit())):
        li, _, wi = address.partition("-")
        if li.isdigit() and wi.isdigit() and int(li) < len(lines):
            words = lines[int(li)]["text"].split()
            if int(wi) < len(words):
                extras.append(words[int(wi)].strip(".,;:!?()\"'"))
    return {"hits": hits, "misses": misses, "extras": extras}
