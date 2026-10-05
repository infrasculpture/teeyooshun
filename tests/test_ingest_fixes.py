"""Chapter 1 fixes: printed numbers and inheritance, the numbering check, shared stems,
hierarchical parts, worked-solution priority, the model's unsure reasons, and the
offline reassemble command. Made-up data only; no API calls."""

import json

import pytest

from app.db import connect, utc_now
from app.ingest import prompts
from app.ingest.assemble import (numbering_problems, parent_attached, parent_missing, parent_parts,
                                 split_for_sub_part, stitch_answers, stitch_questions)
from app.ingest.reassemble import main as reassemble
from app.ingest.summary import file_summary, format_summary
from ingest_helpers import SOURCE_YAML, a, all_responses, book, ingest, make_source, page, q


def _items():
    return connect().execute("SELECT * FROM staged_items WHERE status = 'pending' ORDER BY id").fetchall()


def _record(item):
    return json.loads(item["record_json"])


# ---- 1. Printed numbers, inheritance and the numbering check ---------------------------------

def test_prompt_asks_for_printed_numbers_never_counting():
    assert "the question number exactly as printed on the page" in prompts.QUESTION_RULES
    assert "Never number questions by counting entries." in prompts.QUESTION_RULES
    assert "Never number answers by counting" in prompts.ANSWER_RULES


def test_part_without_number_inherits_the_last_printed_number():
    items = stitch_questions([
        (1, "5", page(questions=[q("3", "a")])),
        (2, "6", page(questions=[q("", "b"), q("", "c")])),  # page starts with (b): no number printed
    ])
    assert [(i["qnum"], i["part"]) for i in items] == [("3", "a"), ("3", "b"), ("3", "c")]
    assert items[1]["number_inherited"] and not items[0].get("number_inherited")


def test_answer_part_without_number_inherits_too():
    entries = stitch_answers([
        (1, "", page("answer", chapter="1", answers=[a("4", "a")])),
        (2, "", page("answer", answers=[a("", "b")])),
    ])
    assert [(e["qnum"], e["part"]) for e in entries] == [("4", "a"), ("4", "b")]


def test_numbering_check_flags_gaps_duplicates_missing_parts_and_key_extras():
    questions = [("1", "a"), ("1", "b"), ("2", "a"), ("2", "c"), ("2", "c"), ("5", ""), ("", "x")]
    answers = [("1", "a"), ("3", ""), ("7", "")]
    problems = numbering_problems(questions, answers)
    assert "1 item(s) have no question number" in problems
    assert "Q2(c) appears 2 times" in problems
    assert "Question numbers jump from 2 to 5 (3, 4 missing)" in problems
    assert "Q2 has parts a, c but no b" in problems
    assert "The answer key has Q3 but no question 3 was found" in problems
    assert "The answer key has Q7 but no question 7 was found" in problems


def test_clean_numbering_has_no_problems():
    assert numbering_problems([("1", "a"), ("1", "b"), ("2", "")], [("1", "a"), ("1", "b"), ("2", "")]) == []


def test_numbering_check_in_the_summary(env):
    folder = make_source(env, "gap", SOURCE_YAML, {"answers.pdf": ["Key"], "qns_c01.pdf": ["Q1 Q3"]})
    ingest(folder, [page("answer", chapter="1", answers=[a("1"), a("2"), a("3")]),
                    page(questions=[q("1"), q("3")])])
    conn = connect()
    file_id = conn.execute("SELECT id FROM source_files WHERE filename = 'qns_c01.pdf'").fetchone()["id"]
    text = "\n".join(format_summary(file_summary(conn, file_id)))
    assert "Numbering check:" in text
    assert "Question numbers jump from 1 to 3 (2 missing)" in text
    assert "The answer key has Q2 but no question 2 was found" in text


# ---- 2. Shared stems ------------------------------------------------------------------

def test_prompt_says_stems_are_not_entries():
    assert "A stem is not an entry." in prompts.QUESTION_RULES


def test_stem_becomes_context_on_each_part_even_across_pages():
    items = stitch_questions([
        (1, "5", page(questions=[q("2", "", marks=0, question="A beaker of water is heated.",
                                   context="Fig 2.1 shows the set-up.")])),
        (2, "6", page(questions=[q("", "a", question="State the boiling point.", context=""),
                                 q("", "b", question="Explain why bubbles form.", context="")])),
    ])
    assert [(i["qnum"], i["part"]) for i in items] == [("2", "a"), ("2", "b")]
    assert items[0]["context"] == "Fig 2.1 shows the set-up. A beaker of water is heated."
    assert items[1]["pages"] == [1, 2]


def test_stem_text_is_not_repeated_when_the_part_already_has_it():
    stem = q("2", "", marks=0, question="A beaker is heated.")
    part = q("2", "a", context="A beaker is heated.", question="State X.")
    items = stitch_questions([(1, "1", page(questions=[stem, part]))])
    assert len(items) == 1 and items[0]["context"] == "A beaker is heated."


def test_question_with_its_own_marks_is_not_a_stem():
    items = stitch_questions([(1, "1", page(questions=[q("4", "", marks=2), q("4", "a")]))])
    assert len(items) == 2


# ---- 3. Hierarchical parts ------------------------------------------------------------------

@pytest.mark.parametrize("label", ["(c)(i)", "c(i)", "ci", "(c) (i)"])
def test_part_labels_normalise_the_same(label):
    from app.ingest.assemble import normalise_part
    assert normalise_part(label) == "ci"


def test_parent_parts():
    assert parent_parts("cii") == [("c", "ii")]
    assert parent_parts("b2") == [("b", "2")]
    assert parent_parts("c") == []


def test_split_parent_answer_by_labelled_sub_parts():
    entry = a("1", "c", points=["Point for i", "Point for ii", "More for ii"], sub_parts=["i", "(ii)", "ii"])
    scheme, how = split_for_sub_part(entry, "ii")
    assert how == "split" and [mp["text"] for mp in scheme["mark_points"]] == ["Point for ii", "More for ii"]
    assert split_for_sub_part(entry, "iii") == (None, "missing")


def test_unlabelled_parent_answer_is_used_whole():
    entry = a("1", "c")
    assert split_for_sub_part(entry, "i") == (entry, "whole")


def _parent_source(env, answer):
    folder = make_source(env, "parts", SOURCE_YAML, {"answers.pdf": ["Key"], "qns_c01.pdf": ["Q1"]})
    ingest(folder, [page("answer", chapter="1", answers=[answer]),
                    page(questions=[q("1", "(c)(i)", marks=1), q("1", "c(ii)", marks=1), q("1", "ciii", marks=1)])])


def test_labelled_parent_answer_is_split_across_sub_parts(env):
    _parent_source(env, a("1", "c", points=["Answer i", "Answer ii", "Answer iii"], sub_parts=["i", "ii", "iii"]))
    items = _items()
    assert [_record(i)["mark_points"][0]["text"] for i in items] == ["Answer i", "Answer ii", "Answer iii"]
    assert all(json.loads(i["warnings_json"]) == [] for i in items)
    assert connect().execute("SELECT COUNT(*) FROM key_entries WHERE matched_item_id IS NULL").fetchone()[0] == 0


def test_unlabelled_parent_answer_is_attached_with_a_warning(env):
    _parent_source(env, a("1", "c", points=["Whole answer"]))
    for item in _items():
        assert _record(item)["mark_points"][0]["text"] == "Whole answer"
        assert parent_attached("1", "c") in json.loads(item["warnings_json"])


def test_labelled_parent_answer_missing_a_sub_part(env):
    _parent_source(env, a("1", "c", points=["Answer i", "Answer ii"], sub_parts=["i", "ii"]))
    third = _items()[2]
    assert parent_missing("1", "c", "iii") in json.loads(third["warnings_json"])
    assert "No matching mark scheme found in the answer key" in json.loads(third["warnings_json"])


# ---- 4. Worked examples (see also test_ingest_summary.py) -------------------------------------

def test_worked_solution_on_a_later_page_answers_inherited_parts(env):
    folder = make_source(env, "worked2", SOURCE_YAML, {"qns_c01.pdf": ["Worked example 2", "Solution"]})
    ingest(folder, [
        page(questions=[q("2", "a"), q("2", "b")]),
        page("answer", answers=[a("2", "a", points=["Step 1", "Step 2"]), a("", "b", points=["Step 3", "Step 4"])]),
    ])
    items = _items()
    assert [_record(i)["mark_points"][0]["text"] for i in items] == ["Step 1", "Step 3"]
    assert all(_record(i)["source"] == "assessment_book" for i in items)


# ---- 5. The model's unsure reasons ------------------------------------------------------------

def test_prompt_requires_a_reason_when_unsure():
    assert "uncertain_reason must say briefly what is unclear" in prompts.COMMON


def test_unsure_reasons_are_kept_and_grouped_in_the_summary(env):
    folder = make_source(env, "unsure", SOURCE_YAML, {"answers.pdf": ["Key"], "qns_c01.pdf": ["Q1", "Q2"]})
    ingest(folder, [
        page("answer", chapter="1", answers=[a("1"), a("2"), a("3", uncertain=True, reason="faint print")]),
        page(questions=[q("1", uncertain=True, reason="cut off by binding"),
                        q("2", uncertain=True, reason="cut off by binding")]),
        page(questions=[q("3", uncertain=True)], uncertain=True, reason="page is skewed"),
    ])
    conn = connect()
    item3 = conn.execute("SELECT warnings_json FROM staged_items WHERE item_id LIKE '%-q3'").fetchone()
    warnings = json.loads(item3["warnings_json"])
    assert "Model unsure about the question: no reason given" in warnings  # the page's reason is not copied
    assert not any("page is skewed" in w for w in warnings)
    assert "Model unsure about the answer: faint print" in warnings
    file_id = conn.execute("SELECT id FROM source_files WHERE filename = 'qns_c01.pdf'").fetchone()["id"]
    s = file_summary(conn, file_id)
    assert s["unsure"][("question", "cut off by binding")] == [
        "science-sec1-unsure-qns_c01-q1", "science-sec1-unsure-qns_c01-q2"]
    assert s["unsure_pages"] == [(2, "page is skewed")]  # page 2 of qns_c01.pdf
    text = "\n".join(format_summary(s))
    assert "[question] cut off by binding (2): science-sec1-unsure-qns_c01-q1, science-sec1-unsure-qns_c01-q2" in text


# ---- Follow-ups from qns_c01 version 3 ---------------------------------------------------------------

def test_number_doubt_cleared_when_inherited_number_matches_the_key(env):
    folder = make_source(env, "inherit", SOURCE_YAML, {"answers.pdf": ["Key"], "qns_c01.pdf": ["Q3a", "Q3b Q3c"]})
    ingest(folder, [
        page("answer", chapter="1", answers=[a("3", "a"), a("3", "b")]),
        page(questions=[q("3", "a")]),
        page(questions=[q("", "b", uncertain=True, reason="question number not shown on this page"),
                        q("", "c", uncertain=True, reason="question number not shown on this page"),
                        q("", "d", uncertain=True, reason="text is faint")]),
    ])
    warnings = {i["item_id"].rsplit("-", 1)[1]: json.loads(i["warnings_json"]) for i in _items()}
    assert not any(w.startswith("Model unsure") for w in warnings["q3b"])  # inherited, matched: settled
    assert "Model unsure about the question: question number not shown on this page" in warnings["q3c"]  # no answer
    assert "Model unsure about the question: text is faint" in warnings["q3d"]  # a different doubt stays


@pytest.mark.parametrize("raw, expected", [
    ("Worked Example 2", "we2"), ("WE2", "we2"), ("Example 2", "we2"), ("WE", "we"), ("Worked example", "we"),
    ("Q2.", "2"), ("12", "12"),
])
def test_worked_example_labels_are_made_the_same(raw, expected):
    from app.ingest.assemble import normalise_qnum
    assert normalise_qnum(raw) == expected


def test_prompt_labels_worked_examples_the_same_way():
    assert 'qnum "WE"' in prompts.QUESTIONS and "exactly the same qnum and part" in prompts.QUESTIONS


def test_worked_example_question_and_solution_match(env):
    folder = make_source(env, "we", SOURCE_YAML, {"qns_c01.pdf": ["Worked Example", "Solution"]})
    ingest(folder, [
        page(questions=[q("Worked Example", "a"), q("Worked Example", "b")]),
        page("answer", answers=[a("WE", "(a)", points=["Step A", "Step A2"]), a("WE", "(b)", points=["Step B", "B2"])]),
    ])
    items = _items()
    assert [i["item_id"] for i in items] == ["science-sec1-we-qns_c01-qwea", "science-sec1-we-qns_c01-qweb"]
    assert [_record(i)["mark_points"][0]["text"] for i in items] == ["Step A", "Step B"]


def test_part_stem_becomes_context_on_sub_parts_on_the_next_page():
    items = stitch_questions([
        (1, "7", page(questions=[q("5", "a"), q("5", "b", marks=0, question="The table shows five metals.")])),
        (2, "8", page(questions=[q("", "(i)", marks=1, question="Which is densest?"),
                                 q("", "(ii)", marks=1, question="Which melts first?")])),
    ])
    assert [(i["qnum"], i["part"]) for i in items] == [("5", "a"), ("5", "bi"), ("5", "bii")]
    assert items[1]["context"].startswith("The table shows five metals.")
    assert items[1]["pages"] == [1, 2]


def test_outer_and_part_stems_both_reach_the_sub_parts_in_order():
    items = stitch_questions([(1, "1", page(questions=[
        q("7", "", marks=0, question="An experiment is set up."),
        q("7", "b", marks=0, question="The results are in Table 7.1."),
        q("7", "bi", marks=1, question="State the trend.")]))])
    assert len(items) == 1
    assert items[0]["context"] == "An experiment is set up. The results are in Table 7.1."


@pytest.mark.parametrize("parts, expected", [
    (["i", "ii", "iii"], ["i", "ii", "iii"]),  # top-level roman parts stay as they are
    (["g", "h", "i"], ["g", "h", "i"]),  # (i) after (h) is the next letter
])
def test_roman_labels_that_are_not_sub_parts(parts, expected):
    items = stitch_questions([(1, "1", page(questions=[q("6", p) for p in parts]))])
    assert [i["part"] for i in items] == expected


def test_stem_and_page_doubts_are_not_copied_onto_items():
    items = stitch_questions([(1, "1", page(
        questions=[q("3", "", marks=0, question="Table 3.1 shows data.", uncertain=True, reason="stem and data only"),
                   q("3", "b", question="Describe the trend."), q("3", "c", question="Explain it.")],
        uncertain=True, reason="stem and data only"))])
    assert [i["part"] for i in items] == ["b", "c"]
    assert not any(i.get("uncertain") for i in items)
    from types import SimpleNamespace
    from app.ingest.assemble import build_question
    cfg = SimpleNamespace(subject="science", level="sec1", slug="s", title="T", key_reliability="assessment_book")
    _record, warnings = build_question(items[0], cfg=cfg, file_stem="qns_c01", file_topic="sci-01-scientific-endeavour",
                                       scheme=a("3", "b"), known_topics={"sci-01-scientific-endeavour"})
    assert not any("Model unsure" in w for w in warnings)


# ---- Second follow-up: structured flag, unnumbered worked-example answers ------------------------

def _warnings_by_suffix():
    return {i["item_id"].rsplit("-", 1)[1]: json.loads(i["warnings_json"]) for i in _items()}


def test_number_inherited_is_set_when_the_model_fills_in_a_carried_number():
    items = stitch_questions([
        (1, "1", page(questions=[q("2", "a"), q("2", "b")])),
        (2, "2", page(questions=[q("2", "c"), q("2", "d"), q("3", "a"), q("2", "e")])),
    ])
    flags = {(i["qnum"], i["part"]): bool(i.get("number_inherited")) for i in items}
    assert flags == {("2", "a"): False, ("2", "b"): False, ("2", "c"): True, ("2", "d"): True,
                     ("3", "a"): False, ("2", "e"): False}


def test_doubt_cleared_whatever_the_reason_when_carried_item_matches(env):
    folder = make_source(env, "carry", SOURCE_YAML, {"answers.pdf": ["Key"], "qns_c01.pdf": ["Q2ab", "Q2cd Q3"]})
    ingest(folder, [
        page("answer", chapter="1", answers=[a("2", p) for p in "abcd"] + [a("3", "a")]),
        page(questions=[q("2", "a"), q("2", "b")]),
        page(questions=[q("2", "c", uncertain=True, reason="continued from previous page"),
                        q("2", "d", uncertain=True, reason="part of Q2"),
                        q("3", "a", uncertain=True, reason="smudge on the page")]),
    ])
    w = _warnings_by_suffix()
    assert not any(x.startswith("Model unsure") for x in w["q2c"] + w["q2d"])
    assert "Model unsure about the question: smudge on the page" in w["q3a"]  # its own number is printed here


def test_doubt_cleared_on_sub_parts_matched_through_their_parent(env):
    folder = make_source(env, "subs", SOURCE_YAML, {"answers.pdf": ["Key"], "qns_c01.pdf": ["Q4c", "(i) (ii)"]})
    ingest(folder, [
        page("answer", chapter="1", answers=[a("4", "c", points=["For i", "For ii"], sub_parts=["i", "ii"])]),
        page(questions=[q("4", "c", marks=0, question="Fig 4.1 shows a circuit.")]),
        page(questions=[q("", "(i)", marks=1, uncertain=True, reason="continues Q4(c)"),
                        q("", "(ii)", marks=1, uncertain=True, reason="continues Q4(c)")]),
    ])
    w = _warnings_by_suffix()
    assert set(w) == {"q4ci", "q4cii"}
    assert w["q4ci"] == [] and w["q4cii"] == []


def test_unnumbered_answers_in_a_questions_file_belong_to_the_worked_example():
    entries = stitch_answers([(2, "", page("answer", answers=[a("", "a"), a("", "(b)")]))], "01", "we")
    assert [(e["qnum"], e["part"]) for e in entries] == [("we", "a"), ("we", "b")]


def test_unnumbered_worked_solution_matches_the_worked_example(env):
    folder = make_source(env, "we3", SOURCE_YAML, {"qns_c01.pdf": ["Worked Example", "Solution", "Q1"]})
    ingest(folder, [
        page(questions=[q("WE", "a"), q("WE", "b")]),
        page("answer", answers=[a("", "a", points=["Sol a", "a2"]), a("", "b", points=["Sol b", "b2"])]),
        page(questions=[q("1")]),
    ])
    records = {i["item_id"].rsplit("-", 1)[1]: _record(i) for i in _items()}
    assert records["qwea"]["mark_points"][0]["text"] == "Sol a"
    assert records["qweb"]["mark_points"][0]["text"] == "Sol b"
    assert records["q1"]["mark_points"] == []  # an ordinary question gets nothing from the worked solution


def test_unnumbered_solution_takes_a_numbered_worked_example_label(env):
    folder = make_source(env, "we4", SOURCE_YAML, {"qns_c01.pdf": ["Worked Example 2", "Solution"]})
    ingest(folder, [
        page(questions=[q("Worked Example 2", "a")]),
        page("answer", answers=[a("", "a", points=["Sol", "Sol2"])]),
    ])
    item = _items()[0]
    assert item["item_id"].endswith("-qwe2a") and _record(item)["mark_points"][0]["text"] == "Sol"


# ---- Rebuilding an existing run without the API ---------------------------------------------------

def _add_stale_item(conn):
    """A pending item as an older version of the code might have left it."""
    f = conn.execute("SELECT * FROM source_files WHERE filename = 'qns_c01.pdf'").fetchone()
    now = utc_now()
    conn.execute("""INSERT INTO staged_items (file_id, kind, item_id, unit, qnum, part, status, record_json,
                                              warnings_json, page_refs_json, raw_json, created_at, updated_at)
                    VALUES (?, 'question', 'stale-stem-item', '01', '', '', 'pending', '{}', '[]', '[]', '{}', ?, ?)""",
                 (f["id"], now, now))
    conn.commit()


def test_reassemble_preview_changes_nothing(env):
    folder = book(env)
    ingest(folder, all_responses())
    conn = connect()
    _add_stale_item(conn)
    before = conn.execute("SELECT id, status, record_json FROM staged_items ORDER BY id").fetchall()
    lines = []
    assert reassemble([str(folder)], out=lines.append) == 0
    text = "\n".join(lines)
    assert "Removed (1): stale-stem-item" in text
    assert "Preview only: nothing was changed." in text
    after = connect().execute("SELECT id, status, record_json FROM staged_items ORDER BY id").fetchall()
    assert [tuple(r) for r in after] == [tuple(r) for r in before]


def test_reassemble_apply_rebuilds_from_stored_pages(env):
    folder = book(env)
    _, client, _ = ingest(folder, all_responses())
    conn = connect()
    _add_stale_item(conn)
    lines = []
    assert reassemble([str(folder), "--file", "qns_c01.pdf", "--apply"], out=lines.append) == 0
    assert "Changes saved." in lines[-1]
    items = _items()
    assert [i["item_id"] for i in items] == [
        "science-sec1-test-book-qns_c01-q1a", "science-sec1-test-book-qns_c01-q1b", "science-sec1-test-book-qns_c01-q2"]
    stale = connect().execute("SELECT status FROM staged_items WHERE item_id = 'stale-stem-item'").fetchone()
    assert stale["status"] == "superseded"
