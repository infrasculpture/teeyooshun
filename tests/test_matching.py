"""Joining pages, duplicates, IDs, schemes and warnings (plain functions, made-up data)."""

from types import SimpleNamespace

import pytest

from app.ingest.assemble import (build_question, make_question_id, normalise_part, normalise_qnum, normalise_unit,
                                 scheme_fields, stitch_answers, stitch_questions)
from ingest_helpers import a, page, q

CFG = SimpleNamespace(subject="science", level="sec1", slug="test-book", title="Test Book",
                      key_reliability="assessment_book")
KNOWN = {"sci-01-scientific-endeavour", "sci-05-ray-model-of-light"}


@pytest.mark.parametrize("raw, expected", [("(a)", "a"), ("c(iii)", "ciii"), ("(B) ", "b"), ("", "")])
def test_normalise_part(raw, expected):
    assert normalise_part(raw) == expected


@pytest.mark.parametrize("raw, expected", [("Q2.", "2"), ("12", "12"), (" 3 ", "3")])
def test_normalise_qnum(raw, expected):
    assert normalise_qnum(raw) == expected


@pytest.mark.parametrize("raw, expected", [("Chapter 1", "01"), ("12", "12"), ("", "")])
def test_normalise_unit(raw, expected):
    assert normalise_unit(raw) == expected


def test_continuation_joins_previous_question():
    items = stitch_questions([
        (2, "5", page(questions=[q("1", "a"), q("1", "b", question="Explain why")])),
        (3, "6", page(questions=[q("", "", question="the sky is blue.", cont=True), q("2", question="Name it.")])),
    ])
    assert [(i["qnum"], i["part"]) for i in items] == [("1", "a"), ("1", "b"), ("2", "")]
    assert items[1]["question"] == "Explain why the sky is blue."
    assert items[1]["pages"] == [2, 3]


def test_boundary_page_duplicate_is_dropped():
    items = stitch_questions([
        (1, "1", page(questions=[q("4", "a", question="State X.")])),
        (2, "1", page(questions=[q("4", "a", question="State  x.")])),  # same page scanned twice
    ])
    assert len(items) == 1 and items[0]["pages"] == [1, 2]


def test_same_number_different_text_is_kept_and_flagged():
    items = stitch_questions([(1, "1", page(questions=[q("4", "a", question="State X."),
                                                       q("4", "a", question="Describe Y.")]))])
    assert len(items) == 2 and items[1].get("duplicate_number")


def test_answer_unit_follows_chapter_heading_across_pages():
    entries = stitch_answers([
        (1, "", page("answer", chapter="Chapter 1", answers=[a("1", "a")])),
        (2, "", page("answer", answers=[a("2")])),  # no heading: still chapter 1
        (3, "", page("answer", chapter="2", answers=[a("1")])),
    ])
    assert [(e["unit"], e["qnum"], e["part"]) for e in entries] == [("01", "1", "a"), ("01", "2", ""), ("02", "1", "")]


def test_answer_continuation_adds_points():
    entries = stitch_answers([
        (1, "", page("answer", chapter="1", answers=[a("3", points=["One"])])),
        (2, "", page("answer", answers=[a("", points=["Two"], cont=True)])),
    ])
    assert len(entries) == 1
    assert [mp["text"] for mp in entries[0]["mark_points"]] == ["One", "Two"]


def test_scheme_fields_points_with_requires_and_choose():
    entry = a("2", points=["Choice", "Reason", "Extra"], choose=2, requires={2: [1], 3: [9]}, key_terms=["bold"])
    fields = scheme_fields(entry)
    assert fields["marking_mode"] == "points" and fields["choose"] == 2
    assert fields["mark_points"][1] == {"id": "MP2", "text": "Reason", "key_terms": ["bold"], "requires": ["MP1"]}
    assert "requires" not in fields["mark_points"][2]  # pointed at a point that doesn't exist


def test_make_question_id():
    assert make_question_id("science", "sec1", "test-book", "qns_c01", "2", "f") == "science-sec1-test-book-qns_c01-q2f"


def _build(item, scheme, file_topic="sci-01-scientific-endeavour"):
    item = dict(item, pages=[1], printed_page="5")
    return build_question(item, cfg=CFG, file_stem="qns_c01", file_topic=file_topic, scheme=scheme, known_topics=KNOWN)


def test_matched_question_has_clean_record():
    record, warnings = _build(q("2", "f"), a("2", "f"))
    assert warnings == []
    assert record["id"] == "science-sec1-test-book-qns_c01-q2f"
    assert record["source_ref"] == "Test Book, p.5, Q2(f)"
    assert record["source"] == "assessment_book" and record["topic"] == "sci-01-scientific-endeavour"
    assert [mp["id"] for mp in record["mark_points"]] == ["MP1", "MP2"]


def test_unmatched_question_warns():
    _record, warnings = _build(q("2"), None)
    assert "No matching mark scheme found in the answer key" in warnings


@pytest.mark.parametrize("item, scheme, expected", [
    (q("1", marks=0), a("1"), "No marks shown for this question"),
    (q("1", marks=3), a("1"), "Fewer mark points (2) than marks (3) and no 'any N'"),
    (q("1", figure="needed_to_answer"), a("1"), "Depends on a figure but has no page image to show"),
    (q("1", answer_format="draw_or_label"), a("1"), "Drawing or labelling question: left out of typed practice"),
    (q("1", uncertain=True, reason="smudged"), a("1"), "Model unsure about the question: smudged"),
])
def test_review_warnings(item, scheme, expected):
    assert expected in _build(item, scheme)[1]


def test_unassigned_topic_warns():
    assert "Topic is unassigned" in _build(q("1"), a("1"), file_topic="unassigned")[1]


def test_exam_paper_topic_from_model_checked_against_tree():
    record, warnings = _build(q("1", topic="sci-05-ray-model-of-light"), a("1"), file_topic=None)
    assert record["topic"] == "sci-05-ray-model-of-light" and warnings == []
    record, warnings = _build(q("1", topic="sci-light"), a("1"), file_topic=None)
    assert record["topic"] == "unassigned"
    assert "The model suggested topic 'sci-light', which is not in topics.yaml" in warnings


def test_mcq_record():
    item = q("5", question="Which is a metal?", marks=1, is_mcq=True,
             options=[("A", "Wood"), ("B", "Iron"), ("C", "Glass"), ("D", "Paper")])
    record, warnings = _build(item, a("5", mode="mcq", correct="b"))
    assert record["marking_mode"] == "mcq" and record["correct_option"] == "B"
    assert [o["label"] for o in record["options"]] == ["A", "B", "C", "D"]
    assert warnings == []
