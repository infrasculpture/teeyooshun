"""The recall ladder's one-point-at-a-time flow for a card in a Revise set (docs: the recall ladder).

Database work only, no web code. Revise sets and session steps both use it, because a session's warm-up and Learn
steps are Revise sets. This step covers the word bank and fill-the-gaps formats (code-checked, no API call).

State: recall_work holds the format chosen for a card in a set; recall_points holds one row per point answered.
When the last point is answered the card's attempt is recorded (revise.record_attempt) and the box moves.
"""

import json
from datetime import datetime, timezone

from app import recall_ladder as ladder
from app import revise

IMPLEMENTED = (ladder.WORDBANK, ladder.GAPS)   # formats with a point-by-point page so far
INPUT_PREFIX = {ladder.WORDBANK: "b", ladder.GAPS: "g"}   # form field names: b0, b1 ... / g0, g1 ...


def _now(now=None) -> datetime:
    return now or datetime.now(timezone.utc)


# ---- Which format ---------------------------------------------------------------------------------------

def format_for(conn, revise_set: dict, card: dict, settings: dict, now=None):
    """The ladder format for this card in this set, or None when the old typed-answer page applies (the ladder is
    switched off, a self-tick card or Quick-fire set, or a format that has no page yet). Chosen once, from the box
    the card is in now, and stored, so a reload (or a box change) never changes it mid-card."""
    if not settings.get("recall_ladder") or revise.recalls_by_self_tick(revise_set, card):
        return None
    row = conn.execute("SELECT format FROM recall_work WHERE set_id = ? AND card_id = ?",
                       (revise_set["id"], card["id"])).fetchone()
    if row:
        return row["format"] if row["format"] in IMPLEMENTED else None
    progress = revise.get_progress(conn, card["id"])
    fmt = ladder.choose_format(card, progress["box"] if progress else 1, revise.check_units(card),
                               low_energy=bool(revise_set.get("low_energy")),
                               long_limit=settings.get("long_card_points", revise.LONG_CARD_POINTS))
    if fmt not in IMPLEMENTED:
        return None
    conn.execute("INSERT OR IGNORE INTO recall_work (set_id, card_id, format, started_at) VALUES (?, ?, ?, ?)",
                 (revise_set["id"], card["id"], fmt, _now(now).strftime(revise.STAMP)))
    conn.commit()
    return fmt


# ---- The points of a card -------------------------------------------------------------------------------------

def presented(card: dict) -> list[dict]:
    """The card's points that can be blanked, in order: [{"unit", "blanked"}]. A point with nothing to blank is not
    asked in the word bank or fill the gaps, and is not counted in the card's total."""
    items = []
    for unit in revise.check_units(card):
        blanked = ladder.blank_unit(unit)
        if blanked:
            items.append({"unit": unit, "blanked": blanked})
    return items


def blank_count(card: dict, slot: int):
    """How many blanks point `slot` (1-based) has, or None if the card has no such point."""
    items = presented(card)
    return len(items[slot - 1]["blanked"]["answers"]) if 1 <= slot <= len(items) else None


def point_rows(conn, set_id: int, card_id: str) -> list:
    return conn.execute("SELECT * FROM recall_points WHERE set_id = ? AND card_id = ? ORDER BY slot",
                        (set_id, card_id)).fetchall()


def next_slot(conn, revise_set: dict, card: dict) -> int:
    """The point to ask next (1-based); more than the number of points when every one is answered."""
    return len(point_rows(conn, revise_set["id"], card["id"])) + 1


def dots(conn, set_id: int, card_id: str, total: int, current: int) -> list[str]:
    """The progress row: one state per point, "full", "half", "miss", "current" or "todo"."""
    scores = {r["slot"]: r["score"] for r in point_rows(conn, set_id, card_id)}
    out = []
    for slot in range(1, total + 1):
        if slot in scores:
            out.append("full" if scores[slot] == 1 else "half" if scores[slot] else "miss")
        else:
            out.append("current" if slot == current else "todo")
    return out


def point_view(conn, revise_set: dict, card: dict, fmt: str, slot: int) -> dict:
    """What the page needs to ask point `slot`: the text with blanks, and for the word bank the words to tap.
    The answers are in the bank (that is the exercise) but nothing says which blank each belongs to."""
    item = presented(card)[slot - 1]
    bank = None
    if fmt == ladder.WORDBANK:
        bank = ladder.build_bank(item["blanked"]["answers"], ladder.distractor_pool(conn, card), item["unit"]["text"],
                                 ladder.stable_seed(revise_set["id"], card["id"], item["unit"]["id"]))
    return {"segments": item["blanked"]["segments"], "bank": bank, "slot": slot, "total": len(presented(card)),
            "n_blanks": len(item["blanked"]["answers"]), "prefix": INPUT_PREFIX[fmt]}


def first_letter(card: dict, slot: int, blank: int) -> str:
    """The first letter of one blank's answer: the fill-the-gaps hint, given only when she asks."""
    items = presented(card)
    if not 1 <= slot <= len(items):
        return ""
    answers = items[slot - 1]["blanked"]["answers"]
    return answers[blank][:1] if 0 <= blank < len(answers) else ""


# ---- Answering ------------------------------------------------------------------------------------------------

def submit_point(conn, revise_set: dict, card: dict, fmt: str, slot: int, given: list, now=None) -> bool:
    """Check and store her answer to point `slot`. False if it isn't the next point (a repeat or a skip ahead):
    nothing changes then. The word bank is exact; fill the gaps forgives a small spelling slip."""
    items = presented(card)
    if not 1 <= slot <= len(items) or slot != next_slot(conn, revise_set, card):
        return False
    item = items[slot - 1]
    scored = ladder.score_blanks(given, item["blanked"], tolerant=fmt == ladder.GAPS)
    padded = (list(given) + [""] * len(item["blanked"]["answers"]))[:len(item["blanked"]["answers"])]
    conn.execute(
        """INSERT INTO recall_points (set_id, card_id, slot, unit_id, response, score, start_used, feedback_json,
                                      created_at)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
        (revise_set["id"], card["id"], slot, item["unit"]["id"], " | ".join(padded), scored["score"],
         json.dumps({"given": padded, "results": scored["results"]}), _now(now).strftime(revise.STAMP)))
    conn.commit()
    return True


def feedback(conn, revise_set: dict, card: dict, slot: int):
    """What the feedback page shows for an answered point, or None. Shown only after she has answered, so it may
    give the card's wording: {"parts": [{"text"} | {"word", "given", "ok"}], "score", "wrong": [{"given","word"}]}."""
    row = conn.execute("SELECT * FROM recall_points WHERE set_id = ? AND card_id = ? AND slot = ?",
                       (revise_set["id"], card["id"], slot)).fetchone()
    items = presented(card)
    if row is None or not 1 <= slot <= len(items):
        return None
    saved = json.loads(row["feedback_json"])
    blanked, parts, wrong = items[slot - 1]["blanked"], [], []
    for seg in blanked["segments"]:
        if "text" in seg:
            parts.append({"text": seg["text"]})
            continue
        n = seg["blank"]
        ok, given, word = saved["results"][n], saved["given"][n], blanked["answers"][n]
        parts.append({"word": word, "given": given, "ok": ok})
        if not ok:
            wrong.append({"given": given, "word": word})
    return {"parts": parts, "score": row["score"], "wrong": wrong, "slot": slot, "total": len(items)}


def finalize(conn, revise_set: dict, card: dict, fmt: str, settings: dict, now=None):
    """Record the card's attempt from its answered points, once all are done. Returns the attempt id, or None if
    points are still to answer or the card already has its attempt in this set."""
    items = presented(card)
    rows = point_rows(conn, revise_set["id"], card["id"])
    if not items or len(rows) < len(items) or revise.set_attempt(conn, revise_set["id"], card["id"]):
        return None
    now = _now(now)
    work = conn.execute("SELECT started_at FROM recall_work WHERE set_id = ? AND card_id = ?",
                        (revise_set["id"], card["id"])).fetchone()
    seconds = None
    if work:
        started = datetime.strptime(work["started_at"], revise.STAMP).replace(tzinfo=timezone.utc)
        seconds = max(0, int((now - started).total_seconds()))
    detail = [{"id": r["unit_id"], "awarded": r["score"] == 1, "partial": r["score"] == 0.5,
               "evidence_quote": "", "reason": ""} for r in rows]
    return revise.record_attempt(
        conn, card, method="typed", covered_ids=[r["unit_id"] for r in rows if r["score"] == 1],
        units_total=len(items), settings=settings, set_id=revise_set["id"],
        points_covered=sum(r["score"] for r in rows), result={"detail": {"points": detail}, "ladder": fmt},
        seconds_taken=seconds, fmt=fmt, help_starts=sum(r["start_used"] for r in rows),
        low_energy=bool(revise_set.get("low_energy")), now=now)
