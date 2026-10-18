"""The question-generation request (docs/GENERATION_SPEC.md sections 2, 4 and 6).

Order for prompt caching: system prompt (cached, one per subject) -> the style examples for the topic
(cached) -> the card and the questions wanted (not cached)."""

import json

from app.generate.plan import card_units

CACHE = {"type": "ephemeral"}
MAX_STYLE_EXAMPLES = 5

RULES = """You write practice questions for a Singapore Secondary student, using only her own revision card.
Rules:
1. Use only facts on the card. No outside facts, dates, names or numbers.
2. Every mark point must be one of the card's points or sub-points, worded so its key terms are kept exactly. A mark point's key_terms must be taken from the card's key terms.
3. The question must be answerable from the card alone. Do not give the answer away in the question or its context.
4. Marks follow the house style: about one mark per distinct point. The command word must suit the marks (no "explain" for 1 mark, no "state" for 4). Use only the command words listed below, within the marks shown.
5. Phrase like the style examples: plain Singapore-school English, no trick questions. Do not write the [n] marks in the question text; it is added for her.
6. Give a full_marks_answer: the shortest answer that earns full marks.
7. context is empty, or one short scene-setting sentence that uses only card facts.
8. Write the questions for the slots asked for, one per slot, with the slot number. If a slot cannot be written from the card, leave it out.
Return only the JSON."""


def system_prompt(subject: str, glossary: dict) -> str:
    """The rules plus the command words allowed for this subject. Sorted, so the text is the same every time."""
    lines = [f"- {word} ({lo}-{hi} marks): {e['meaning']}" for word, e in sorted(glossary.items())
             if subject in e["use"] for lo, hi in [e["marks"]]]
    return RULES + "\n\nCommand words:\n" + "\n".join(lines)


def _obj(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


_STR = {"type": "string"}
_MARK_POINT = _obj({"text": _STR, "key_terms": {"type": "array", "items": _STR}})
QUESTION_SCHEMA = _obj({
    "slot": {"type": "integer"},
    "command_word": _STR,
    "context": _STR,
    "question": _STR,
    "marks": {"type": "integer"},
    "mark_points": {"type": "array", "items": _MARK_POINT},
    "full_marks_answer": _STR,
})
SCHEMA = _obj({"questions": {"type": "array", "items": QUESTION_SCHEMA}})


def style_examples(conn, subject: str, topic: str) -> list[dict]:
    """Up to five approved real questions of the subject (never generated ones), the same topic first.

    Text-only points questions. Chosen by a fixed order, so the same topic always gets the same
    examples and the cached block is reused."""
    rows = conn.execute(
        """SELECT record_json FROM questions
           WHERE subject = ? AND source != 'generated' AND marking_mode = 'points' AND figure = 'none'
                 AND worked_example = 0
           ORDER BY (topic = ?) DESC, id LIMIT ?""", (subject, topic, MAX_STYLE_EXAMPLES)).fetchall()
    examples = []
    for r in rows:
        q = json.loads(r["record_json"])
        example = {"question": q["question"], "marks": q["marks"], "command_word": q.get("command_word", ""),
                   "mark_points": [{"text": mp["text"], "key_terms": mp.get("key_terms", [])}
                                   for mp in q.get("mark_points", [])]}
        if q.get("context"):
            example["context"] = q["context"]
        if q.get("choose"):
            example["any_n_count"] = q["choose"]
        examples.append(example)
    return examples


def render_style(examples: list[dict]) -> str:
    body = json.dumps(examples, sort_keys=True, ensure_ascii=False, indent=1)
    return f"<style_examples>\n{body}\n</style_examples>"


def render_card(card: dict) -> str:
    shown = {"prompt": card.get("prompt", ""), "command_word": card.get("command_word", ""),
             "subject": card["subject"], "level": card["level"], "topic": card["topic"],
             "points": [{"text": u["text"], "key_terms": u["key_terms"]}
                        for u in card_units(card) if u["kind"] == "point"],
             "examples": [u["text"] for u in card_units(card) if u["kind"] == "example"]}
    if card.get("notes"):
        shown["notes"] = card["notes"]
    return "<card>\n" + json.dumps(shown, sort_keys=True, ensure_ascii=False, indent=1) + "\n</card>"


SLOT_TEXT = {
    "recall": "a recall question (define / state / name) on one fact of the card",
    "multi": "a question that needs several of the card's points; if the card has more points than marks, "
             "list all the card's points as mark points and the student needs any N of them",
    "example": "a question asking her to use an example or a named place from the card's examples",
}


def render_slots(slots: list[dict]) -> str:
    lines = [f"slot {s['slot']}: {SLOT_TEXT[s['kind']]}; "
             + (f"{s['marks_min']} mark" + ("s" if s["marks_min"] != 1 else "") if s["marks_min"] == s["marks_max"]
                else f"{s['marks_min']} to {s['marks_max']} marks")
             for s in slots]
    return "Write these questions:\n" + "\n".join(lines)


def build_request(card: dict, subject: str, glossary: dict, examples: list[dict], slots: list[dict]):
    """(system blocks, content blocks)."""
    system = [{"type": "text", "text": system_prompt(subject, glossary), "cache_control": CACHE}]
    content = [{"type": "text", "text": render_style(examples), "cache_control": CACHE},
               {"type": "text", "text": render_card(card) + "\n\n" + render_slots(slots)}]
    return system, content
