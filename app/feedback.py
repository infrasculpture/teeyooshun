"""Turn a stored attempt into what the feedback page shows (MARKING_SPEC.md section 6)."""

import re

from markupsafe import Markup, escape

from app import practice
from app.marking import mcq_explain
from app.marking.rules import TYPOGRAPHY, quote_core

WHY_NOT_AWARDED = {
    "evidence_mismatch": "The marker's quote wasn't in your answer, so this mark wasn't given.",
    "requires_unmet": "This mark only counts if you got the point it depends on.",
    "reversed": "Your answer says the opposite of this.",
}


def highlight(answer: str, quotes: list) -> Markup:
    """The answer, HTML-escaped, with each quote wrapped in <mark>.

    Matching is case- and whitespace-insensitive like the quote check. Straightening
    curly quotes keeps every character in place, so positions match the original.
    """
    text = answer.translate(TYPOGRAPHY)
    spans = []
    for q in quotes:
        core = quote_core(q)
        if not core:
            continue
        pattern = r"\s+".join(re.escape(word) for word in core.split())
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            spans.append((m.start(), m.end()))
    spans.sort()
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    out, pos = [], 0
    for start, end in merged:
        out.append(escape(answer[pos:start]))
        out.append(Markup("<mark>") + escape(answer[start:end]) + Markup("</mark>"))
        pos = end
    out.append(escape(answer[pos:]))
    return Markup("").join(out)


def _points_view(result: dict) -> dict:
    detail = result["detail"]
    earned, not_earned = [], []
    for p in detail["points"]:
        if p["awarded"]:
            earned.append({"quote": p["evidence_quote"], "point": p["text"]})
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
    }


def _lorms_view(result: dict) -> dict:
    detail = result["detail"]
    quotes = []
    for f in detail["features"]:
        quotes.append(f["claim"])
        if f["supported"]:
            quotes.append(f["support_quote"])
    return {
        "level": result["level_awarded"],
        "features": detail["features"],
        "descriptor_matched": detail.get("descriptor_matched", ""),
        "why_not_higher": detail.get("why_not_higher", ""),
        "peel_map": detail.get("peel_map", {}),
        "highlight_quotes": quotes,
    }


def _mcq_view(result: dict) -> dict:
    detail = result["detail"]
    text = {o["label"]: o["text"] for o in detail["options"]}
    return {
        "chosen": detail["chosen"], "chosen_text": text.get(detail["chosen"], ""),
        "correct": detail["correct_option"], "correct_text": text.get(detail["correct_option"], ""),
        "right": detail["chosen"] == detail["correct_option"],
        "highlight_quotes": [],
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

    return {
        "attempt": attempt,
        "question": practice.question_for_student(question),
        "mode": mode,
        "marks": attempt["marks_awarded"],
        "max_marks": attempt["max_marks"],
        "confidence": attempt["confidence"],
        "answer_html": highlight(f"{mode_view['chosen']}: {mode_view['chosen_text']}" if mode == "mcq"
                                 else attempt["answer_text"], mode_view["highlight_quotes"]),
        "m": mode_view,
        "full_marks_answer": result["detail"].get("full_marks_answer", ""),
        "technique_note": result["detail"].get("technique_note", ""),
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
