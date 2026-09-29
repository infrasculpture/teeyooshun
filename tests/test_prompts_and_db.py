"""What the model sees, and storing an attempt (no API calls)."""

import json

from app.importer import import_questions, stored_record
from app.marking.marker import save_attempt
from app.marking.prompts import render_scheme, system_prompt
from app.marking.rules import score


def test_scheme_hides_ids_source_and_test_answers(points_question):
    text = render_scheme(stored_record(points_question))
    assert "sci-test-points-q1" not in text
    assert "assessment_book" not in text
    assert "The balloon expands because the particles" not in text  # a test answer
    assert "MP1" in text  # mark point ids are needed to report points


def test_scheme_and_prompt_are_identical_between_calls(points_question):
    # Needed for prompt caching.
    assert render_scheme(points_question) == render_scheme(points_question)
    assert system_prompt(points_question) == system_prompt(points_question)


def test_tables_created(conn):
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"questions", "attempts", "api_calls"} <= names


def test_save_attempt_stores_flags_and_point_counts(conn, points_question):
    import_questions(conn, [points_question])
    answer = "The balloon expands."
    model_output = {"points": [
        {"id": "MP1", "awarded": True, "evidence_quote": "balloon expands", "reason": "", "reversed": False},
        {"id": "MP2", "awarded": False, "evidence_quote": "", "reason": "", "reversed": False},
    ], "missing_key_terms": [], "filler_quotes": [], "misread_question": False, "borderline": True,
        "borderline_reason": "test", "full_marks_answer": "", "technique_note": ""}
    result = score(points_question, model_output, answer)
    result.update({"question_id": points_question["id"], "error": None, "model": "test-model",
                   "api_call_id": None, "usage": None, "model_output": model_output})

    attempt_id = save_attempt(conn, points_question, answer, result)
    row = conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
    assert row["marks_awarded"] == 1
    assert row["valid_points"] == 1 and row["counted_points"] == 1
    assert row["confidence"] == "medium"  # assessment book, down one for borderline
    assert json.loads(row["flags_json"]) == ["borderline"]
