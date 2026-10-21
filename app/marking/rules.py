"""Code-side marking rules (MARKING_SPEC.md sections 1, 3, 4 and 5).

The model reports what the answer contains; these functions check its quotes
and decide the score and confidence. Nothing here calls the API, so all of it
can be tested with made-up model output.

Entry point: score(question, model_output, answer) -> result dict.
"""

import re

# ---- Flags -------------------------------------------------------------------
# Every flag that fires is stored on the attempt. Only these lower confidence.
CONFIDENCE_DROP_FLAGS = ("borderline", "evidence_mismatch", "clamped", "level_lowered", "borderline_lowered")
# Others are informational: reversed, requires_unmet, unknown_point,
# missing_point, misread_question, recounted, describes_without_inference,
# self_supported.

CONFIDENCE_STEPS = ["low", "medium", "high"]
BASE_CONFIDENCE = {
    "assessment_book": "high",
    "school": "medium",
    "publisher_paper": "medium",
    "generated": "low",
}
SOURCE_LABELS = {
    "assessment_book": "assessment book key",
    "school": "school key",
    "publisher_paper": "publisher paper key",
    "generated": "generated question",
}


def _add_flag(flags: list, flag: str) -> None:
    if flag not in flags:
        flags.append(flag)


# ---- Quote check ---------------------------------------------------------------

TYPOGRAPHY = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                             "–": "-", "—": "-"})
_EDGE_CHARS = " .,;:!?'\"()"


def normalise(text: str) -> str:
    """Lowercase, straighten curly quotes and dashes, collapse whitespace."""
    return " ".join((text or "").translate(TYPOGRAPHY).lower().split())


def quote_core(quote: str) -> str:
    """A quote normalised for comparison, without leading/trailing punctuation."""
    return normalise(quote).strip(_EDGE_CHARS)


def quote_in_answer(quote: str, answer: str) -> bool:
    """True if the quote really appears in the answer (case- and whitespace-insensitive).

    Leading/trailing punctuation on the quote is ignored. An empty quote never matches.
    """
    q = quote_core(quote)
    return bool(q) and q in normalise(answer)


# ---- Points mode ---------------------------------------------------------------

def apply_points(question: dict, output: dict, answer: str) -> dict:
    flags = []
    scheme = {mp["id"]: mp for mp in question["mark_points"]}

    reported = {}
    for p in output.get("points", []):
        pid = p.get("id")
        if pid not in scheme:
            _add_flag(flags, "unknown_point")
        elif pid not in reported:  # keep the first report of each point
            reported[pid] = p

    points = []
    for mp in question["mark_points"]:
        p = reported.get(mp["id"])
        status = {"id": mp["id"], "text": mp["text"], "awarded": False, "evidence_quote": "",
                  "reason": "", "reversed": False, "not_awarded_because": ""}
        if p is None:
            _add_flag(flags, "missing_point")
            status["reason"] = "Not reported by the marker."
        else:
            status["evidence_quote"] = p.get("evidence_quote", "")
            status["reason"] = p.get("reason", "")
            status["reversed"] = bool(p.get("reversed"))
            if p.get("awarded"):
                if status["reversed"]:
                    status["not_awarded_because"] = "reversed"
                elif not quote_in_answer(status["evidence_quote"], answer):
                    status["not_awarded_because"] = "evidence_mismatch"
                    _add_flag(flags, "evidence_mismatch")
                else:
                    status["awarded"] = True
        if status["reversed"]:
            _add_flag(flags, "reversed")
        points.append(status)

    # "requires": a point only counts if the points it depends on were awarded.
    by_id = {s["id"]: s for s in points}
    changed = True
    while changed:
        changed = False
        for s in points:
            if s["awarded"] and any(not by_id[r]["awarded"] for r in scheme[s["id"]].get("requires", [])):
                s["awarded"] = False
                s["not_awarded_because"] = "requires_unmet"
                _add_flag(flags, "requires_unmet")
                changed = True

    marks = question["marks"]
    valid = sum(s["awarded"] for s in points)
    # Hitting the "any N" cap is normal marking: not a clamp, no flag.
    counted = min(valid, question.get("choose", marks), marks)

    if output.get("borderline"):
        _add_flag(flags, "borderline")
    if output.get("misread_question"):
        _add_flag(flags, "misread_question")

    return {
        "mode": "points",
        "marks_awarded": counted,
        "max_marks": marks,
        "valid_points": valid,
        "counted_points": counted,
        "level_awarded": None,
        "flags": flags,
        "detail": {
            "points": points,
            "missing_key_terms": output.get("missing_key_terms", []),
            # Only keep filler quotes that really are in the answer, so they can be highlighted.
            "filler_quotes": [f for f in output.get("filler_quotes", []) if quote_in_answer(f, answer)],
            "borderline_reason": output.get("borderline_reason", ""),
            "full_marks_answer": output.get("full_marks_answer", ""),
            "technique_note": output.get("technique_note", ""),
        },
    }


# ---- LORMS mode ----------------------------------------------------------------

_PAIR = re.compile(r"(\d+)\s*marks?\s+for\s+(\d+)", re.IGNORECASE)
# Whole word "supported". "unsupported" never matches because there is no word boundary inside it.
_SUPPORTED_WORD = re.compile(r"\bsupported\b", re.IGNORECASE)
# ...and a match is ignored if it follows "un-", "un " or "not ".
_NEGATED_BEFORE = re.compile(r"(?:\bun[\s-]*|\bnot\s+)$", re.IGNORECASE)


def rule_counts_supported_only(rule_text: str) -> bool:
    """True if award-rule text says only supported features count."""
    for m in _SUPPORTED_WORD.finditer(rule_text or ""):
        if not _NEGATED_BEFORE.search(rule_text[:m.start()]):
            return True
    return False


def count_rule(level: dict):
    """How a level's marks follow from counting features.

    Returns (basis, {count: marks}) where basis is "supported" or "any",
    or None if the level can't be counted (e.g. "E.g. 'There are many ships.'").
    A structured "count" field wins; otherwise the award_rules text is read.
    """
    structured = level.get("count")
    if structured:
        return structured["basis"], {int(k): v for k, v in structured["marks_by_count"].items()}
    text = level.get("award_rules", "")
    pairs = _PAIR.findall(text)
    if not pairs:
        return None
    table = {}
    for marks, count in pairs:
        table.setdefault(int(count), int(marks))
    basis = "supported" if rule_counts_supported_only(text) else "any"
    return basis, table


def marks_from_count(table: dict, count: int):
    """Marks for this many features: the entry for the largest count reached. None if none reached."""
    reached = [k for k in table if k <= count]
    return table[max(reached)] if reached else None


def _count_features(features: list, basis: str) -> int:
    if basis == "supported":
        return sum(1 for f in features if f["supported"])
    return len(features)


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def level_for_marks(levels: list, marks: int) -> int:
    """The LORMS level whose mark range holds these marks (0 if none)."""
    for lv in levels:
        if lv["marks_min"] <= marks <= lv["marks_max"]:
            return lv["level"]
    return 0


def apply_lorms(question: dict, output: dict, answer: str) -> dict:
    flags = []
    levels = sorted(question["lorms"], key=lambda lv: lv["level"])
    by_number = {lv["level"]: lv for lv in levels}

    # 1. Check quotes. A claim that isn't in the answer is dropped;
    #    support that isn't in the answer makes the feature unsupported.
    features, dropped = [], []
    for f in output.get("features", []):
        if not quote_in_answer(f.get("claim", ""), answer):
            _add_flag(flags, "evidence_mismatch")
            dropped.append({**f, "dropped_because": "claim not found in answer"})
            continue
        supported = bool(f.get("supported"))
        note = ""
        if supported and not quote_in_answer(f.get("support_quote", ""), answer):
            _add_flag(flags, "evidence_mismatch")
            supported = False
            note = "support quote not found in answer"
        elif supported and quote_core(f.get("support_quote", "")) in quote_core(f.get("claim", "")):
            # The "support" is just the claim again (or part of it): not real support.
            _add_flag(flags, "self_supported")
            supported = False
            note = "support quote is the same as, or part of, the claim"
        features.append({"claim": f.get("claim", ""), "supported": supported,
                         "support_quote": f.get("support_quote", "") if supported else "",
                         "explained": bool(f.get("explained")), "note": note})

    # 2. Put the proposed level and marks inside the scheme's ranges.
    proposed_level = output.get("proposed_level", 0)
    proposed_marks = output.get("proposed_marks", 0)
    lowest, highest = levels[0], levels[-1]
    if proposed_level < lowest["level"]:
        level = None
        marks = 0
        if proposed_marks != 0:
            _add_flag(flags, "clamped")
    else:
        if proposed_level > highest["level"]:
            _add_flag(flags, "clamped")
            proposed_level = highest["level"]
        level = by_number[proposed_level]
        marks = _clamp(proposed_marks, level["marks_min"], level["marks_max"])
        if marks != proposed_marks:
            _add_flag(flags, "clamped")
    proposed_in_range = marks

    # 3. Where the level's rule can be counted, recount from the checked features
    #    and keep the lower mark. If the level isn't met at all, drop a level.
    lowered = False
    while level is not None:
        rule = count_rule(level)
        if rule is None:
            if lowered:  # dropped into a level we can't count: be conservative
                marks = level["marks_min"]
            break
        basis, table = rule
        from_count = marks_from_count(table, _count_features(features, basis))
        if from_count is None:
            _add_flag(flags, "level_lowered")
            lowered = True
            level = by_number.get(level["level"] - 1)
            marks = level["marks_max"] if level else 0
            continue
        from_count = _clamp(from_count, level["marks_min"], level["marks_max"])
        if from_count < marks:
            marks = from_count
            _add_flag(flags, "recounted")
        break

    # 4. Conservative rule: when borderline, award the lower of the proposed mark and the
    #    other mark the model was torn between, then the level follows from the ranges.
    #    Without a usable other mark, fall back to one mark less than proposed (minimum 1).
    #    If the checks above already lowered the mark that far, it isn't lowered again.
    if output.get("borderline"):
        _add_flag(flags, "borderline")
        other = output.get("borderline_other_marks")
        if isinstance(other, int) and not isinstance(other, bool):
            target = max(0, min(proposed_in_range, other))
        else:
            target = max(1, proposed_in_range - 1)
        if marks > target:
            marks = target
            _add_flag(flags, "borderline_lowered")
            level = by_number.get(level_for_marks(levels, marks))

    if output.get("describes_without_inference"):
        _add_flag(flags, "describes_without_inference")

    return {
        "mode": "lorms",
        "marks_awarded": marks,
        "max_marks": question["marks"],
        "valid_points": None,
        "counted_points": None,
        "level_awarded": level["level"] if level else 0,
        "flags": flags,
        "detail": {
            "proposed_level": output.get("proposed_level"),
            "proposed_marks": output.get("proposed_marks"),
            "features": features,
            "dropped_features": dropped,
            "descriptor_matched": output.get("descriptor_matched", ""),
            "why_not_higher": output.get("why_not_higher", ""),
            "borderline_reason": output.get("borderline_reason", ""),
            "borderline_other_marks": output.get("borderline_other_marks"),
            "peel_map": output.get("peel_map", {}),
            "full_marks_answer": output.get("full_marks_answer", ""),
            "technique_note": output.get("technique_note", ""),
        },
    }


# ---- Confidence ----------------------------------------------------------------

def confidence(source: str, flags: list, generated_base: str | None = None) -> tuple[str, str]:
    """(label, reason). One step down if any drop flag fired, however many. Low is the floor.

    LORMS marking on its own does not lower confidence.
    generated_base: the base for a generated question once a parent approved it (the
    generated_approved_confidence setting). Only the "generated" source uses it; every other
    source, and a call without it, keeps the table above. Questions reach the bank only by approval.
    """
    base = BASE_CONFIDENCE.get(source, "low")
    label = SOURCE_LABELS.get(source, source)
    if source == "generated" and generated_base in CONFIDENCE_STEPS:
        base, label = generated_base, "generated question, approved by a parent"
    causes = [f for f in CONFIDENCE_DROP_FLAGS if f in flags]
    reason = f"{base} base ({label})"
    if not causes:
        return base, reason
    label = CONFIDENCE_STEPS[max(0, CONFIDENCE_STEPS.index(base) - 1)]
    return label, f"{reason}; down one step for: {', '.join(causes)}"


def score(question: dict, output: dict, answer: str, generated_base: str | None = None) -> dict:
    """Apply every code-side rule to the model's output and add confidence."""
    if question["marking_mode"] == "lorms":
        result = apply_lorms(question, output, answer)
    else:
        result = apply_points(question, output, answer)
    result["confidence"], result["confidence_reason"] = confidence(
        question["source"], result["flags"], generated_base)
    return result
