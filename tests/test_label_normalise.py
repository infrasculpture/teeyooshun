"""Odd answer-key labels ('Q5(5)', 'a1') matched to unmatched questions of the same exam paper.
Made-up data only; no API calls."""

import json

import pytest

from app.db import connect
from app.ingest.assemble import (LABEL_PART_IS_NUMBER, LABEL_SECTION_STRIPPED, label_candidates, label_normalised)
from app.ingest.summary import file_summary, format_summary
from ingest_helpers import PAPERS_YAML, SOURCE_YAML, a, ingest, make_source, page, q

TOPIC = "sci-05-ray-model-of-light"


def _qs(*items):
    return [q(n, p, topic=TOPIC) for n, p in items]


def _paper(env, questions, answers, yaml_text=PAPERS_YAML):
    folder = make_source(env, "papers", yaml_text, {"2024_test_eoy.pdf": ["Instructions", "Q", "Key"]})
    ingest(folder, [page("other", type_reason="instructions"), page(questions=questions),
                    page("answer", answers=answers)])


def _items():
    rows = connect().execute("SELECT * FROM staged_items WHERE status = 'pending' ORDER BY id").fetchall()
    return {r["item_id"].rsplit("-", 1)[1]: (json.loads(r["record_json"]), json.loads(r["warnings_json"])) for r in rows}


def _summary():
    conn = connect()
    return "\n".join(line for f in conn.execute("SELECT id FROM source_files WHERE status != 'superseded'").fetchall()
                     for line in format_summary(file_summary(conn, f["id"])))


def _texts(record):
    return [mp["text"] for mp in record["mark_points"]]


# ---- The label rules on their own ---------------------------------------------------------------

@pytest.mark.parametrize("qnum, part, embedded, expected", [
    ("5", "5", True, [("5", "", LABEL_PART_IS_NUMBER)]),
    ("5", "5", False, [("5", "", LABEL_PART_IS_NUMBER)]),
    ("5", "2", True, []),  # Q5(2) is a real part
    ("5", "a", True, []),
    ("a1", "", True, [("1", "", LABEL_SECTION_STRIPPED)]),
    ("b10", "ci", True, [("10", "ci", LABEL_SECTION_STRIPPED)]),
    ("a1", "", False, []),  # a separate answer key: never
    ("c12q1", "", True, []),  # a stray token with no valid label: unchanged
    ("c12", "", True, [("12", "", LABEL_SECTION_STRIPPED)]),  # section C, question 12: still just letter + number
    ("ab1", "", True, []),
])
def test_label_candidates(qnum, part, embedded, expected):
    assert label_candidates(qnum, part, embedded) == expected


# ---- 'Q5(5)': part equals the question number ---------------------------------------------------

def test_part_equal_to_question_number_is_dropped_but_other_parts_are_not(env):
    _paper(env, _qs(("5", ""), ("6", "")), [a("Q5", "5", points=("Five",)), a("Q6", "2", points=("Six",))])
    items = _items()
    record, warnings = items["q5"]
    assert _texts(record) == ["Five"] and record["key_label"] == "Q5(5)"
    assert not any(w.startswith("Answer label") for w in warnings)  # mechanical: no per-item warning
    assert "No matching mark scheme found in the answer key" in items["q6"][1] and "key_label" not in items["q6"][0]
    text = _summary()
    assert "  Labels normalised: 1 (part = question number)" in text
    assert "Answers without a question (1): Q6(2)" in text


def test_a_raw_label_that_already_matches_is_left_alone(env):
    _paper(env, _qs(("5", "5"), ("5", "")), [a("Q5", "5", points=("Part five",))])
    items = _items()
    assert _texts(items["q55"][0]) == ["Part five"] and "key_label" not in items["q55"][0]
    assert "No matching mark scheme found in the answer key" in items["q5"][1]  # not given Q5(5)'s answer
    assert "Labels normalised" not in _summary()


def test_a_second_answer_never_attaches_to_a_question_that_has_one(env):
    _paper(env, _qs(("5", "")), [a("5", points=("Right",)), a("Q5", "5", points=("Wrong",))])
    items = _items()
    assert _texts(items["q5"][0]) == ["Right"] and "key_label" not in items["q5"][0]
    text = _summary()
    assert "Labels normalised" not in text and "Answers without a question (1): Q5(5)" in text


# ---- 'a1': section letter in front of the number ------------------------------------------------

def test_section_letter_is_stripped_for_an_unmatched_question_with_the_same_part(env):
    _paper(env, _qs(("1", ""), ("2", "a")),
           [a("A1", points=("One",)), a("B2", "a", points=("Two a",)), a("B1", points=("Clash",)), a("C9")])
    items = _items()
    assert _texts(items["q1"][0]) == ["One"] and items["q1"][0]["key_label"] == "A1"
    assert _texts(items["q2a"][0]) == ["Two a"] and items["q2a"][0]["key_label"] == "B2(a)"
    assert label_normalised("A1", "1", "", LABEL_SECTION_STRIPPED) in items["q1"][1]
    text = _summary()
    assert "  Labels normalised: 2 (section letter stripped)" in text
    assert "Answers without a question (2): B1, C9" in text  # B1 would be a second answer for Q1; C9 has no question


def test_section_letter_needs_the_same_part(env):
    _paper(env, _qs(("1", "a")), [a("A1", "b")])
    assert "Labels normalised" not in _summary()


# ---- numbering_overrides keep priority ----------------------------------------------------------

def test_numbering_overrides_win_over_label_normalising(env):
    yaml_text = PAPERS_YAML + 'numbering_overrides:\n  "2024_test_eoy.pdf": {"5": "7"}\n'
    _paper(env, _qs(("5", "")), [a("Q5", "5", points=("Five",)), a("Q7", "7", points=("Seven",))], yaml_text)
    record, _warnings = _items()["q5"]
    assert _texts(record) == ["Seven"]  # the override says Q5's answer is filed under 7
    text = _summary()
    assert "  Labels normalised: 1 (part = question number)" in text
    assert "Answers without a question (1): Q5(5)" in text


# ---- Separate answer keys are never touched -----------------------------------------------------

def test_a_separate_answer_key_orphan_is_not_matched_to_another_unit(env):
    folder = make_source(env, "test-book", SOURCE_YAML, {"answers.pdf": ["Answers 2", "Answers 1"],
                                                         "qns_c01.pdf": ["Q1 and Q3"]})
    ingest(folder, [
        page("answer", chapter="2", answers=[a("1", points=("Unit two",))]),  # restarts at Q1 in unit 2
        page("answer", chapter="1", answers=[a("B3", points=("Section letter",))]),
        page(questions=[q("1", topic=TOPIC), q("3", topic=TOPIC)]),
    ])
    items = _items()
    assert all("key_label" not in record for record, _w in items.values())
    assert all("No matching mark scheme found in the answer key" in w for _r, w in items.values())
    text = _summary()
    assert "Labels normalised" not in text
    assert "c02 Q1" in text and "c01 B3" in text  # both still orphans
