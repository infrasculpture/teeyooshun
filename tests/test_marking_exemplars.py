"""LORMS exemplars in the marking prompt (no API calls)."""

import json

from app.marking.marker import mark_answer
from app.marking.prompts import LORMS_SYSTEM, render_scheme, system_prompt
from ingest_helpers import FakeClient

NOTE = "Exemplars illustrate the standard of each level."
INDICATIVE = ("Inferences, examples and exemplars listed in the mark scheme are indicative, not a complete"
              " list, unless the scheme explicitly says only those are accepted. Credit any valid inference"
              " that is supported by the source and answers the question, at the level its support and"
              " explanation justify.")

LORMS_OUTPUT = {
    "features": [], "describes_without_inference": True, "proposed_level": 1, "proposed_marks": 1,
    "descriptor_matched": "", "why_not_higher": "", "borderline": False, "borderline_reason": "",
    "borderline_other_marks": 1, "peel_map": {"point": "", "evidence": "", "explain": "", "link": "MISSING"},
    "full_marks_answer": "", "technique_note": "",
}


def _with_exemplars(question):
    levels = sorted(question["lorms"], key=lambda lv: lv["level"])
    levels[0]["exemplars"] = ["Weak model answer."]
    levels[-1]["exemplars"] = ["Best model answer.", "Another best answer."]
    return question


def test_exemplars_are_shown_labelled_by_level(lorms_question):
    question = _with_exemplars(lorms_question)
    top = max(lv["level"] for lv in question["lorms"])
    low = min(lv["level"] for lv in question["lorms"])
    shown = json.loads(render_scheme(question).split("\n", 1)[1].rsplit("\n", 1)[0])
    assert shown["exemplars"] == [
        {"level": low, "answer": "Weak model answer."},
        {"level": top, "answer": "Best model answer."},
        {"level": top, "answer": "Another best answer."},
    ]
    assert all("exemplars" not in lv for lv in shown["levels"])
    assert NOTE in system_prompt(question)


def test_prompt_is_unchanged_without_exemplars(lorms_question):
    assert "exemplars" not in render_scheme(lorms_question)
    assert system_prompt(lorms_question) == LORMS_SYSTEM
    lorms_question["lorms"][0]["exemplars"] = []  # an empty list or blank text counts as none
    lorms_question["lorms"][1]["exemplars"] = ["  "]
    assert "exemplars" not in render_scheme(lorms_question)
    assert system_prompt(lorms_question) == LORMS_SYSTEM


def test_indicative_note_is_in_every_lorms_prompt(lorms_question):
    assert INDICATIVE in LORMS_SYSTEM
    plain = system_prompt(lorms_question)
    assert INDICATIVE in plain
    assert NOTE not in plain  # exemplar line only when there are exemplars
    assert INDICATIVE in system_prompt(_with_exemplars(lorms_question))  # edits the question in place


def test_lorms_output_asks_for_the_other_borderline_mark(lorms_question):
    from app.marking.prompts import output_schema
    schema = output_schema(lorms_question)
    assert schema["properties"]["borderline_other_marks"] == {"type": "integer"}
    assert "borderline_other_marks" in schema["required"]
    assert "borderline_other_marks" in system_prompt(lorms_question)


def test_exemplar_prompt_is_identical_between_calls(lorms_question):
    question = _with_exemplars(lorms_question)
    assert render_scheme(question) == render_scheme(question)
    assert system_prompt(question) == system_prompt(question)


def test_exemplars_sit_in_the_cached_question_block(bank, lorms_question):
    client = FakeClient([LORMS_OUTPUT])
    mark_answer(bank, _with_exemplars(lorms_question), "The notice lists many rules.", client=client)
    request = client.requests[0]
    content = request["messages"][0]["content"]
    assert content[0]["text"].startswith("<mark_scheme>") and "Best model answer." in content[0]["text"]
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert content[1]["text"].startswith("<student_answer>") and "cache_control" not in content[1]
    assert "Best model answer." not in content[1]["text"]
    assert NOTE in request["system"][0]["text"] and request["system"][0]["cache_control"] == {"type": "ephemeral"}
