"""What to generate for a card, and the code-side checks on what comes back
(docs/GENERATION_SPEC.md sections 2, 3 and 5). Nothing here calls the API.

The command words, and the marks each may carry, come from docs/command_words.yaml.
"""

import difflib
import re
from pathlib import Path

import yaml

COMMAND_WORDS_FILE = Path(__file__).resolve().parent.parent.parent / "docs" / "command_words.yaml"
MAX_PER_CARD = 3
HISTORY_MAX_MARKS = 3  # History knowledge questions are 1-3 marks in v1
MATCH = 0.6  # a mark point this similar to a card point is "on the card"
DUPLICATE = 0.85  # question texts this similar are a possible duplicate
GIVES_AWAY = 0.8  # share of a mark point's content words found in the question text

POSSIBLE_DUPLICATE = "Possible duplicate: very like"
GIVES_AWAY_FLAG = "Gives the answer away: the question text contains the mark point"

_STOP = {"the", "a", "an", "of", "and", "is", "are", "to", "in", "it", "be", "by", "for", "on", "that", "this",
         "with", "as", "at", "or", "its", "was", "were"}
_WORD = re.compile(r"[a-z0-9]+")


class CommandWordsError(ValueError):
    pass


def load_command_words(path=None) -> dict:
    """{word: {"meaning", "marks": (lo, hi), "use": [subjects]}} from docs/command_words.yaml."""
    path = Path(path or COMMAND_WORDS_FILE)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CommandWordsError(f"{path}: file not found") from None
    except yaml.YAMLError as e:
        raise CommandWordsError(f"{path}: not valid YAML: {e}") from None
    if not isinstance(data, dict):
        raise CommandWordsError(f"{path}: top level must map each command word to its details")
    words, problems = {}, []
    for word, entry in data.items():
        marks = entry.get("marks") if isinstance(entry, dict) else None
        ok = (isinstance(marks, list) and len(marks) == 2 and all(isinstance(m, int) for m in marks)
              and 1 <= marks[0] <= marks[1] and isinstance(entry.get("use"), list))
        if not ok:
            problems.append(f"'{word}' needs marks: [lowest, highest] and a use list")
            continue
        words[str(word).lower()] = {"meaning": str(entry.get("meaning", "")), "marks": (marks[0], marks[1]),
                                    "use": [str(u) for u in entry["use"]]}
    if problems:
        raise CommandWordsError(f"{path}: " + "; ".join(problems))
    return words


# ---- A card's points ------------------------------------------------------------------

def card_units(card: dict) -> list[dict]:
    """Every point, sub-point, group heading, timeline event and example on the card, in order.

    {"text", "key_terms", "top", "kind"}: top marks the items that count as "points" for the mix
    (top-level points; for a grouped card the group headings; for a table each row); kind is "point"
    or "example". Image cards give no units (v1 does not generate from them)."""
    answer = card.get("answer") or {}
    fmt = answer.get("format")
    units = []

    def add(text, key_terms, top, kind="point"):
        text = (text or "").strip()
        if text:
            units.append({"text": text, "key_terms": [k for k in (key_terms or []) if k], "top": top, "kind": kind})

    def add_points(points, top):
        for p in points or []:
            add(p.get("text"), p.get("key_terms"), top)
            for s in p.get("sub") or []:
                add(s.get("text"), s.get("key_terms"), False)

    if fmt in ("bullets", "list"):
        add_points(answer.get("points"), True)
    elif fmt == "grouped":
        for g in answer.get("groups") or []:
            add(g.get("heading"), [], True)
            add_points(g.get("points"), False)
    elif fmt == "timeline":
        for e in answer.get("events") or []:
            add(f"{e.get('date', '')}: {e.get('text', '')}".strip(": "), e.get("key_terms"), True)
    elif fmt == "table":
        # Each row is one point, worded as Revise mode words it: "Row label; Heading: cell; Heading: cell".
        table = answer.get("table") or {}
        headers, terms = table.get("headers") or [], answer.get("key_terms") or []
        for row in table.get("rows") or []:
            cells = [f"{h}: {c}" if h else str(c) for h, c in zip(list(headers) + [""] * len(row), row)]
            joined = " ".join(str(c) for c in row).lower()
            add("; ".join(cells), [t for t in terms if t.lower() in joined], True)
    if units:
        for example in card.get("examples") or []:
            add(example, [], False, "example")
    return units


def point_count(units: list[dict]) -> int:
    return sum(1 for u in units if u["top"] and u["kind"] == "point")


def card_key_terms(units: list[dict]) -> set:
    return {k.strip().lower() for u in units for k in u["key_terms"] if k.strip()}


def skip_reason(card: dict, units: list[dict]) -> str:
    """Why a card is skipped, or "" if questions can be generated from it."""
    fmt = (card.get("answer") or {}).get("format")
    if fmt == "image":
        return "image card"
    if point_count(units) < 1:
        return "no usable point"
    return ""


def compared_things(card: dict) -> list[str]:
    """The things a table card compares: its column headings after the first (the row labels).
    Two or more means compare / distinguish questions suit it. Empty for any other card."""
    answer = card.get("answer") or {}
    if answer.get("format") != "table":
        return []
    headers = (answer.get("table") or {}).get("headers") or []
    return [str(h).strip() for h in headers[1:] if str(h).strip()]


def plan_slots(card: dict, units: list[dict]) -> list[dict]:
    """The questions to ask for, decided by code from the card's size (spec section 3).

    [{"slot": 1, "kind": "recall" | "multi" | "compare" | "example", "marks_min", "marks_max"}], at most
    MAX_PER_CARD. For a table the size is the row count, and the multi-mark question is a "compare" one
    when the table compares two or more things (not for History, where compare is not a command word).
    """
    n = point_count(units)
    if n < 1:
        return []
    slots = [{"kind": "recall", "marks_min": 1, "marks_max": 1}]
    if n == 2:
        slots.append({"kind": "multi", "marks_min": 2, "marks_max": 2})
    elif n == 3:
        slots.append({"kind": "multi", "marks_min": 2, "marks_max": 3})
    elif n >= 4:
        slots.append({"kind": "multi", "marks_min": 3, "marks_max": 4})
    if len(compared_things(card)) >= 2 and card.get("subject") != "history":
        for s in slots:
            if s["kind"] == "multi":
                s["kind"] = "compare"
    if any(u["kind"] == "example" for u in units) and len(slots) < MAX_PER_CARD:
        slots.append({"kind": "example", "marks_min": 2, "marks_max": 3})
    if card.get("subject") == "history":
        for s in slots:
            s["marks_max"] = min(s["marks_max"], HISTORY_MAX_MARKS)
            s["marks_min"] = min(s["marks_min"], s["marks_max"])
    return [dict(s, slot=i) for i, s in enumerate(slots[:MAX_PER_CARD], 1)]


# ---- Checks on the model's questions ----------------------------------------------------

def tokens(text) -> list[str]:
    return _WORD.findall((text or "").lower())


def norm(text) -> str:
    return " ".join(tokens(text))


def _content(text) -> list[str]:
    all_tokens = tokens(text)
    return [t for t in all_tokens if t not in _STOP] or all_tokens


def match_score(point_text: str, unit_text: str) -> float:
    """How much of the mark point is found in the card point: the larger of the share of its content
    words present and the overall text similarity (0 to 1). Simple text similarity, no API."""
    a, b = set(_content(point_text)), set(_content(unit_text))
    if not a or not b:
        return 0.0
    contained = len(a & b) / len(a)
    return max(contained, difflib.SequenceMatcher(None, norm(point_text), norm(unit_text)).ratio())


def best_unit(point_text: str, units: list[dict]):
    """(score, unit) for the card point closest to this mark point."""
    return max(((match_score(point_text, u["text"]), u) for u in units), key=lambda pair: pair[0],
               default=(0.0, None))


def check_question(item: dict, slot: dict, units: list[dict], glossary: dict, subject: str):
    """Check one question from the model against the card (spec section 5).

    Returns (fields, warnings). fields is None when the question must be dropped, and warnings then
    says why. Otherwise fields has context, question, marks, command_word, mark_points, choose
    (0 for none) and full_marks_answer; warnings are notes for the parent."""
    warnings, kept = [], []
    card_terms = card_key_terms(units)
    for mp in item.get("mark_points") or []:
        text = (mp.get("text") or "").strip()
        if not text:
            continue
        score, unit = best_unit(text, units)
        if score < MATCH:
            warnings.append(f"Dropped a mark point that is not on the card: {text}")
            continue
        if any(norm(text) == norm(k["text"]) for k in kept):
            continue
        terms = [t.strip() for t in mp.get("key_terms") or [] if t.strip().lower() in card_terms]
        kept.append({"id": f"MP{len(kept) + 1}", "text": text, "key_terms": terms or list(unit["key_terms"])})

    marks = item.get("marks")
    if not isinstance(marks, int) or isinstance(marks, bool) or marks < 1:
        return None, warnings + ["Dropped the question: its marks are not a whole number of 1 or more"]
    if not kept:
        return None, warnings + ["Dropped the question: none of its mark points are on the card"]
    if marks > len(kept):
        return None, warnings + [f"Dropped the question: worth {marks} marks but only {len(kept)} mark point(s) "
                                 "are on the card and there is no 'any N'"]
    question = (item.get("question") or "").strip()
    if not question:
        return None, warnings + ["Dropped the question: it has no question text"]

    command_word = (item.get("command_word") or "").strip().lower()
    entry = glossary.get(command_word)
    if entry is None:
        warnings.append(f"Command word '{command_word}' is not in the command word list")
    elif subject not in entry["use"]:
        warnings.append(f"Command word '{command_word}' is not used for {subject}")
    elif not entry["marks"][0] <= marks <= entry["marks"][1]:
        lo, hi = entry["marks"]
        warnings.append(f"Command word '{command_word}' suits {lo}-{hi} marks, not {marks}")
    if not slot["marks_min"] <= marks <= slot["marks_max"]:
        warnings.append(f"Marks ({marks}) differ from the {slot['marks_min']}-{slot['marks_max']} planned "
                        "for this kind of question")

    fields = {"context": (item.get("context") or "").strip(), "question": question, "marks": marks,
              "command_word": command_word, "mark_points": kept,
              "choose": marks if len(kept) > marks else 0,
              "full_marks_answer": (item.get("full_marks_answer") or "").strip()}
    return fields, warnings


def find_duplicate(question: str, existing) -> str:
    """The id of the existing question whose text is nearly the same, or "". existing: [(id, normalised text)]."""
    key = norm(question)
    best, best_ratio = "", 0.0
    for other_id, other in existing:
        ratio = difflib.SequenceMatcher(None, key, other).ratio()
        if ratio >= DUPLICATE and ratio > best_ratio:
            best, best_ratio = other_id, ratio
    return best


def answer_given_away(context: str, question: str, mark_points: list[dict]) -> str:
    """The text of the first mark point that the question (or its context) already says, or "".

    Short points (under three words) are ignored: a question naturally uses a key term."""
    whole = norm(f"{context} {question}")
    words = set(whole.split())
    for mp in mark_points:
        text = norm(mp["text"])
        if len(text.split()) < 3:
            continue
        content = set(_content(mp["text"]))
        if f" {text} " in f" {whole} " or (len(content) >= 4 and len(content & words) / len(content) >= GIVES_AWAY):
            return mp["text"]
    return ""
