"""'Help me' on a question (docs/STUDENT_UI_SPEC.md section 6), three tiers, one at a time:

1. Explain the question (LIGHT_MODEL): the question in simpler words, what the command word asks for, what the
   marks mean, and which part of the topic it is about. It sees only what she can already see, never the mark scheme.
2. Nudge (LIGHT_MODEL): which idea to think about. It sees the mark scheme so it can point the right way.
3. The related revision card (code): same topic, best text match.

Tiers 1 and 2 are written once per question version and stored in question_help. A failed call stores nothing,
so it can be tried again. Code checks each reply and throws away one that gives the answer away. Any tier she opens
is recorded in hint_uses, and the next first attempt on that question is marked "with help" (app/practice.py).
"""

import json
import math
import re

import anthropic

from app import practice, revise
from app.config import get_settings
from app.db import utc_now
from app.feedback import _command_words
from app.generate import plan
from app.llm import LLMError, structured_call
from app.marking import prompts
from app.marking.marker import figure_blocks

CACHE = {"type": "ephemeral"}
EXPLAIN_PARTS = ("rephrase", "command_word", "marks", "topic_part")
KEYS = {1: "explain", 2: "nudge", 3: "card"}

EXPLAIN_SYSTEM = (
    "You help a Singapore Secondary student aged 13 who is stuck on an exam question and has asked for help"
    " understanding what it is asking. You are given only what she can already see: the question, its context and"
    " options, its marks, its command word (and what that word means) and its topic. You are not given the mark"
    " scheme. Never give, hint at or start any part of the answer, and never say or suggest which option is correct:"
    " you only explain the question. Add no facts of your own beyond the words of the question."
    " Write plain, encouraging language and speak to her as \"you\". Keep each field to two sentences or fewer."
    " rephrase: the question put in simpler words (keep any key words the question itself uses)."
    " command_word: what the command word asks her to do; if there is no command word, say what kind of answer is wanted."
    " marks: what the marks tell her about the size of the answer, such as how many separate points to make or how"
    " much explanation each point needs."
    " topic_part: the part of the topic the question is about, as a short phrase that names the area and not the"
    " answer. Return only the JSON."
)

EXPLAIN_SCHEMA = {
    "type": "object", "properties": {p: {"type": "string"} for p in EXPLAIN_PARTS},
    "required": list(EXPLAIN_PARTS), "additionalProperties": False,
}

NUDGE_SYSTEM = (
    "You give a Singapore Secondary student aged 13 a small nudge on an exam question she is stuck on, before she"
    " answers. You are given the question and its mark scheme inside <question> and <mark_scheme> tags. The scheme is"
    " only so that you can point her the right way: it is confidential. Never quote it, never state or paraphrase"
    " any mark point, and never use a key term from it that the question does not already use."
    " Write one or two sentences, in plain encouraging language, speaking to her as \"you\", that tell her which idea"
    " or area to think about and not what to write, for example: \"Think about what happens to the particles when"
    " they are heated.\" Do not give a list. Return only the JSON."
)

NUDGE_SCHEMA = {
    "type": "object", "properties": {"nudge": {"type": "string"}}, "required": ["nudge"],
    "additionalProperties": False,
}


# ---- Which tiers she can open --------------------------------------------------------------

def tiers_available(conn, question: dict, settings: dict) -> list[int]:
    """The tiers offered for this question, in order. Tier 2 follows the parent's setting and needs a mark
    scheme to nudge from (an MCQ's scheme is just the answer). Tier 3 needs a card in the question's topic."""
    tiers = [1]
    if settings["help_tier2"] and question["marking_mode"] != "mcq":
        tiers.append(2)
    if related_card(conn, question) is not None:
        tiers.append(3)
    return tiers


# ---- Tier 3: the related card (code only) ------------------------------------------------------

def _card_words(card: dict) -> set:
    parts = [card.get("prompt", "")]
    for unit in revise.check_units(card):
        parts.append(unit["text"])
        parts.extend(unit.get("detail") or [])
        parts.extend(unit.get("key_terms") or [])
    return set(plan._content(" ".join(parts)))


def _question_words(question: dict) -> set:
    parts = [question["question"], question.get("context", "")]
    parts.extend(mp["text"] for mp in question.get("mark_points") or [])
    return set(plan._content(" ".join(parts)))


def related_card(conn, question: dict):
    """The revision card in the question's topic whose words overlap most with the question and its mark
    points (the scheme is read here, in code, and never shown). Ties go to book order. None if the topic has no cards."""
    rows = conn.execute("SELECT record_json, version FROM revision_cards WHERE topic = ? ORDER BY sort_key, id",
                        (question["topic"],)).fetchall()
    wanted, best, best_score = _question_words(question), None, -1.0
    for row in rows:
        card = {**json.loads(row["record_json"]), "version": row["version"]}
        words = _card_words(card)
        score = len(wanted & words) / math.sqrt(len(wanted) * len(words)) if wanted and words else 0.0
        if score > best_score:
            best, best_score = card, score
    return best


# ---- Tiers 1 and 2: written once, stored ---------------------------------------------------------

def _stored(conn, question: dict, tier: int):
    row = conn.execute("SELECT help_json FROM question_help WHERE question_id = ? AND question_version = ? AND tier = ?",
                       (question["id"], question.get("version", 1), tier)).fetchone()
    return json.loads(row["help_json"]) if row else None


def _topic_title(conn, topic_id: str) -> str:
    row = conn.execute("SELECT title FROM topics WHERE id = ?", (topic_id,)).fetchone()
    return row["title"] if row and row["title"] else practice.topic_label(topic_id)


def _visible_block(conn, question: dict) -> str:
    """What she can see, plus the meaning of the command word. No mark scheme."""
    view = practice.question_for_student(question)
    entry = _command_words().get((view["command_word"] or "").strip().lower()) or {}
    shown = {"subject": view["subject"], "level": question.get("level", ""), "topic": _topic_title(conn, view["topic"]),
             "context": view["context"], "question": view["question"], "marks": view["marks"],
             "command_word": view["command_word"], "command_word_means": entry.get("meaning", ""),
             "options": view["options"]}
    return "<question>\n" + json.dumps(shown, sort_keys=True, ensure_ascii=False, indent=1) + "\n</question>"


def _gives_away(question: dict, reply: dict) -> bool:
    """True if the reply states a mark point or uses a key term that the question itself doesn't."""
    points = question.get("mark_points") or []
    if not points:
        return False
    text = " ".join(str(v) for v in reply.values()).lower()
    seen = f"{question.get('context', '')} {question['question']}".lower()
    for mp in points:
        for term in mp.get("key_terms") or []:
            term = term.strip().lower()
            if term and term not in seen and re.search(r"\b" + re.escape(term) + r"\b", text):
                return True
    # A mark point the question already states isn't a leak when the reply repeats it.
    unstated = [mp for mp in points if not plan.answer_given_away(question.get("context", ""), question["question"], [mp])]
    return bool(plan.answer_given_away("", text, unstated))


def _write(conn, question: dict, tier: int, *, client=None):
    """One LIGHT_MODEL call. Returns (help, info), or (None, None) if it failed or the reply is unusable."""
    settings = get_settings()
    block = _visible_block(conn, question)
    if tier == 1:
        system, schema, text = EXPLAIN_SYSTEM, EXPLAIN_SCHEMA, block
    else:
        system, schema, text = NUDGE_SYSTEM, NUDGE_SCHEMA, block + "\n" + prompts.render_scheme(question)
    content = figure_blocks(question) + [{"type": "text", "text": text, "cache_control": CACHE}]
    try:
        output, info = structured_call(
            conn, model=settings.light_model, effort=settings.light_effort,
            system=[{"type": "text", "text": system, "cache_control": CACHE}],
            content=content, schema=schema, purpose=f"question_help_{tier}", question_id=question["id"],
            fallback=settings.refusal_fallback, max_tokens=1500, client=client)
    except (LLMError, anthropic.AnthropicError):
        return None, None
    keys = EXPLAIN_PARTS if tier == 1 else ("nudge",)
    help_ = {k: (output.get(k) or "").strip() for k in keys}
    if not all(help_.values()) or _gives_away(question, help_):
        return None, None
    return help_, info


def get_or_write(conn, question: dict, tier: int, *, client=None):
    """The stored help for this tier and question version, or written now (once). None if it couldn't be written."""
    cached = _stored(conn, question, tier)
    if cached:
        return cached
    help_, info = _write(conn, question, tier, client=client)
    if help_ is None:
        return None
    conn.execute(
        """INSERT OR IGNORE INTO question_help (question_id, question_version, tier, help_json, model, api_call_id,
                                                created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (question["id"], question.get("version", 1), tier, json.dumps(help_, ensure_ascii=False),
         info["model"], info["api_call_id"], utc_now()))
    conn.commit()
    return help_


# ---- Opening the help --------------------------------------------------------------------------

def open_help(conn, question: dict, settings: dict, *, client=None, now=None) -> dict:
    """Open the next tier she hasn't used on this question (or show them all again once she has used every tier).

    Returns {"explain", "nudge", "card": what is shown so far (None if not reached), "failed": True if a tier
    couldn't be loaded (nothing is recorded, so she can try again), "next_tier": the tier a "more help" button
    opens, or None}. Opening a tier is recorded; that is what marks the attempt "with help".
    """
    tiers = tiers_available(conn, question, settings)
    used = practice.unclaimed_tiers(conn, question["id"], now)
    wanted = next((t for t in tiers if t not in used), tiers[-1])
    shown, failed = {"explain": None, "nudge": None, "card": None}, False
    for tier in tiers:
        if tier > wanted:
            break
        content = related_card(conn, question) if tier == 3 else get_or_write(conn, question, tier, client=client)
        if content is None:
            failed = True
            break
        shown[KEYS[tier]] = content
    if not failed and wanted not in used:
        practice.record_hint(conn, question["id"], wanted)
    later = [t for t in tiers if t > wanted]
    return {**shown, "failed": failed, "wanted": wanted, "next_tier": later[0] if later else None}
