"""The recall ladder's one-point-at-a-time flow for a card in a Revise set (docs: the recall ladder).

Database work only, no web code. Revise sets and session steps both use it, because a session's warm-up and Learn
steps are Revise sets.

Formats: word bank and fill the gaps ("blank formats") are checked in code. Starters and free recall ("text
formats") are checked by the model, one point (or slot) at a time, with the recall rubric (revise.card_as_question).

State: recall_work holds the format chosen for a card in a set; recall_points holds one row per point (slot)
answered, and a row with no score yet when she has asked for "a start" on a point she hasn't answered. When the
last point is answered the card's attempt is recorded (revise.record_attempt) and the box moves.
"""

import json
from datetime import datetime, timezone

from app import recall_ladder as ladder
from app import revise
from app.llm import LLMError
from app.marking import marker

BLANK_FORMATS = (ladder.WORDBANK, ladder.GAPS)
TEXT_FORMATS = (ladder.STARTERS, ladder.FREE)
IMPLEMENTED = BLANK_FORMATS + TEXT_FORMATS
INPUT_PREFIX = {ladder.WORDBANK: "b", ladder.GAPS: "g"}   # blank formats' form fields: b0, b1 ... / g0, g1 ...
SELF_RATINGS = {"got": 1.0, "partly": 0.5, "not": 0.0}


def _now(now=None) -> datetime:
    return now or datetime.now(timezone.utc)


# ---- Which format ---------------------------------------------------------------------------------------

def format_for(conn, revise_set: dict, card: dict, settings: dict, now=None):
    """The ladder format for this card in this set, or None when the old typed-answer page applies (the ladder is
    switched off, or a self-tick card or Quick-fire set). Chosen once, from the box the card is in now, and
    stored, so a reload (or a box change) never changes it mid-card."""
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
    """For the blank formats, the card's points that can be blanked, in order: [{"unit", "blanked"}]. A point with
    nothing to blank is not asked and is not counted in the card's total."""
    items = []
    for unit in revise.check_units(card):
        blanked = ladder.blank_unit(unit)
        if blanked:
            items.append({"unit": unit, "blanked": blanked})
    return items


def slot_count(card: dict, fmt: str) -> int:
    """How many points (slots) the card has in this format."""
    return len(presented(card)) if fmt in BLANK_FORMATS else len(revise.check_units(card))


def blank_count(card: dict, slot: int):
    """How many blanks point `slot` (1-based) has, or None if the card has no such point."""
    items = presented(card)
    return len(items[slot - 1]["blanked"]["answers"]) if 1 <= slot <= len(items) else None


def point_rows(conn, set_id: int, card_id: str) -> list:
    """The points she has answered, in order (a start taken but not yet answered is not one)."""
    return conn.execute("SELECT * FROM recall_points WHERE set_id = ? AND card_id = ? AND score IS NOT NULL "
                        "ORDER BY slot", (set_id, card_id)).fetchall()


def _row(conn, set_id: int, card_id: str, slot: int):
    return conn.execute("SELECT * FROM recall_points WHERE set_id = ? AND card_id = ? AND slot = ?",
                        (set_id, card_id, slot)).fetchone()


def next_slot(conn, revise_set: dict, card: dict) -> int:
    """The point to ask next (1-based); more than the number of points when every one is answered."""
    return len(point_rows(conn, revise_set["id"], card["id"])) + 1


def credited_units(conn, revise_set: dict, card: dict) -> set:
    """Free recall: the card's points her answers have already matched."""
    credited = set()
    for r in point_rows(conn, revise_set["id"], card["id"]):
        credited |= {u for u in (r["unit_id"] or "").split(",") if u}
    return credited


def remaining_units(conn, revise_set: dict, card: dict) -> list[dict]:
    credited = credited_units(conn, revise_set, card)
    return [u for u in revise.check_units(card) if u["id"] not in credited]


def target_unit(conn, revise_set: dict, card: dict, fmt: str, slot: int):
    """The card point this slot is about: for starters the slot's own point, for free recall the first point not
    matched yet (used for "Give me a start" and for a self-mark). None if there is none."""
    units = revise.check_units(card)
    if fmt == ladder.STARTERS:
        return units[slot - 1] if 1 <= slot <= len(units) else None
    remaining = remaining_units(conn, revise_set, card)
    return remaining[0] if remaining else None


def is_done(conn, revise_set: dict, card: dict, fmt: str) -> bool:
    """Has every point been answered (free recall: or every point been matched, which ends it early)?"""
    answered = len(point_rows(conn, revise_set["id"], card["id"]))
    if answered >= slot_count(card, fmt):
        return True
    return fmt == ladder.FREE and answered > 0 and not remaining_units(conn, revise_set, card)


def dots(conn, set_id: int, card_id: str, total: int, current: int) -> list[str]:
    """The progress row: one state per point, "full", "half", "miss", "current" or "todo"."""
    scores = {r["slot"]: r["score"] for r in point_rows(conn, set_id, card_id)}
    out = []
    for slot in range(1, total + 1):
        if slot in scores:
            out.append("full" if scores[slot] >= 1 else "half" if scores[slot] else "miss")
        else:
            out.append("current" if slot == current else "todo")
    return out


def point_view(conn, revise_set: dict, card: dict, fmt: str, slot: int) -> dict:
    """What the page needs to ask point `slot`. Blank formats: the text with blanks and, for the word bank, the
    words to tap (the answers are in the bank, but nothing says which blank each belongs to). Starters and free
    recall: the opening words (starters) or any start she has asked for. No other part of the answer."""
    total = slot_count(card, fmt)
    if fmt in TEXT_FORMATS:
        unit = target_unit(conn, revise_set, card, fmt, slot)
        row = _row(conn, revise_set["id"], card["id"], slot)
        used = bool(row and row["start_used"])
        return {"text_mode": True, "slot": slot, "total": total, "start_used": used,
                "opening": starter_text(unit, fmt, used) if unit else ""}
    item = presented(card)[slot - 1]
    bank = None
    if fmt == ladder.WORDBANK:
        bank = ladder.build_bank(item["blanked"]["answers"], ladder.distractor_pool(conn, card), item["unit"]["text"],
                                 ladder.stable_seed(revise_set["id"], card["id"], item["unit"]["id"]))
    return {"text_mode": False, "segments": item["blanked"]["segments"], "bank": bank, "slot": slot, "total": total,
            "n_blanks": len(item["blanked"]["answers"]), "prefix": INPUT_PREFIX[fmt]}


def starter_text(unit: dict, fmt: str, start_used: bool) -> str:
    """The words shown above her answer box: starters show the point's opening words; free recall shows nothing.
    After "Give me a start", a few more words of the point."""
    if fmt == ladder.STARTERS:
        opening, _ = ladder.starter_split(unit)
        return ladder.reveal_start(unit, len(opening.split())) if start_used else opening
    return ladder.reveal_start(unit, 0) if start_used else ""


def first_letter(card: dict, slot: int, blank: int) -> str:
    """The first letter of one blank's answer: the fill-the-gaps hint, given only when she asks."""
    items = presented(card)
    if not 1 <= slot <= len(items):
        return ""
    answers = items[slot - 1]["blanked"]["answers"]
    return answers[blank][:1] if 0 <= blank < len(answers) else ""


# ---- Answering: the blank formats (code only) -------------------------------------------------------------------

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


# ---- Answering: starters and free recall (the model, one point at a time) --------------------------------------------

def give_start(conn, revise_set: dict, card: dict, fmt: str, slot: int, now=None):
    """"Give me a start": note it and return the words to show, or None if it isn't allowed here. The point can then
    score at most half, and the use is logged on the point (start_used)."""
    if fmt not in TEXT_FORMATS or slot != next_slot(conn, revise_set, card) or slot > slot_count(card, fmt):
        return None
    unit = target_unit(conn, revise_set, card, fmt, slot)
    if unit is None:
        return None
    conn.execute(
        """INSERT OR IGNORE INTO recall_points (set_id, card_id, slot, unit_id, response, score, start_used,
                                                feedback_json, created_at)
           VALUES (?, ?, ?, ?, '', NULL, 1, NULL, ?)""",
        (revise_set["id"], card["id"], slot, unit["id"], _now(now).strftime(revise.STAMP)))
    conn.commit()
    return starter_text(unit, fmt, True)


def check_text(conn, card: dict, units: list, text: str, client=None) -> dict:
    """Mark one answer against these points with the recall rubric. Raises revise.CheckFailed if it gives no result."""
    try:
        result = marker.mark_answer(conn, revise.card_as_question(card, units), text, purpose="recall", client=client)
    except LLMError as e:
        raise revise.CheckFailed(str(e)) from None
    if result["marks_awarded"] is None:
        raise revise.CheckFailed(result.get("error") or "the check failed")
    return result


def _store_text_point(conn, revise_set, card, slot, unit_id, response, score, feedback, now):
    """Save an answered text point. If a start was taken on it, that row is completed (and keeps start_used)."""
    conn.execute(
        """INSERT INTO recall_points (set_id, card_id, slot, unit_id, response, score, start_used, feedback_json,
                                      created_at)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
           ON CONFLICT (set_id, card_id, slot) DO UPDATE SET unit_id = excluded.unit_id,
               response = excluded.response, score = excluded.score, feedback_json = excluded.feedback_json,
               created_at = excluded.created_at WHERE recall_points.score IS NULL""",
        (revise_set["id"], card["id"], slot, unit_id, response, score, json.dumps(feedback, ensure_ascii=False),
         _now(now).strftime(revise.STAMP)))
    conn.commit()


def submit_text(conn, revise_set: dict, card: dict, fmt: str, slot: int, text: str, now=None, client=None) -> bool:
    """Check her written answer to point `slot` with the model and store it. False if it isn't the next point.
    Raises revise.CheckFailed if the check gave no result (nothing is stored).

    Starters: the point's own words are checked, with the opening words in front of what she wrote. Free recall:
    her text is checked against every point not matched yet, and whichever it matches are credited (in any order).
    A point she took a start on scores at most half."""
    if fmt not in TEXT_FORMATS or slot != next_slot(conn, revise_set, card) or slot > slot_count(card, fmt):
        return False
    units = [target_unit(conn, revise_set, card, fmt, slot)] if fmt == ladder.STARTERS \
        else remaining_units(conn, revise_set, card)
    if not units or units[0] is None:
        return False
    row = _row(conn, revise_set["id"], card["id"], slot)
    used = bool(row and row["start_used"])
    opening = starter_text(units[0], fmt, used) if fmt == ladder.STARTERS else ""
    full = f"{opening} {text}".strip()
    result = check_text(conn, card, units, full, client)
    points = {p["id"]: p for p in result["detail"]["points"]}
    scores = {}
    for unit in units:
        p = points.get(unit["id"]) or {}
        scores[unit["id"]] = ladder.cap_score(1.0 if p.get("awarded") else 0.5 if p.get("partial") else 0.0, used)
    matched = [uid for uid, s in scores.items() if s > 0]
    unit_id = units[0]["id"] if fmt == ladder.STARTERS else (",".join(matched) or None)
    feedback = {"text": full, "units": scores, "detail": result["detail"], "api_call_id": result.get("api_call_id")}
    _store_text_point(conn, revise_set, card, slot, unit_id, full, sum(scores.values()), feedback, now)
    return True


def self_rate(conn, revise_set: dict, card: dict, fmt: str, slot: int, rating: str, now=None,
              note: str = "(you marked this one yourself)") -> bool:
    """She marks the point herself ("got it", "partly", "not yet"): offered only when the check can't run. "Not
    yet" is also what "I don't know" does, which skips the point with nothing sent to the model."""
    if fmt not in TEXT_FORMATS or rating not in SELF_RATINGS or slot != next_slot(conn, revise_set, card) \
            or slot > slot_count(card, fmt):
        return False
    unit = target_unit(conn, revise_set, card, fmt, slot)
    if unit is None:
        return False
    row = _row(conn, revise_set["id"], card["id"], slot)
    score = ladder.cap_score(SELF_RATINGS[rating], bool(row and row["start_used"]))
    feedback = {"text": note, "units": {unit["id"]: score}, "detail": {}, "self": True}
    _store_text_point(conn, revise_set, card, slot, unit["id"] if score else None, "", score, feedback, now)
    return True


# ---- Feedback after a point ---------------------------------------------------------------------------------------

def feedback(conn, revise_set: dict, card: dict, fmt: str, slot: int):
    """What the feedback page shows for an answered point, or None. Shown only after she has answered, so it may
    give the card's wording of what she matched (and, for starters, of the point itself)."""
    row = _row(conn, revise_set["id"], card["id"], slot)
    if row is None or row["score"] is None or not 1 <= slot <= slot_count(card, fmt):
        return None
    saved = json.loads(row["feedback_json"])
    total = slot_count(card, fmt)
    if fmt in BLANK_FORMATS:
        blanked = presented(card)[slot - 1]["blanked"]
        parts, wrong = [], []
        for seg in blanked["segments"]:
            if "text" in seg:
                parts.append({"text": seg["text"]})
                continue
            n = seg["blank"]
            ok, given, word = saved["results"][n], saved["given"][n], blanked["answers"][n]
            parts.append({"word": word, "given": given, "ok": ok})
            if not ok:
                wrong.append({"given": given, "word": word})
        return {"text_mode": False, "parts": parts, "score": row["score"], "wrong": wrong, "slot": slot, "total": total}
    units = {u["id"]: u for u in revise.check_units(card)}
    reasons = {p["id"]: p.get("reason", "") for p in (saved.get("detail") or {}).get("points", [])}
    shown = []
    for uid, score in saved["units"].items():
        if uid in units and (score > 0 or fmt == ladder.STARTERS):   # free recall never shows a point it didn't match
            shown.append({"unit": units[uid], "score": score, "reason": reasons.get(uid, "") if 0 < score < 1 else ""})
    return {"text_mode": True, "answer": saved["text"], "shown": shown, "score": row["score"], "slot": slot,
            "total": total, "by_self": bool(saved.get("self")), "start_used": bool(row["start_used"]),
            "matched": any(s["score"] > 0 for s in shown)}


# ---- The card's attempt ---------------------------------------------------------------------------------------------

def finalize(conn, revise_set: dict, card: dict, fmt: str, settings: dict, now=None):
    """Record the card's attempt from its answered points, once all are done. Returns the attempt id, or None if
    points are still to answer or the card already has its attempt in this set."""
    rows = point_rows(conn, revise_set["id"], card["id"])
    if not rows or not is_done(conn, revise_set, card, fmt) or revise.set_attempt(conn, revise_set["id"], card["id"]):
        return None
    now = _now(now)
    work = conn.execute("SELECT started_at FROM recall_work WHERE set_id = ? AND card_id = ?",
                        (revise_set["id"], card["id"])).fetchone()
    seconds = None
    if work:
        started = datetime.strptime(work["started_at"], revise.STAMP).replace(tzinfo=timezone.utc)
        seconds = max(0, int((now - started).total_seconds()))
    common = {"method": "typed", "settings": settings, "set_id": revise_set["id"], "seconds_taken": seconds,
              "fmt": fmt, "help_starts": sum(r["start_used"] for r in rows),
              "low_energy": bool(revise_set.get("low_energy")), "now": now}

    if fmt in BLANK_FORMATS:
        items = presented(card)
        detail = [{"id": r["unit_id"], "awarded": r["score"] == 1, "partial": r["score"] == 0.5,
                   "evidence_quote": "", "reason": ""} for r in rows]
        return revise.record_attempt(
            conn, card, covered_ids=[r["unit_id"] for r in rows if r["score"] == 1], units_total=len(items),
            points_covered=sum(r["score"] for r in rows), result={"detail": {"points": detail}, "ladder": fmt}, **common)

    units = revise.check_units(card)
    scores, quotes, missing, api_call_id, one_to_add = {}, {}, [], None, ""
    for r in rows:
        saved = json.loads(r["feedback_json"])
        for uid, s in saved["units"].items():
            scores[uid] = max(scores.get(uid, 0.0), s)
        for p in (saved.get("detail") or {}).get("points", []):
            if p.get("awarded") or p.get("partial"):
                quotes[p["id"]] = p
        missing += [t for t in (saved.get("detail") or {}).get("missing_key_terms") or [] if t not in missing]
        api_call_id = saved.get("api_call_id") or api_call_id
        one_to_add = (saved.get("detail") or {}).get("one_to_add") or one_to_add
    points = [{"id": u["id"], "awarded": scores.get(u["id"], 0) == 1, "partial": scores.get(u["id"], 0) == 0.5,
               "evidence_quote": (quotes.get(u["id"]) or {}).get("evidence_quote", "") if scores.get(u["id"]) else "",
               "reason": (quotes.get(u["id"]) or {}).get("reason", "") if scores.get(u["id"]) == 0.5 else ""}
              for u in units]
    typed = "\n".join(f"{r['slot']}. {json.loads(r['feedback_json'])['text']}" for r in rows)
    result = {"detail": {"points": points, "one_to_add": one_to_add, "missing_key_terms": missing,
                         "other_points": []}, "ladder": fmt}
    return revise.record_attempt(
        conn, card, covered_ids=[u["id"] for u in units if scores.get(u["id"], 0) == 1], units_total=len(units),
        points_covered=sum(scores.get(u["id"], 0) for u in units), typed_text=typed, result=result,
        key_terms_missed=missing, api_call_id=api_call_id,
        command_note_text=revise.command_note(card.get("command_word", ""), typed, len(units)), **common)
