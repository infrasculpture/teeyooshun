"""Turn a stored attempt into what the feedback page shows (MARKING_SPEC.md section 6)."""

import re
from functools import lru_cache

from markupsafe import Markup, escape

from app import practice
from app.generate.plan import CommandWordsError, load_command_words
from app.marking import mcq_explain
from app.marking.rules import TYPOGRAPHY, quote_core

WHY_NOT_AWARDED = {
    "evidence_mismatch": "The marker's quote wasn't in your answer, so this mark wasn't given.",
    "requires_unmet": "This mark only counts if you got the point it depends on.",
    "reversed": "Your answer says the opposite of this.",
}


@lru_cache(maxsize=1)
def _command_words() -> dict:
    try:
        return load_command_words()
    except CommandWordsError:  # a broken table must never break the question page
        return {}


def command_note(command_word: str, marks: int) -> str:
    """The gentle note under the answer box, e.g. "Explain [4]: give reasons or …". Empty if the word is unknown."""
    entry = _command_words().get((command_word or "").strip().lower())
    if not entry or not entry["meaning"]:
        return ""
    return f"{command_word.strip().capitalize()} [{marks}]: {entry['meaning']}."


def _find(text: str, quote: str):
    """(start, end) of the quote in the text, case- and whitespace-insensitive, or None."""
    core = quote_core(quote)
    if not core:
        return None
    pattern = r"\s+".join(re.escape(word) for word in core.split())
    m = re.search(pattern, text, re.IGNORECASE)
    return (m.start(), m.end()) if m else None


def highlight(answer: str, quotes: list, labels: list | None = None, filler: list = ()) -> Markup:
    """The answer, HTML-escaped, with each quote wrapped in <mark>.

    `labels` (parallel to `quotes`) adds a small number after a highlight, tying it to the mark
    point it earned. `filler` quotes are greyed, unless they overlap a highlight.

    Matching is case- and whitespace-insensitive like the quote check. Straightening
    curly quotes keeps every character in place, so positions match the original.
    """
    text = answer.translate(TYPOGRAPHY)
    found = []
    for i, q in enumerate(quotes):
        span = _find(text, q)
        if span:
            found.append((span[0], span[1], labels[i] if labels else ""))
    found.sort()
    merged = []  # [start, end, [labels]]
    for start, end, label in found:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
            if label:
                merged[-1][2].append(label)
        else:
            merged.append([start, end, [label] if label else []])
    greyed = []
    for q in filler:
        span = _find(text, q)
        if span and not any(span[0] < m[1] and m[0] < span[1] for m in merged + greyed):
            greyed.append([span[0], span[1], None])
    out, pos = [], 0
    for start, end, tags in sorted(merged + greyed):
        out.append(escape(answer[pos:start]))
        if tags is None:
            out.append(Markup('<span class="filler">') + escape(answer[start:end]) + Markup("</span>"))
        else:
            out.append(Markup("<mark>") + escape(answer[start:end]) + Markup("</mark>"))
            if tags:
                out.append(Markup('<sup class="mp">') + escape(",".join(tags)) + Markup("</sup>"))
        pos = end
    out.append(escape(answer[pos:]))
    return Markup("").join(out)


def _is_missing(text: str) -> bool:
    return (text or "").strip().upper().startswith("MISSING")


def one_thing_to_fix(mode: str, marks: int, max_marks: int, view: dict, technique_note: str) -> dict | None:
    """The single headline for the score card, chosen in code from the marking JSON.

    Order: a missed key term, then a missed point, then the technique note. None when there is
    nothing to fix (full marks) or for MCQs (their explanation is the feedback).
    """
    if mode == "mcq" or marks >= max_marks:
        return None
    if mode == "points":
        if view["missing_key_terms"]:
            return {"kind": "key_term", "text": f"Use the key term “{view['missing_key_terms'][0]}”."}
        if view["not_earned"]:
            return {"kind": "point", "text": view["not_earned"][0]["point"]}
    else:  # lorms: a missing PEEL part is the concrete gap; "why not higher" says what the next level needs
        if view["peel_missing"]:
            return {"kind": "point", "text": f"Your answer is missing the {view['peel_missing'][0]} part of PEEL."}
        if view["why_not_higher"]:
            return {"kind": "point", "text": view["why_not_higher"]}
    if technique_note:
        return {"kind": "technique", "text": technique_note}
    return None


def _points_view(result: dict) -> dict:
    detail = result["detail"]
    earned, not_earned = [], []
    for p in detail["points"]:
        if p["awarded"]:
            earned.append({"n": len(earned) + 1, "quote": p["evidence_quote"], "point": p["text"]})
        else:
            why = WHY_NOT_AWARDED.get(p["not_awarded_because"]) or (
                WHY_NOT_AWARDED["reversed"] if p["reversed"] else "")
            not_earned.append({"point": p["text"], "reason": p["reason"], "why": why,
                               "reversed": p["reversed"]})
    cap = None
    if result["valid_points"] is not None and result["valid_points"] > result["counted_points"]:
        cap = {"valid": result["valid_points"], "counted": result["counted_points"]}
    return {
        "earned": earned,
        "not_earned": not_earned,
        "filler_quotes": detail.get("filler_quotes", []),
        "missing_key_terms": detail.get("missing_key_terms", []),
        "cap": cap,
        "highlight_quotes": [p["evidence_quote"] for p in detail["points"] if p["awarded"]],
        "highlight_labels": [str(e["n"]) for e in earned],
        "highlight_filler": detail.get("filler_quotes", []),
    }


def _lorms_view(result: dict) -> dict:
    detail = result["detail"]
    quotes = []
    for f in detail["features"]:
        quotes.append(f["claim"])
        if f["supported"]:
            quotes.append(f["support_quote"])
    peel = detail.get("peel_map", {})
    return {
        "level": result["level_awarded"],
        "features": detail["features"],
        "unsupported": [f for f in detail["features"] if not f["supported"]],
        "peel_missing": [p.capitalize() for p in ("point", "evidence", "explain", "link") if _is_missing(peel.get(p))],
        "descriptor_matched": detail.get("descriptor_matched", ""),
        "why_not_higher": detail.get("why_not_higher", ""),
        "peel_map": detail.get("peel_map", {}),
        "highlight_quotes": quotes, "highlight_labels": [], "highlight_filler": [],
    }


def _mcq_view(result: dict) -> dict:
    detail = result["detail"]
    text = {o["label"]: o["text"] for o in detail["options"]}
    return {
        "chosen": detail["chosen"], "chosen_text": text.get(detail["chosen"], ""),
        "correct": detail["correct_option"], "correct_text": text.get(detail["correct_option"], ""),
        "right": detail["chosen"] == detail["correct_option"],
        "highlight_quotes": [], "highlight_labels": [], "highlight_filler": [],
    }


def build_view(conn, attempt, is_parent: bool) -> dict:
    result = practice.attempt_result(attempt)
    question = practice.question_for_attempt(conn, attempt)
    mode = result["mode"]
    if mode == "mcq":
        mode_view = _mcq_view(result)
        # Read from the database only: loading this page never calls the API.
        mode_view["explanation"] = mcq_explain.get(conn, attempt["question_id"], attempt["question_version"],
                                                   mode_view["chosen"])
    elif mode == "lorms":
        mode_view = _lorms_view(result)
    else:
        mode_view = _points_view(result)

    first = None
    if attempt["parent_attempt_id"] is not None:
        first_row = practice.get_attempt(conn, attempt["parent_attempt_id"])
        first = {"id": first_row["id"], "marks": first_row["marks_awarded"],
                 "gain": attempt["marks_awarded"] - first_row["marks_awarded"]}

    status = practice.rewrite_status(conn, attempt)
    rewrite = practice.get_rewrite(conn, attempt["id"]) if status == "rewritten" else None
    events = practice.events_for(conn, attempt["id"])

    marks, max_marks = attempt["marks_awarded"], attempt["max_marks"]
    full_marks_answer = result["detail"].get("full_marks_answer", "")
    technique_note = result["detail"].get("technique_note", "")
    one_thing = one_thing_to_fix(mode, marks, max_marks, mode_view, technique_note)

    # The steps she clicks through, in order (STUDENT_UI_SPEC.md section 5). Earlier ones stay visible.
    has_missing = False
    if mode == "mcq":
        steps = ["score", "explain"]
    else:
        if mode == "points":
            has_missing = bool(mode_view["not_earned"] or mode_view["missing_key_terms"])
        else:
            has_missing = bool(mode_view["why_not_higher"] or mode_view["unsupported"] or mode_view["peel_missing"])
        steps = ["score", "answer"]
        if has_missing or technique_note:
            steps.append("missing")
        if full_marks_answer:
            steps.append("full")
        if status == "available":
            steps.append("rewrite")

    return {
        "attempt": attempt,
        "question": practice.question_for_student(question),
        "mode": mode,
        "marks": marks,
        "max_marks": max_marks,
        "confidence": attempt["confidence"],
        "answer_html": highlight(f"{mode_view['chosen']}: {mode_view['chosen_text']}" if mode == "mcq"
                                 else attempt["answer_text"], mode_view["highlight_quotes"],
                                 mode_view["highlight_labels"], mode_view["highlight_filler"]),
        "m": mode_view,
        "one_thing": one_thing,
        "steps": steps,
        "has_missing": has_missing,
        "full_marks_answer": full_marks_answer,
        "technique_note": technique_note,
        "first": first,
        "rewrite_status": status,
        "rewrite_id": rewrite["id"] if rewrite else None,
        "looks_wrong": [e for e in events if e["event"] == "marking_looks_wrong"],
        "is_parent": is_parent,
        # Parent-only detail
        "confidence_reason": attempt["confidence_reason"],
        "practice_question": is_parent and question.get("source") == "generated",  # shown in the parent panel only
        "flags": result["flags"],
        "borderline_reason": result["detail"].get("borderline_reason", ""),
        "verdict": practice.parent_verdict(conn, attempt["id"]),
    }
