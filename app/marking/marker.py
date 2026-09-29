"""Mark one answer: build the request, call the model, apply the code-side rules."""

import json

from app.config import get_settings
from app.db import utc_now
from app.llm import LLMError, structured_call
from app.marking import prompts, rules

CACHE = {"type": "ephemeral"}


def choose_model(question: dict, settings) -> tuple[str, str]:
    """(model, effort). Spec section 7: the light model for 1-mark point questions,
    the marking model otherwise. A blank effort means don't send one."""
    if question["marking_mode"] == "points" and question["marks"] == 1:
        return settings.light_model, settings.light_effort
    return settings.marking_model, settings.marking_effort


def mark_answer(conn, question: dict, answer: str, *, purpose="marking", client=None) -> dict:
    """Return a result dict. A failed call gives marks_awarded None and a flag
    naming the failure. LLMError is re-raised only when it is fatal."""
    settings = get_settings()
    model, effort = choose_model(question, settings)

    # Order matters for caching: system prompt (cached), mark scheme (cached), answer (not cached).
    system = [{"type": "text", "text": prompts.system_prompt(question), "cache_control": CACHE}]
    content = [
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

    result = rules.score(question, output, answer)
    result.update({
        "question_id": question["id"], "error": None, "model": info["model"],
        "api_call_id": info["api_call_id"], "usage": info["usage"], "model_output": output,
    })
    return result


def save_attempt(conn, question: dict, answer: str, result: dict, parent_attempt_id=None) -> int:
    """Store a marked student answer. Returns the attempt id."""
    cur = conn.execute(
        """INSERT INTO attempts
           (question_id, parent_attempt_id, answer_text, marks_awarded, max_marks,
            valid_points, counted_points, level_awarded, confidence, confidence_reason,
            flags_json, model_output_json, result_json, model, api_call_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            question["id"], parent_attempt_id, answer, result["marks_awarded"], result["max_marks"],
            result["valid_points"], result["counted_points"], result["level_awarded"],
            result["confidence"], result["confidence_reason"],
            json.dumps(result["flags"]),
            json.dumps(result["model_output"], ensure_ascii=False) if result["model_output"] is not None else None,
            json.dumps(result, ensure_ascii=False), result["model"], result["api_call_id"], utc_now(),
        ),
    )
    conn.commit()
    return cur.lastrowid
