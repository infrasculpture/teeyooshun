"""Source-based questions (History/Geography): sources pages, question context carried across pages,
level-of-response (LORMS) answers, and rebuilding without API calls. Made-up data only; no API calls."""

import json
from types import SimpleNamespace

import pytest

from app.db import connect
from app.ingest import prompts, staging
from app.ingest.assemble import (build_question, scheme_fields, source_labels_in, stitch_answers,
                                 stitch_questions)
from app.ingest.reassemble import main as reassemble
from ingest_helpers import PAPERS_YAML, a, ingest, level, make_source, page, q, src

TOPIC = "sci-05-ray-model-of-light"
CFG = SimpleNamespace(subject="history", level="sec1", slug="s", title="T", key_reliability="publisher_paper")


def _rows(status="pending"):
    rows = connect().execute("SELECT * FROM staged_items WHERE status = ? ORDER BY id", (status,)).fetchall()
    return {r["item_id"].rsplit("-", 1)[1]: (json.loads(r["record_json"]), json.loads(r["warnings_json"])) for r in rows}


# A made-up paper: a sources page that also holds the question number and stem, the parts on the next page,
# and a level table that carries on to the next page with no question number at the top.
def _paper(env, exemplar_field=True):
    folder = make_source(env, "papers", PAPERS_YAML, {"2024_test_eoy.pdf": ["Sources", "Parts", "Key 1", "Key 2"]})
    sources_page = page("sources", sources=[
        src("Background", "The harbour was busy in 1942."),
        src("A", "We left at dawn.", provenance="A diary, 1942"),
        src("B", picture="A cartoon of a crowded dock.", has_picture=True, provenance="A cartoon, 1950")],
        questions=[q("B1", "", marks=0, question="Study the sources below.", topic=TOPIC)])
    parts = page(questions=[
        q("", "a", question="What does Source A say about the journey?", marks=2, topic=TOPIC),
        q("", "b", question="Do the sources agree about the harbour? Use all the sources.", marks=0, topic=TOPIC)])
    key_1 = page("answer", answers=[
        a("B1", "a", points=["Left early", "Left at dawn"]),
        a("B1", "b", mode="lorms", lorms=[level(1, 1, 1), level(2, 2, 2)], exemplars=[] if exemplar_field else None)])
    key_2 = page("answer", answers=[
        a("", "", mode="lorms", lorms=[level(3, 3, 3)], exemplars=[(3, "A model answer using Sources A and B.")])])
    ingest(folder, [sources_page, parts, key_1, key_2])
    return folder


def test_sources_are_added_to_the_context_of_the_questions_that_refer_to_them(env):
    _paper(env)
    items = _rows()
    assert set(items) == {"qb1a", "qb1b"}  # the stem is folded; nothing without a number
    record_a, warnings_a = items["qb1a"]
    assert record_a["context"] == ("Study the sources below.\n\nBackground: The harbour was busy in 1942.\n"
                                   "Source A: We left at dawn. (A diary, 1942)")
    assert warnings_a == []
    record_b, warnings_b = items["qb1b"]
    assert "Source A: We left at dawn." in record_b["context"]
    assert "Source B: [Picture: A cartoon of a crowded dock.] (A cartoon, 1950)" in record_b["context"]
    assert record_b["figure"] == "needed_to_answer"  # the picture source: the marker is sent the page image
    assert 1 in [i["page"] for i in record_b["page_images"]] and warnings_b == []


def test_level_table_split_across_pages_becomes_one_lorms_answer(env):
    _paper(env)
    record, warnings = _rows()["qb1b"]
    assert record["marking_mode"] == "lorms" and "mark_points" not in record
    assert [(lv["level"], lv["marks_min"], lv["marks_max"]) for lv in record["lorms"]] == [(1, 1, 1), (2, 2, 2), (3, 3, 3)]
    assert record["lorms"][2]["exemplars"] == ["A model answer using Sources A and B."]
    assert record["marks"] == 3 and warnings == []  # no marks printed: the top of the highest level
    conn = connect()
    assert conn.execute("SELECT COUNT(*) FROM key_entries WHERE superseded = 0").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM key_entries WHERE matched_item_id IS NULL").fetchone()[0] == 0


# ---- 1. Sources ---------------------------------------------------------------------------------

@pytest.mark.parametrize("text, labels, every", [
    ("Study Source A.", {"A"}, False),
    ("How far do Sources C and D agree?", {"C", "D"}, False),
    ("Sources A to C show...", {"A", "B", "C"}, False),
    ("Using Source A, B and C, explain", {"A", "B", "C"}, False),
    ("Compare Source A and Source B.", {"A", "B"}, False),
    ("Use all the sources and your knowledge.", set(), True),
    ("Read Source Alpha. Sources were few.", set(), False),
])
def test_source_references_in_question_text(text, labels, every):
    assert source_labels_in(text) == (labels, every)


def test_sources_across_consecutive_pages_form_one_set_and_picture_pages_are_kept():
    items = stitch_questions([
        (1, "", page("sources", sources=[src("A", "Text A")])),
        (2, "", page("sources", sources=[src("B", picture="A map of the island.", has_picture=True)])),
        (3, "", page(questions=[q("1", "a", question="What does Source B show?", marks=2),
                                q("1", "b", question="What does Source A say?", marks=2)])),
    ])
    first, second = items
    assert "Source B: [Picture: A map of the island.]" in first["context"] and "Source A" not in first["context"]
    assert first["figure"] == "needed_to_answer" and first["pages"] == [2, 3]  # the picture page travels with it
    assert second["context"] == "Source A: Text A" and second["figure"] == "none" and second["pages"] == [3]


def test_a_new_sources_page_after_a_question_starts_a_new_set():
    items = stitch_questions([
        (1, "", page("sources", sources=[src("A", "First A")], questions=[q("1", "", question="Study Source A.")])),
        (2, "", page("sources", sources=[src("A", "Second A")], questions=[q("2", "", question="Study Source A.")])),
    ])
    assert [i["context"] for i in items] == ["Source A: First A", "Source A: Second A"]


def test_stem_before_its_sources_finds_them_on_the_next_page():
    items = stitch_questions([
        (1, "", page(questions=[q("3", "", question="Study Source A and answer.", marks=2)])),
        (2, "", page("sources", sources=[src("A", "Late source")])),
    ])
    assert items[0]["context"] == "Source A: Late source"


def test_model_reported_refs_are_used_and_a_missing_source_is_flagged():
    items = stitch_questions([
        (1, "", page("sources", sources=[src("A", "Text A")])),
        (2, "", page(questions=[q("1", "", question="Explain.", refs=["Source A", "C"])])),
    ])
    item = items[0]
    assert item["context"] == "Source A: Text A" and item["missing_sources"] == ["C"]
    _record, warnings = build_question(item, cfg=CFG, file_stem="p", file_topic="x", scheme=a("1"), known_topics={"x"})
    assert "Refers to Source C but no text for it was found: its sources page may need to be read again" in warnings


def test_questions_without_references_are_left_alone():
    items = stitch_questions([
        (1, "", page("sources", sources=[src("A", "Text A")])),
        (2, "", page(questions=[q("1", "", question="Define the term.")])),
    ])
    assert items[0]["context"] == "" and "sources_used" not in items[0]


def test_prompt_asks_for_sources():
    assert '"sources" for a page of background text and sources' in prompts.COMMON
    assert "sources" in prompts.PAGE_SCHEMA["properties"] and "source_refs" in prompts.QUESTION_ITEM["properties"]
    assert "sources" in prompts.PAGE_SCHEMA["properties"]["page_type"]["enum"]


# ---- 2. Question context carries across pages --------------------------------------------------------

def test_unnumbered_parts_inherit_the_number_and_stem_from_the_previous_page():
    items = stitch_questions([
        (1, "", page(questions=[q("4", "", marks=0, question="Study the cartoon.", context="A cartoon of a king.")])),
        (2, "", page(questions=[q("", "a", question="Who is shown?"), q("", "b", question="Why?"),
                                q("", "c", question="So what?")])),
    ])
    assert [(i["qnum"], i["part"]) for i in items] == [("4", "a"), ("4", "b"), ("4", "c")]
    assert all(i["number_inherited"] and i["context"] == "A cartoon of a king. Study the cartoon." for i in items)
    assert not any(i.get("no_number") for i in items)


def test_parts_inherit_through_a_sources_page_between_stem_and_parts():
    items = stitch_questions([
        (1, "", page(questions=[q("4", "", marks=0, question="Study Source A.")])),
        (2, "", page("sources", sources=[src("A", "The text")])),
        (3, "", page(questions=[q("", "a"), q("", "b")])),
    ])
    assert [(i["qnum"], i["part"]) for i in items] == [("4", "a"), ("4", "b")]
    assert all("Source A: The text" in i["context"] for i in items)


def test_an_item_with_no_number_is_kept_and_flagged_never_silent():
    items = stitch_questions([(1, "", page(questions=[q("", "a", question="Orphan part.")]))])
    assert items[0]["no_number"]
    _record, warnings = build_question(items[0], cfg=CFG, file_stem="p", file_topic="x", scheme=a("1"),
                                       known_topics={"x"})
    assert "No question number could be found for this item: check which question it belongs to" in warnings


def test_ids_stay_unique_when_two_items_share_a_number(env):
    folder = make_source(env, "dup", PAPERS_YAML, {"2024_test_eoy.pdf": ["Q", "Key"]})
    ingest(folder, [page(questions=[q("1", "a", question="First?", topic=TOPIC), q("1", "a", question="Second?", topic=TOPIC)]),
                    page("answer", answers=[a("1", "a")])])
    rows = connect().execute("SELECT item_id, warnings_json FROM staged_items WHERE status = 'pending' ORDER BY id").fetchall()
    ids = [r["item_id"] for r in rows]
    assert len(set(ids)) == 2 and ids[1] == ids[0] + "-2"
    assert any(w.startswith("Duplicate ID") for w in json.loads(rows[1]["warnings_json"]))


# ---- 3. LORMS answers ---------------------------------------------------------------------------------

def test_level_table_split_across_two_pages_is_joined_without_a_number():
    entries = stitch_answers([
        (3, "", page("answer", answers=[a("5", "", mode="lorms", lorms=[level(1, 1, 2), level(2, 3, 4)])])),
        (4, "", page("answer", answers=[a("", "", mode="lorms", lorms=[level(2, 3, 4, "and more words"), level(3, 5, 6)]),
                                        a("6", "")])),
    ])
    assert [(e["qnum"], e["part"]) for e in entries] == [("5", ""), ("6", "")]
    assert [lv["level"] for lv in entries[0]["lorms"]] == [1, 2, 3]
    assert entries[0]["lorms"][1]["descriptor"] == "Level 2 descriptor and more words"
    assert entries[0]["pages"] == [3, 4]


def test_model_answer_is_an_exemplar_never_mark_points_and_joins_across_pages():
    entries = stitch_answers([
        (1, "", page("answer", answers=[a("2", "", mode="lorms", exemplars=[(0, "First half of the answer.")])])),
        (2, "", page("answer", answers=[a("", "", mode="lorms", exemplars=[(0, "Second half.")])])),
    ])
    assert len(entries) == 1
    assert entries[0]["exemplars"] == [{"level": 0, "text": "First half of the answer. Second half."}]
    fields = scheme_fields(entries[0])
    assert fields["marking_mode"] == "lorms" and "mark_points" not in fields
    assert fields["exemplars"] == ["First half of the answer. Second half."]  # no levels yet: kept on the record


def test_exemplar_goes_to_its_level_and_defaults_to_the_top_level():
    entry = a("1", "", mode="lorms", lorms=[level(1, 1, 2), level(2, 3, 4)], exemplars=[(1, "Weak one"), (0, "Best one")])
    levels = scheme_fields(entry)["lorms"]
    assert levels[0]["exemplars"] == ["Weak one"] and levels[1]["exemplars"] == ["Best one"]


def test_model_answer_alone_is_flagged_for_hand_entered_levels():
    items = stitch_questions([(1, "", page(questions=[q("2", "", marks=4)]))])
    _record, warnings = build_question(items[0], cfg=CFG, file_stem="p", file_topic="x", known_topics={"x"},
                                       scheme=a("2", "", mode="lorms", exemplars=[(0, "A model answer.")]))
    assert "Only a model answer was found, with no level table: add the levels by hand" in warnings


def test_marks_are_the_top_of_the_highest_level_when_none_are_printed():
    items = stitch_questions([(1, "", page(questions=[q("2", "", marks=0)]))])
    record, warnings = build_question(items[0], cfg=CFG, file_stem="p", file_topic="x", known_topics={"x"},
                                      scheme=a("2", "", mode="lorms", lorms=[level(1, 1, 3), level(2, 4, 6)]))
    assert record["marks"] == 6 and not any("No marks" in w or w.startswith("Check:") for w in warnings)


def test_level_range_check_runs_on_the_joined_table():
    entries = stitch_answers([
        (1, "", page("answer", answers=[a("2", "", mode="lorms", lorms=[level(1, 1, 3), level(2, 4, 6)])])),
        (2, "", page("answer", answers=[a("", "", mode="lorms", lorms=[level(3, 8, 9)])])),  # 7 is missing
    ])
    items = stitch_questions([(1, "", page(questions=[q("2", "", marks=0)]))])
    _record, warnings = build_question(items[0], cfg=CFG, file_stem="p", file_topic="x", scheme=entries[0],
                                       known_topics={"x"})
    assert any(w.startswith("Check:") and "gap between level 2 (max 6) and level 3 (min 8)" in w for w in warnings)


def test_prompt_asks_for_exemplars_and_unnumbered_continuations():
    assert "put the answer in exemplars" in prompts.ANSWER_RULES and "Never split a model answer" in prompts.ANSWER_RULES
    assert "exemplars" in prompts.ANSWER_ITEM["properties"]


# ---- 4. Rebuilding without API calls --------------------------------------------------------------------

def test_rebuild_leaves_approved_items_untouched(env):
    folder = _paper(env)
    conn = connect()
    approved = conn.execute("SELECT id, item_id FROM staged_items WHERE item_id LIKE '%qb1a'").fetchone()
    staging.approve(conn, approved["id"])
    before = conn.execute("SELECT record_json FROM questions WHERE id = ?", (approved["item_id"],)).fetchone()[0]
    lines = []
    assert reassemble([str(folder), "--apply"], out=lines.append) == 0
    rows = connect().execute(
        "SELECT item_id, status FROM staged_items WHERE status IN ('pending', 'approved') ORDER BY id").fetchall()
    assert sorted((r["item_id"].rsplit("-", 1)[1], r["status"]) for r in rows) == [("qb1a", "approved"), ("qb1b", "pending")]
    assert connect().execute("SELECT record_json FROM questions WHERE id = ?", (approved["item_id"],)).fetchone()[0] == before


def test_report_lists_the_pages_to_read_again_with_the_command_and_cost(env):
    folder = make_source(env, "old", PAPERS_YAML, {"2024_test_eoy.pdf": ["Sources", "Q", "Key", "Key2"]})
    ingest(folder, [
        page("other", type_reason="background text and sources A to C"),  # read before there was a sources class
        page(questions=[q("1", "", marks=4, topic=TOPIC)]),
        page("answer", answers=[a("1", "", mode="lorms", lorms=[level(1, 1, 2), level(2, 3, 4)])]),  # no exemplars field
        page("answer", answers=[a("", "", mode="lorms", lorms=[], exemplars=[])]),
    ])
    lines = []
    assert reassemble([str(folder), "--report"], out=lines.append) == 0
    text = "\n".join(lines)
    assert "2024_test_eoy.pdf: read again 2 page(s), about US$" in text
    assert "pages 1: sources or background text" in text and "pages 3: level tables" in text
    assert f"python -m app.ingest {folder} --file 2024_test_eoy.pdf --pages 1,3" in text
    assert connect().execute("SELECT COUNT(*) FROM staged_items WHERE status = 'pending'").fetchone()[0] == 1  # nothing rebuilt


def test_report_says_nothing_to_read_when_the_readings_are_current(env):
    folder = _paper(env, exemplar_field=True)
    lines = []
    assert reassemble([str(folder), "--report"], out=lines.append) == 0
    assert "Nothing needs reading again" in "\n".join(lines)
