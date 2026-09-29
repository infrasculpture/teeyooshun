"""Importer: checks and loading, using the made-up fixture file."""

import json

import pytest

from app.importer import QuestionFileError, get_question, import_questions, load_file, validate_question
from conftest import FIXTURE


def test_fixture_loads(sample_data):
    data = load_file(FIXTURE)
    assert len(data["questions"]) == 2


def test_import_strips_answers_and_is_repeatable(conn):
    data = load_file(FIXTURE)
    assert import_questions(conn, data["questions"]) == {"added": 2, "updated": 0, "unchanged": 0}
    assert import_questions(conn, data["questions"]) == {"added": 0, "updated": 0, "unchanged": 2}
    stored = get_question(conn, "sci-test-points-q1")
    assert "answers" not in stored
    assert stored["mark_points"][1]["requires"] == ["MP1"]


def test_changed_question_is_updated(conn, sample_data):
    import_questions(conn, sample_data["questions"])
    sample_data["questions"][0]["question"] = "Changed wording."
    assert import_questions(conn, sample_data["questions"])["updated"] == 1


def test_optional_fields_can_be_missing(points_question):
    for key in ("choose", "rejects", "answers", "source_ref", "context"):
        points_question.pop(key, None)
    for mp in points_question["mark_points"]:
        mp.pop("requires", None)
        mp.pop("key_terms", None)
    assert validate_question(points_question) == []


def test_bad_file_lists_every_problem(tmp_path, sample_data):
    q = sample_data["questions"][0]
    q["source"] = "internet"
    q["mark_points"][1]["requires"] = ["MP7"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(sample_data), encoding="utf-8")
    with pytest.raises(QuestionFileError) as e:
        load_file(path)
    text = str(e.value)
    assert "'source' must be one of" in text
    assert "unknown mark point 'MP7'" in text


def test_invalid_json_is_reported(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(QuestionFileError, match="not valid JSON"):
        load_file(path)


def test_overlapping_lorms_levels_rejected(lorms_question):
    lorms_question["lorms"][1]["marks_max"] = 4  # overlaps level 3 (4-5)
    assert any("overlap" in p for p in validate_question(lorms_question))


def test_gap_in_lorms_levels_rejected(lorms_question):
    lorms_question["lorms"][2]["marks_min"] = 5  # level 2 ends at 3, level 3 now starts at 5
    lorms_question["lorms"][2]["count"]["marks_by_count"] = {"1": 5}
    assert any("gap" in p for p in validate_question(lorms_question))


def test_valid_count_block_accepted(lorms_question):
    assert validate_question(lorms_question) == []


@pytest.mark.parametrize("count, message", [
    ({"basis": "some", "marks_by_count": {"1": 4}}, "'basis' must be"),
    ({"basis": "any", "marks_by_count": {"one": 4}}, "must be a whole number of 1 or more"),
    ({"basis": "any", "marks_by_count": {"0": 4}}, "must be a whole number of 1 or more"),
    ({"basis": "any", "marks_by_count": {"1": 9}}, "must be a whole number from 4 to 5"),
    ({"basis": "any", "marks_by_count": {}}, "must be a non-empty object"),
])
def test_bad_count_block_rejected(lorms_question, count, message):
    lorms_question["lorms"][2]["count"] = count
    assert any(message in p for p in validate_question(lorms_question))


def test_expected_marks_above_question_marks_rejected(points_question):
    points_question["answers"][0]["expected_marks"] = 3
    assert any("expected_marks" in p for p in validate_question(points_question))
