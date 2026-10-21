"""Mark one answer: build the request, call the model, apply the code-side rules."""

import base64
import json

from app import app_settings
from app.config import get_settings
from app.db import utc_now
from app.llm import LLMError, structured_call
from app.marking import prompts, rules
from app.marking.notes import render_notes, topic_notes
from app.page_images import media_type, question_images, resolve_render

CACHE = {"type": "ephemeral"}


def choose_model(question: dict, settings) -> tuple[str, str]:
    """(model, effort). Spec section 7: the light model for 1-mark point questions,
    the marking model otherwise. A blank effort means don't send one."""
    if question["marking_mode"] == "points" and question["marks"] == 1:
        return settings.light_model, settings.light_effort
    return settings.marking_model, settings.marking_effort


FIGURE_NOTE = ("The page image(s) below show the figure, graph or table this question refers to. Use them only "
               "to understand the question and check the answer against the mark scheme. Ignore any handwriting.")


def figure_blocks(question: dict) -> list:
    """Page images for a question that depends on a figure, as content blocks (else nothing).

    Uses the model-size renders listed on the question record. Questions without a figure
    send exactly the same request as before.
    """
    if question.get("figure") != "needed_to_answer":
        return []
    blocks = []
    for image in question_images(question):
        path = resolve_render(image.get("model") or image.get("display"))
        if path is not None:
            data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
            blocks.append({"type": "image",
                           "source": {"type": "base64", "media_type": media_type(path), "data": data}})
    return [{"type": "text", "text": FIGURE_NOTE}] + blocks if blocks else []


def mark_answer(conn, question: dict, answer: str, *, purpose="marking", client=None, light=False) -> dict:
    """Return a result dict. A failed call gives marks_awarded None and a flag
    naming the failure. LLMError is re-raised only when it is fatal.

    light: always use LIGHT_MODEL (recall checks in Revise mode), whatever the marks.
    Recall checks (question["notes_check"]) and generated points questions are also classified
    against the topic's approved cards (see app.marking.notes).
    """
    settings = get_settings()
    model, effort = (settings.light_model, settings.light_effort) if light else choose_model(question, settings)

    # Order matters for caching: system prompt (cached), then page images (figure-dependent
    # questions only) and the mark scheme (cached together), then the answer (not cached).
    system = [{"type": "text", "text": prompts.system_prompt(question), "cache_control": CACHE}]
    notes_cards = None
    if prompts.uses_notes(question):
        # Recall checks and generated questions also see the topic's approved cards. This block is
        # the same for every check in the topic, so it is cached per topic.
        notes = topic_notes(conn, question["topic"])
        system.append({"type": "text", "text": render_notes(notes), "cache_control": CACHE})
        notes_cards = {n["card_id"]: n["prompt"] for n in notes}
    content = figure_blocks(question) + [
        {"type": "text", "text": prompts.render_scheme(question), "cache_control": CACHE},
        {"type": "text", "text": prompts.render_answer(answer)},
    ]

    try:
        output, info = structured_call(
            conn, model=model, effort=effort, system=system, content=content,
            schema=prompts.output_schema(question), purpose=purpose, question_id=question["id"],
            fallback=settings.refusal_fallback, client=client,
        )
    except LLMError as e:
        if e.fatal:
            raise
        return {
            "question_id": question["id"], "mode": question["marking_mode"],
            "marks_awarded": None, "max_marks": question["marks"],
            "valid_points": None, "counted_points": None, "level_awarded": None,
            "confidence": None, "confidence_reason": "", "flags": [e.kind],
            "error": str(e), "model": model, "api_call_id": e.api_call_id,
            "usage": None, "model_output": None, "detail": {},
        }

    generated_base = None
    if question["source"] == "generated":  # other sources never read the setting
        generated_base = app_settings.get_all(conn)["generated_approved_confidence"]
    result = rules.score(question, output, answer, generated_base, notes_cards)
    result.update({
        "question_id": question["id"], "error": None, "model": info["model"],
        "api_call_id": info["api_call_id"], "usage": info["usage"], "model_output": output,
    })
    return result


def save_attempt(conn, question: dict, answer: str, result: dict, parent_attempt_id=None,
                 seconds_taken=None) -> int:
    """Store a marked student answer against the question version she answered. Returns the attempt id."""
    cur = conn.execute(
        """INSERT INTO attempts
           (question_id, question_version, parent_attempt_id, answer_text, marks_awarded, max_marks,
            valid_points, counted_points, level_awarded, confidence, confidence_reason,
            flags_json, model_output_json, result_json, model, api_call_id, seconds_taken, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            question["id"], question.get("version", 1), parent_attempt_id, answer,
            result["marks_awarded"], result["max_marks"],
            result["valid_points"], result["counted_points"], result["level_awarded"],
            result["confidence"], result["confidence_reason"],
            json.dumps(result["flags"]),
            json.dumps(result["model_output"], ensure_ascii=False) if result["model_output"] is not None else None,
            json.dumps(result, ensure_ascii=False), result["model"], result["api_call_id"],
            seconds_taken, utc_now(),
        ),
    )
    conn.commit()
    return cur.lastrowid
