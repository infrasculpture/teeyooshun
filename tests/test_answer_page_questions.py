"""Exam papers with their key: questions printed on answer pages, answers printed on question pages,
unnumbered answer labels in a one-question file, and page headers that are not units.
Made-up data only; no API calls."""

import json

import pytest

from app.db import connect
from app.ingest import notes, prompts
from app.ingest.assemble import (FROM_ANSWER_PAGE, LABEL_SINGLE_QUESTION, label_candidates, label_normalised,
                                 stitch_answers, stitch_questions)
from app.ingest.reassemble import reread_needs
from ingest_helpers import PAPERS_YAML, a, ingest, level, make_source, page, q

TOPIC = "sci-05-ray-model-of-light"
LEVELS = [level(1, 1, 2), level(2, 3, 4), level(3, 5, 6)]


def _paper(env, pages, names=None):
    folder = make_source(env, "papers", PAPERS_YAML, {"2024_test_eoy.pdf": names or [f"P{i}" for i in range(len(pages))]})
    ingest(folder, pages)
    return folder


def _items():
    rows = connect().execute("SELECT * FROM staged_items WHERE status = 'pending' ORDER BY id").fetchall()
    return {r["item_id"].rsplit("-", 1)[1]: (json.loads(r["record_json"]), json.loads(r["warnings_json"])) for r in rows}


def _seq(qnum="1", question="Evaluate the claim.", marks=6):
    return page("answer", questions=[q(qnum, question=question, marks=marks, topic=TOPIC)],
                answers=[a(qnum, mode="lorms", lorms=LEVELS)])


# ---- 1. A question printed on an answer page -----------------------------------------------------

def test_seq_page_gives_a_question_matched_to_its_level_table(env):
    _paper(env, [_seq()])
    items = _items()
    assert list(items) == ["q1"]
    record, warnings = items["q1"]
    assert record["question"] == "Evaluate the claim." and record["marks"] == 6
    assert record["marking_mode"] == "lorms" and len(record["lorms"]) == 3
    assert FROM_ANSWER_PAGE in warnings and "No matching mark scheme found in the answer key" not in warnings
    assert "page_images" not in record  # an answer page is never shown to the student
    conn = connect()
    f = conn.execute("SELECT id FROM source_files").fetchone()
    from app.ingest.staging import numbering_check
    assert not any("answer key has" in p for p in numbering_check(conn, f["id"]))


def test_no_duplicate_when_the_question_is_also_on_a_question_page(env):
    _paper(env, [page(questions=[q("1", question="Evaluate the claim.", marks=6, topic=TOPIC)]),
                 _seq()])
    items = _items()
    assert list(items) == ["q1"]
    assert FROM_ANSWER_PAGE not in items["q1"][1]
    assert items["q1"][0]["marking_mode"] == "lorms"  # still matched to the answer


def test_no_duplicate_when_the_question_page_has_the_parts(env):
    _paper(env, [page(questions=[q("1", "a", topic=TOPIC), q("1", "b", topic=TOPIC)]), _seq("1")])
    assert sorted(_items()) == ["q1a", "q1b"]


def test_a_question_missing_from_the_question_pages_is_added_beside_the_others(env):
    _paper(env, [page(questions=[q("1", topic=TOPIC)]), page("answer", answers=[a("1")]), _seq("2")])
    items = _items()
    assert sorted(items) == ["q1", "q2"]
    assert FROM_ANSWER_PAGE in items["q2"][1] and FROM_ANSWER_PAGE not in items["q1"][1]


def test_only_embedded_key_files_take_questions_from_answer_pages():
    pages = [(1, "1", dict(_seq(), page_type="answer"))]
    assert stitch_questions(pages, embedded_key=True)[0]["from_answer_page"] is True
    assert "from_answer_page" not in stitch_questions(pages)[0]


def test_sources_on_an_earlier_page_are_attached_to_a_question_from_an_answer_page(env):
    from ingest_helpers import src
    seq = page("answer", questions=[q("1", "a", question="What does Source A show?", topic=TOPIC)],
               answers=[a("1", "a", mode="lorms", lorms=LEVELS)])
    _paper(env, [page("sources", sources=[src("A", text="A made-up poster about harvests.")]), seq])
    record, warnings = _items()["q1a"]
    assert "A made-up poster about harvests." in record["context"] and FROM_ANSWER_PAGE in warnings
    assert not any("no text for it was found" in w for w in warnings)


# ---- 2. An answer printed on a question page -----------------------------------------------------

def test_content_page_with_an_embedded_level_table_gives_a_key_entry(env):
    _paper(env, [page("content", questions=[q("1", marks=6, topic=TOPIC)], answers=[a("1", mode="lorms", lorms=LEVELS)])])
    record, warnings = _items()["q1"]
    assert record["marking_mode"] == "lorms" and len(record["lorms"]) == 3
    assert "No matching mark scheme found in the answer key" not in warnings
    assert "page_images" not in record  # the page shows the answer, so it is never shown


# ---- 3. Q(a) in a one-question file --------------------------------------------------------------

def test_label_candidates_single_question():
    assert label_candidates("", "a", True, "1") == [("1", "a", LABEL_SINGLE_QUESTION)]
    assert label_candidates("", "a", True, None) == []
    assert label_candidates("", "a", False, "1") == []
    assert label_candidates("", "", True, "1") == []


def test_unnumbered_parts_take_the_only_question_number(env):
    _paper(env, [page(questions=[q("1", "a", topic=TOPIC), q("1", "b", topic=TOPIC)]),
                 page("answer", answers=[a("", "a", points=("Alpha",)), a("", "b", points=("Beta",))])])
    items = _items()
    assert [mp["text"] for mp in items["q1a"][0]["mark_points"]] == ["Alpha"]
    assert [mp["text"] for mp in items["q1b"][0]["mark_points"]] == ["Beta"]
    assert label_normalised("Q(a)", "1", "a", LABEL_SINGLE_QUESTION) in items["q1a"][1]


def test_unnumbered_parts_are_left_alone_when_the_file_has_two_questions(env):
    _paper(env, [page(questions=[q("1", "a", topic=TOPIC), q("2", "a", topic=TOPIC)]),
                 page("answer", answers=[a("", "a", points=("Alpha",))])])
    for record, warnings in _items().values():
        assert "key_label" not in record and "No matching mark scheme found in the answer key" in warnings


def test_unnumbered_part_never_takes_a_part_that_already_has_an_answer(env):
    _paper(env, [page(questions=[q("1", "a", topic=TOPIC)]),
                 page("answer", answers=[a("1", "a", points=("Right",)), a("", "a", points=("Wrong",))])])
    record, _warnings = _items()["q1a"]
    assert [mp["text"] for mp in record["mark_points"]] == ["Right"] and "key_label" not in record


# ---- 4. Page headers are not units ----------------------------------------------------------------

def test_headers_are_not_units_for_an_exam_paper_with_key():
    pages = [(1, "1", dict(page("answer", chapter="19", answers=[a("1", mode="lorms", lorms=[level(1, 1, 2)])]))),
             (2, "2", dict(page("answer", chapter="20", answers=[a("", "", mode="lorms", lorms=[level(2, 3, 4)])])))]
    entries = stitch_answers(pages, ignore_units=True)
    assert len(entries) == 1 and entries[0]["unit"] == "" and [lv["level"] for lv in entries[0]["lorms"]] == [1, 2]
    separate = stitch_answers(pages)  # a separate answer key still uses the headings
    assert [e["unit"] for e in separate] == ["19", "20"]


def test_key_entries_of_an_exam_paper_have_no_unit(env):
    _paper(env, [page(questions=[q("1", topic=TOPIC)]),
                 page("answer", chapter="Chapter 19", answers=[a("1")])])
    assert [r["unit"] for r in connect().execute("SELECT unit FROM key_entries")] == [""]


# ---- Prompt change: exam_paper_with_key only -------------------------------------------------------

MIXED = "Mixed pages"


def test_mixed_page_wording_is_only_in_the_exam_paper_prompt():
    assert MIXED in prompts.EXAM_PAPER
    others = {t: p for t, p in prompts.TYPE_PROMPTS.items() if t != "exam_paper_with_key"}
    assert others and not any(MIXED in p for p in others.values())
    for material_type in prompts.TYPE_PROMPTS:
        text = prompts.system_blocks(material_type, "science")[0]["text"]
        assert (MIXED in text) == (material_type == "exam_paper_with_key")
    assert MIXED not in prompts.COMMON and MIXED not in prompts.CARDS_COMMON and MIXED not in notes.COMMON


# ---- reassemble --report ---------------------------------------------------------------------------

def test_report_lists_answer_pages_with_no_question_for_their_number(env):
    _paper(env, [page(questions=[q("1", topic=TOPIC)]), page("answer", answers=[a("1"), a("2")])])
    conn = connect()
    f = conn.execute("SELECT * FROM source_files").fetchone()
    assert reread_needs(conn, f)["questions"] == [2]


def test_report_leaves_a_page_alone_once_its_question_was_read(env):
    _paper(env, [_seq()])
    conn = connect()
    assert reread_needs(conn, conn.execute("SELECT * FROM source_files").fetchone())["questions"] == []
