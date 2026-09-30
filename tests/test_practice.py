"""Attempt and rewrite rules, question versions and scores (fake marker, no API calls)."""

import copy

import pytest

from app import practice
from app.importer import get_question, import_questions, load_file
from app.llm import LLMError
from conftest import CAP_QUESTION, FIXTURE, ONE_MARK_QUESTION, attempt_count

POINTS_Q = "sci-test-points-q1"
PART = "The balloon expands."  # 1 of 2 marks with the fake marker
FULL = "The balloon expands as particles move further apart."  # 2 of 2


def test_first_attempt_saved_with_version_and_time(bank, fake_marker):
    attempt_id = practice.submit_first_attempt(bank, POINTS_Q, PART, seconds_taken=42)
    row = practice.get_attempt(bank, attempt_id)
    assert row["marks_awarded"] == 1 and row["max_marks"] == 2
    assert row["question_version"] == 1
    assert row["seconds_taken"] == 42
    assert row["parent_attempt_id"] is None


def test_failed_marking_saves_nothing(bank, fake_marker):
    with pytest.raises(practice.MarkingFailed):
        practice.submit_first_attempt(bank, POINTS_Q, "FAIL")
    assert attempt_count(bank) == 0


def test_fatal_api_error_saves_nothing(bank, monkeypatch):
    def broken(*args, **kwargs):
        raise LLMError("api_error", "401: bad key", fatal=True)
    monkeypatch.setattr("app.marking.marker.mark_answer", broken)
    with pytest.raises(practice.MarkingFailed):
        practice.submit_first_attempt(bank, POINTS_Q, PART)
    assert attempt_count(bank) == 0


def test_rewrite_offered_after_lost_marks(bank, fake_marker):
    first = practice.get_attempt(bank, practice.submit_first_attempt(bank, POINTS_Q, PART))
    assert practice.rewrite_status(bank, first) == "available"


def test_no_rewrite_on_full_marks(bank, fake_marker):
    first = practice.get_attempt(bank, practice.submit_first_attempt(bank, POINTS_Q, FULL))
    assert practice.rewrite_status(bank, first) == "not_needed"
    with pytest.raises(practice.NotAllowed):
        practice.submit_rewrite(bank, first["id"], FULL)


def test_no_rewrite_on_one_mark_question(bank, fake_marker):
    import_questions(bank, [ONE_MARK_QUESTION])
    first = practice.get_attempt(bank, practice.submit_first_attempt(bank, "sci-test-one-mark", "Evaporation"))
    assert first["marks_awarded"] == 0
    assert practice.rewrite_status(bank, first) == "not_needed"


def test_one_rewrite_linked_to_first_attempt(bank, fake_marker):
    first_id = practice.submit_first_attempt(bank, POINTS_Q, PART)
    rewrite_id = practice.submit_rewrite(bank, first_id, FULL)
    rewrite = practice.get_attempt(bank, rewrite_id)
    assert rewrite["parent_attempt_id"] == first_id
    assert rewrite["marks_awarded"] == 2
    assert practice.rewrite_status(bank, practice.get_attempt(bank, first_id)) == "rewritten"
    assert practice.rewrite_status(bank, rewrite) == "not_needed"  # no rewrite of a rewrite
    with pytest.raises(practice.NotAllowed):
        practice.submit_rewrite(bank, first_id, FULL)  # second rewrite
    with pytest.raises(practice.NotAllowed):
        practice.skip_rewrite(bank, first_id)  # can't skip after rewriting
    assert attempt_count(bank) == 2


def test_failed_rewrite_saves_nothing_and_can_be_retried(bank, fake_marker):
    first_id = practice.submit_first_attempt(bank, POINTS_Q, PART)
    with pytest.raises(practice.MarkingFailed):
        practice.submit_rewrite(bank, first_id, "FAIL")
    assert practice.rewrite_status(bank, practice.get_attempt(bank, first_id)) == "available"


def test_skip_is_recorded_and_blocks_rewrite(bank, fake_marker):
    first_id = practice.submit_first_attempt(bank, POINTS_Q, PART)
    practice.skip_rewrite(bank, first_id)
    events = practice.events_for(bank, first_id)
    assert [(e["event"], e["actor"]) for e in events] == [("rewrite_skipped", "student")]
    assert practice.rewrite_status(bank, practice.get_attempt(bank, first_id)) == "skipped"
    with pytest.raises(practice.NotAllowed):
        practice.submit_rewrite(bank, first_id, FULL)
    with pytest.raises(practice.NotAllowed):
        practice.skip_rewrite(bank, first_id)  # only one skip


def test_scores_count_first_attempts_only(bank, fake_marker):
    first_id = practice.submit_first_attempt(bank, POINTS_Q, PART)  # 1/2
    practice.submit_rewrite(bank, first_id, FULL)  # 2/2, doesn't count
    assert practice.first_attempt_scores(bank)["science"] == {"marks": 1, "max": 2, "pct": 50}


def test_answering_again_later_is_a_new_first_attempt(bank, fake_marker):
    practice.submit_first_attempt(bank, POINTS_Q, PART)
    practice.submit_first_attempt(bank, POINTS_Q, FULL)
    assert practice.first_attempt_scores(bank)["science"] == {"marks": 3, "max": 4, "pct": 75}


def _edited_points_question():
    q = copy.deepcopy(load_file(FIXTURE)["questions"][0])
    q["question"] = "EDITED: describe and explain what happens."
    return q


def test_edit_bumps_version_and_old_attempts_keep_theirs(bank, fake_marker):
    first_id = practice.submit_first_attempt(bank, POINTS_Q, PART)
    assert import_questions(bank, [_edited_points_question()])["updated"] == 1
    assert get_question(bank, POINTS_Q)["version"] == 2
    old = practice.question_for_attempt(bank, practice.get_attempt(bank, first_id))
    assert old["version"] == 1 and not old["question"].startswith("EDITED")
    # Re-importing the same content changes nothing and adds no duplicate.
    assert import_questions(bank, [_edited_points_question()]) == {"added": 0, "updated": 0, "unchanged": 1}
    assert bank.execute("SELECT COUNT(*) FROM questions WHERE id = ?", (POINTS_Q,)).fetchone()[0] == 1
    assert bank.execute("SELECT COUNT(*) FROM question_versions WHERE question_id = ?", (POINTS_Q,)).fetchone()[0] == 2


def test_rewrite_is_marked_against_the_version_she_answered(bank, fake_marker):
    first_id = practice.submit_first_attempt(bank, POINTS_Q, PART)
    import_questions(bank, [_edited_points_question()])
    rewrite_id = practice.submit_rewrite(bank, first_id, FULL)
    assert fake_marker[-1]["question"]["version"] == 1
    assert not fake_marker[-1]["question"]["question"].startswith("EDITED")
    assert practice.get_attempt(bank, rewrite_id)["question_version"] == 1


def test_next_question_prefers_untried(bank, fake_marker):
    import_questions(bank, [ONE_MARK_QUESTION])
    assert practice.next_question_id(bank, "science") == "sci-test-one-mark"
    practice.submit_first_attempt(bank, "sci-test-one-mark", "Diffusion")
    assert practice.next_question_id(bank, "science") == POINTS_Q
    assert practice.next_question_id(bank, "science", "sci-01-scientific-endeavour") is None


def test_cap_recorded_on_attempt(bank, fake_marker):
    import_questions(bank, [CAP_QUESTION])
    row = practice.get_attempt(bank, practice.submit_first_attempt(bank, "sci-test-cap", "alpha, beta and gamma"))
    assert (row["valid_points"], row["counted_points"], row["marks_awarded"]) == (3, 2, 2)


@pytest.mark.parametrize("shown_at, now, expected", [
    ("1000", 1090, 90),
    ("1000", 900, None),  # in the future
    ("1000", 1000 + 7 * 3600, None),  # page left open for hours
    ("", 1000, None),
    ("abc", 1000, None),
])
def test_seconds_since(shown_at, now, expected):
    assert practice.seconds_since(shown_at, now) == expected


def test_clean_answer():
    assert practice.clean_answer("  hello  ") == "hello"
    with pytest.raises(ValueError):
        practice.clean_answer("   ")
    with pytest.raises(ValueError):
        practice.clean_answer("x" * (practice.MAX_ANSWER_CHARS + 1))


@pytest.mark.parametrize("topic_id, label", [
    ("sci-01-scientific-endeavour", "Scientific Endeavour"),
    ("sci-07-particulate-nature-of-matter", "Particulate Nature of Matter"),
    ("his-u1-temasek-to-singapore", "Temasek to Singapore"),
])
def test_topic_label(topic_id, label):
    assert practice.topic_label(topic_id) == label
