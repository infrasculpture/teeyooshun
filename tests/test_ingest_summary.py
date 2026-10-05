"""Run summary, the summary command, and worked-solution pages (made-up PDFs, fake client)."""

import json

import pytest

from app.db import connect
from app.ingest import prompts
from app.ingest.assemble import TWO_ANSWERS, TWO_ANSWERS_WORKED, warning_type
from app.ingest.summary import file_summary, main as summary_main
from ingest_helpers import PAPERS_YAML, SOURCE_YAML, a, all_responses, book, ingest, make_source, page, q


def _file_id(name):
    return connect().execute("SELECT id FROM source_files WHERE filename = ? AND status != 'superseded'",
                             (name,)).fetchone()["id"]


@pytest.mark.parametrize("warning, label", [
    ("No matching mark scheme found in the answer key", "No mark scheme"),
    ("Fewer mark points (1) than marks (2) and no 'any N'", "Fewer mark points than marks"),
    ("Topic 'sci-x' is not in topics.yaml", "Topic not in topics.yaml"),
    ("The model suggested topic 'sci-x', which is not in topics.yaml", "Topic not in topics.yaml"),
    ("Model unsure about the question: smudged", "Model unsure (question)"),
    ("Check: 'choose' must be a whole number", "Record check failed"),
    (TWO_ANSWERS, "Two answer sources"),
    ("Something new", "Other"),
])
def test_warning_types(warning, label):
    assert warning_type(warning) == label


def test_run_ends_with_a_summary(env):
    code, _, lines = ingest(book(env), all_responses())
    text = "\n".join(lines)
    assert code == 0 and "=== Summary ===" in text
    assert "qns_c01.pdf (questions, version 1, done)" in text
    assert "    p1 cover: name box (nothing kept)" in text
    assert "Answers without a question (1): c01 Q9" in text
    assert "Show this again: python -m app.ingest.summary" in text


def test_summary_lists_unmatched_questions_warning_types_and_unread_pages(env):
    folder = make_source(env, "papers", PAPERS_YAML, {"2024_test_eoy.pdf": ["Contents", "Q1 Q2", "Blank", "Q3"]})
    ingest(folder, [
        page("other", type_reason="contents page"),
        page(questions=[q("1", topic="sci-05-ray-model-of-light"),
                        q("2", topic="sci-05-ray-model-of-light", figure="needed_to_answer")]),
        page("blank"),
        "TRUNCATED", "TRUNCATED",  # page 4 fails twice
    ])
    s = file_summary(connect(), _file_id("2024_test_eoy.pdf"))
    assert s["unmatched"] == ["science-sec1-papers-2024_test_eoy-q1", "science-sec1-papers-2024_test_eoy-q2"]
    assert s["warning_counts"] == {"No mark scheme": 2}  # the figure question has its page image, so no warning
    assert s["not_read"] == [(1, "other", "contents page"), (3, "blank", "no reason given"),
                             (4, "failed", "the reply was cut off at max_tokens")]


def test_summary_command_shows_one_file_again(env):
    folder = book(env)
    ingest(folder, all_responses())
    lines = []
    assert summary_main([str(folder), "--file", "answers.pdf"], out=lines.append) == 0
    text = "\n".join(lines)
    assert "answers.pdf (answer_key" in text and "qns_c01.pdf" not in text
    assert "Answers without a question (1): c01 Q9" in text


def test_summary_command_for_a_folder_not_ingested(env):
    lines = []
    assert summary_main([str(env / "sources" / "nothing")], out=lines.append) == 1
    assert "has not been ingested yet" in lines[0]


# ---- Worked solutions ------------------------------------------------------------

def test_prompt_classes_worked_solutions_as_answer():
    assert 'A worked example whose solution answers questions on this page or earlier pages is "answer", never "other".' \
        in prompts.COMMON
    assert "Worked examples:" in prompts.QUESTIONS
    assert "page_type_reason" in prompts.PAGE_SCHEMA["properties"]


def test_worked_solution_page_answers_the_questions_before_it(env):
    folder = make_source(env, "worked", SOURCE_YAML, {"qns_c01.pdf": ["Worked example 1", "Solution"]})
    code, _, _ = ingest(folder, [
        page(questions=[q("1", question="Calculate the speed.")]),
        page("answer", answers=[a("1", points=["Speed = distance / time", "= 5 m/s"])]),
    ])
    assert code == 0
    item = connect().execute("SELECT * FROM staged_items").fetchone()
    record = json.loads(item["record_json"])
    assert [mp["text"] for mp in record["mark_points"]] == ["Speed = distance / time", "= 5 m/s"]
    assert json.loads(item["warnings_json"]) == []
    s = file_summary(connect(), _file_id("qns_c01.pdf"))
    assert s["unmatched"] == [] and s["orphans"] == [] and s["not_read"] == []


def test_worked_solution_wins_over_the_answer_key(env):
    folder = make_source(env, "both", SOURCE_YAML, {"answers.pdf": ["Answers"], "qns_c01.pdf": ["Q1", "Solution"]})
    ingest(folder, [
        page("answer", chapter="1", answers=[a("1", points=["From the key", "Second"])]),  # answers.pdf first
        page(questions=[q("1")]),
        page("answer", answers=[a("1", points=["From the worked solution", "Second"])]),
    ])
    conn = connect()
    item = conn.execute("SELECT * FROM staged_items").fetchone()
    record = json.loads(item["record_json"])
    assert record["mark_points"][0]["text"] == "From the worked solution"
    assert record["source"] == "assessment_book"  # same reliability as the book's key
    assert TWO_ANSWERS_WORKED in json.loads(item["warnings_json"])
    assert conn.execute("SELECT COUNT(*) FROM key_entries WHERE matched_item_id IS NULL").fetchone()[0] == 0
