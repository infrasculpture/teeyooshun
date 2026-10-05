"""A short explanation shown after an MCQ is answered. Scoring stays in code (mcq.py).

Written by LIGHT_MODEL the first time an option is chosen for a question version,
stored in mcq_explanations, and reused after that: one API call per (question
version, chosen option), ever. A failed call stores nothing, so it can be tried again.
"""

import json

import anthropic

from app.config import get_settings
from app.db import utc_now
from app.llm import LLMError, structured_call
from app.marking.marker import figure_blocks

SYSTEM = (
    "You are explaining the answer to a multiple-choice question to a Singapore Secondary student aged 13,"
    " after she has answered it. The correct option is given and is always right: never contradict it."
    " why_correct: one or two sentences on why the correct option is right, using the key scientific term."
    " If her choice was wrong: why_chosen_wrong is one or two sentences on why the option she chose is wrong,"
    " and misconception is one short sentence naming the misunderstanding that choice suggests."
    " If her choice was correct, leave why_chosen_wrong and misconception empty."
    " Use plain, encouraging language and speak to her as \"you\". If a page image is included, use it only to"
    " understand the diagram the question refers to and ignore any handwriting. Return only the JSON."
)

SCHEMA = {
    "type": "object",
    "properties": {"why_correct": {"type": "string"}, "why_chosen_wrong": {"type": "string"},
                   "misconception": {"type": "string"}},
    "required": ["why_correct", "why_chosen_wrong", "misconception"],
    "additionalProperties": False,
}


def get(conn, question_id: str, question_version: int, chosen: str):
    """The stored explanation for this question version and choice, or None."""
    row = conn.execute(
        "SELECT why_correct, why_chosen_wrong, misconception FROM mcq_explanations "
        "WHERE question_id = ? AND question_version = ? AND chosen_option = ?",
        (question_id, question_version, chosen)).fetchone()
    return dict(row) if row else None


def _question_block(question: dict) -> str:
    shown = {"context": question.get("context", ""), "question": question["question"],
             "options": question["options"], "correct_option": question["correct_option"]}
    return "<question>\n" + json.dumps(shown, sort_keys=True, ensure_ascii=False, indent=1) + "\n</question>"


def explain(conn, question: dict, chosen: str, *, client=None):
    """The explanation for this choice: from the database, or generated once and stored.

    Returns None if the API call fails (nothing is stored, so it can be tried again).
    """
    version = question.get("version", 1)
    cached = get(conn, question["id"], version, chosen)
    if cached:
        return cached

    settings = get_settings()
    right = chosen == question["correct_option"]
    # Stable parts first for caching: system prompt, then any page image and the question; her choice last.
    content = figure_blocks(question) + [
        {"type": "text", "text": _question_block(question), "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": f"She chose option {chosen}. " + ("That is correct." if right else "That is wrong.")},
    ]
    try:
        output, info = structured_call(
            conn, model=settings.light_model, effort=settings.light_effort,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            content=content, schema=SCHEMA, purpose="mcq_explanation", question_id=question["id"],
            fallback=settings.refusal_fallback, max_tokens=4000, client=client,
        )
    except (LLMError, anthropic.AnthropicError):
        return None

    explanation = {
        "why_correct": (output.get("why_correct") or "").strip(),
        "why_chosen_wrong": "" if right else (output.get("why_chosen_wrong") or "").strip(),
        "misconception": "" if right else (output.get("misconception") or "").strip(),
    }
    if not explanation["why_correct"]:
        return None
    conn.execute(
        """INSERT OR IGNORE INTO mcq_explanations
           (question_id, question_version, chosen_option, why_correct, why_chosen_wrong, misconception,
            model, api_call_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (question["id"], version, chosen, explanation["why_correct"], explanation["why_chosen_wrong"],
         explanation["misconception"], info["model"], info["api_call_id"], utc_now()))
    conn.commit()
    return explanation
