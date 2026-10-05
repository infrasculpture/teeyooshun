"""Exam papers with sections (A, B, C): section letters carried over pages, sections aligned
by position, source.yaml overrides, MCQ marks, and the per-section numbering check.
Made-up data only; no API calls."""

import json

import pytest

from app.db import connect
from app.ingest import prompts
from app.ingest.assemble import (align_sections, matched_by_position, numbering_problems, split_qnum,
                                 stitch_answers, stitch_questions)
from app.ingest.summary import file_summary, format_summary
from app.sources import SourceError, load_source
from ingest_helpers import PAPERS_YAML, a, ingest, make_source, page, q

TOPIC = "sci-05-ray-model-of-light"
OPTIONS = [("A", "Wood"), ("B", "Iron"), ("C", "Glass"), ("D", "Paper")]


def _by_suffix():
    rows = connect().execute("SELECT * FROM staged_items WHERE status = 'pending' ORDER BY id").fetchall()
    return {r["item_id"].rsplit("-", 1)[1]: (json.loads(r["record_json"]), json.loads(r["warnings_json"])) for r in rows}


def _summary_text():
    conn = connect()
    file_id = conn.execute("SELECT id FROM source_files WHERE status != 'superseded'").fetchone()["id"]
    return "\n".join(format_summary(file_summary(conn, file_id)))


# ---- 1. Section letter inherited with the number ------------------------------------------------

@pytest.mark.parametrize("qnum, expected", [("b5", ("b", 5)), ("12", ("", 12)), ("we2", (None, None)), ("", (None, None))])
def test_split_qnum(qnum, expected):
    assert split_qnum(qnum) == expected


def test_question_continuing_on_a_new_page_keeps_its_section_letter():
    items = stitch_questions([
        (1, "4", page(questions=[q("B5", "a"), q("B5", "b")])),
        (2, "5", page(questions=[q("5", "c"), q("", "d"), q("B6", "a")])),
    ])
    assert [(i["qnum"], i["part"]) for i in items] == [("b5", "a"), ("b5", "b"), ("b5", "c"), ("b5", "d"), ("b6", "a")]
    assert items[2]["number_inherited"] and items[3]["number_inherited"] and not items[4].get("number_inherited")


def test_a_different_number_does_not_take_the_section_letter():
    items = stitch_questions([(1, "1", page(questions=[q("B5", "a"), q("7", "a")]))])
    assert [i["qnum"] for i in items] == ["b5", "7"]


def test_answer_key_keeps_its_section_letter_too():
    entries = stitch_answers([
        (1, "", page("answer", answers=[a("B5", "a")])),
        (2, "", page("answer", answers=[a("5", "b"), a("", "c")])),
    ])
    assert [(e["qnum"], e["part"]) for e in entries] == [("b5", "a"), ("b5", "b"), ("b5", "c")]


def test_prompt_asks_for_section_letters():
    assert 'including a section letter when the paper prints one ("B5", "C12")' in prompts.QUESTION_RULES
    assert "never change a number to make it agree with the question paper" in prompts.ANSWER_RULES


# ---- 2. Sections aligned by position ------------------------------------------------------------

PAPER_C = [("c12", "a"), ("c12", "b"), ("c13", "")]
KEY_C = [("c1", "a"), ("c1", "b"), ("c2", "")]


def test_section_with_different_numbers_is_aligned_in_order():
    assert align_sections(PAPER_C, KEY_C) == {"c12": "c1", "c13": "c2"}


def test_sub_parts_count_as_their_parent_part_when_comparing_structure():
    paper = [("c12", "a"), ("c12", "bi"), ("c12", "bii")]
    assert align_sections(paper, [("c1", "a"), ("c1", "b")]) == {"c12": "c1"}


@pytest.mark.parametrize("paper, key", [
    (PAPER_C, KEY_C + [("c3", "")]),  # different number of questions
    (PAPER_C, [("c1", "a"), ("c2", "")]),  # different part structure
    (PAPER_C, [("b1", "a"), ("b1", "b"), ("b2", "")]),  # the key has no section C
    ([("a1", ""), ("a2", "")], [("a2", ""), ("a1", "")]),  # the numbers already agree
])
def test_no_alignment_unless_counts_and_structure_match(paper, key):
    assert align_sections(paper, key) == {}


def test_only_the_differing_section_is_aligned():
    paper = [("a1", ""), ("a2", "")] + PAPER_C
    key = [("a1", ""), ("a2", "")] + KEY_C
    assert align_sections(paper, key) == {"c12": "c1", "c13": "c2"}


def _paper(env, yaml_text=PAPERS_YAML, extra_question=None, key=None):
    folder = make_source(env, "papers", yaml_text, {"2024_test_eoy.pdf": ["Instructions", "Q", "Q", "Key"]})
    section_c = [q("C12", "a", topic=TOPIC), q("C12", "b", topic=TOPIC), q("C13", topic=TOPIC)]
    if extra_question:
        section_c.append(extra_question)
    ingest(folder, [
        page("other", type_reason="instructions"),
        page(questions=[q("A1", question="Which is a metal?", marks=0, is_mcq=True, options=OPTIONS, topic=TOPIC),
                        q("B5", "a", topic=TOPIC)]),
        page(questions=[q("5", "b", topic=TOPIC)] + section_c),
        page("answer", answers=[a("A1", mode="mcq", correct="B"), a("B5", "a"), a("B5", "b")]
             + (key or [a("C1", "a"), a("C1", "b"), a("C2")])),
    ])


def test_paper_sections_match_their_key_by_position_with_a_warning(env):
    _paper(env)
    items = _by_suffix()
    assert set(items) == {"qa1", "qb5a", "qb5b", "qc12a", "qc12b", "qc13"}
    record, warnings = items["qc12a"]
    assert record["key_number"] == "C1" and len(record["mark_points"]) == 2
    assert warnings == [matched_by_position("c12", "c1")]
    assert items["qc13"][0]["key_number"] == "C2"
    assert items["qb5b"][1] == [] and "key_number" not in items["qb5b"][0]  # B5(b) found under its own number
    text = _summary_text()
    assert "    C12 -> C1 (by position)" in text and "    C13 -> C2 (by position)" in text
    assert "Numbering matched by position (confirm): 3" in text
    assert "Numbering check:" not in text and "Answers without a question" not in text


def test_no_alignment_when_the_key_has_a_different_count(env):
    _paper(env, extra_question=q("C14", topic=TOPIC))
    items = _by_suffix()
    assert all("key_number" not in items[k][0] for k in ("qc12a", "qc13", "qc14"))
    assert "No matching mark scheme found in the answer key" in items["qc12a"][1]
    text = _summary_text()
    assert "The answer key has C1 but no question C1 was found" in text


# ---- 3. Overrides in source.yaml ---------------------------------------------------------------

OVERRIDES = PAPERS_YAML + 'numbering_overrides:\n  "2024_test_eoy.pdf": {"C12": "C1", "C13": "C2"}\n'


def test_overrides_are_read_from_source_yaml(tmp_path):
    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "source.yaml").write_text(OVERRIDES, encoding="utf-8")
    cfg = load_source(folder)
    assert cfg.numbering_overrides == {"2024_test_eoy.pdf": {"C12": "C1", "C13": "C2"}} and cfg.warnings == []
    (folder / "source.yaml").write_text(PAPERS_YAML + "numbering_overrides: [1, 2]\n", encoding="utf-8")
    with pytest.raises(SourceError, match="numbering_overrides"):
        load_source(folder)


def test_override_resolves_what_alignment_cannot(env):
    _paper(env, yaml_text=OVERRIDES, extra_question=q("C14", topic=TOPIC))  # counts differ: no automatic alignment
    items = _by_suffix()
    record, warnings = items["qc12a"]
    assert record["key_number"] == "C1" and warnings == []  # an override needs no confirmation warning
    assert items["qc13"][0]["key_number"] == "C2"
    assert "No matching mark scheme found in the answer key" in items["qc14"][1]
    assert "    C12 -> C1 (override)" in _summary_text()


def test_override_takes_priority_over_alignment(env):
    yaml_text = PAPERS_YAML + 'numbering_overrides:\n  "2024_test_eoy.pdf": {"C13": "C1"}\n'
    folder = make_source(env, "papers", yaml_text, {"2024_test_eoy.pdf": ["Q", "Key"]})
    ingest(folder, [
        page(questions=[q("C12", topic=TOPIC), q("C13", topic=TOPIC)]),
        page("answer", answers=[a("C1", points=["From C1", "x"]), a("C2", points=["From C2", "y"])]),
    ])
    items = _by_suffix()
    # By position C13 would be C2; the override says C1 and wins, with no confirmation warning.
    assert items["qc13"][0]["mark_points"][0]["text"] == "From C1" and items["qc13"][1] == []
    # C12 has no override, so it is still aligned by position.
    assert items["qc12"][0]["key_number"] == "C1" and items["qc12"][1] == [matched_by_position("c12", "c1")]


# ---- 4. MCQ marks ---------------------------------------------------------------------

def test_mcq_without_printed_marks_defaults_to_one_with_no_warning(env):
    _paper(env)
    record, warnings = _by_suffix()["qa1"]
    assert record["marking_mode"] == "mcq" and record["marks"] == 1 and record["correct_option"] == "B"
    assert warnings == []


def test_mcq_takes_the_sections_stated_marks(env):
    folder = make_source(env, "marks", PAPERS_YAML, {"2024_test_eoy.pdf": ["Instructions", "Q", "Key"]})
    ingest(folder, [
        page("other", type_reason="instructions", section_marks=[("Section A", 2)]),
        page(questions=[q("A1", marks=0, is_mcq=True, options=OPTIONS, topic=TOPIC),
                        q("B1", marks=0, topic=TOPIC)]),
        page("answer", answers=[a("A1", mode="mcq", correct="C"), a("B1")]),
    ])
    items = _by_suffix()
    assert items["qa1"][0]["marks"] == 2 and items["qa1"][1] == []
    assert "No marks shown for this question" in items["qb1"][1]  # written questions still need printed marks


def test_prompt_asks_for_section_marks():
    assert "section_marks" in prompts.PAGE_SCHEMA["properties"] and "section_marks:" in prompts.COMMON


# ---- 5. Numbering check per section -----------------------------------------------------------

def test_numbering_is_checked_within_each_section():
    questions = [("a1", ""), ("a2", ""), ("b5", "a"), ("b5", "b"), ("b6", ""), ("c12", "")]
    assert numbering_problems(questions, questions) == []  # A2 -> B5 -> C12 are not gaps
    problems = numbering_problems([("a1", ""), ("b1", ""), ("b3", "")], [])
    assert problems == ["Section B: question numbers jump from 1 to 3 (2 missing)"]


def test_numbering_check_uses_the_matched_key_numbers():
    questions, answers = [("c12", ""), ("c13", "")], [("c1", ""), ("c2", "")]
    assert numbering_problems(questions, answers, {"c12": "c1", "c13": "c2"}) == []
    assert numbering_problems(questions, answers) == [
        "The answer key has C1 but no question C1 was found", "The answer key has C2 but no question C2 was found"]
