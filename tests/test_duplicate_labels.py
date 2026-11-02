"""Duplicate question labels: a chapter's main section and its Extension section both number from 1, and the
answer key lists both in the same order. Matched by order of appearance when the counts agree.
Made-up data only; no API calls."""

import json

from app.db import connect
from app.ingest import staging
from app.ingest.assemble import PAIRED_BY_ORDER_WARNING
from app.ingest.reassemble import main as reassemble
from app.ingest.summary import file_summary, format_summary
from ingest_helpers import PAPERS_YAML, SOURCE_YAML, a, ingest, make_source, page, q

MAIN, EXT = "Main section question", "Extension question"


def _book(env, key_pages, yaml_text=SOURCE_YAML):
    """answers.pdf (read first) and qns_c01.pdf: a cover, then a main-section Q1 and Q2, then an Extension Q1."""
    folder = make_source(env, "test-book", yaml_text,
                         {"answers.pdf": [f"Answers {i}" for i in range(len(key_pages))],
                          "qns_c01.pdf": ["Name: ____", "Main", "Extension"]})
    ingest(folder, [*key_pages,
                    page("cover", type_reason="name box"),
                    page(questions=[q("1", question=MAIN, marks=1), q("2", question="Second question", marks=1)]),
                    page(questions=[q("1", question=EXT, marks=1)])])
    return folder


def _key(*pages_of_answers):
    first, *rest = pages_of_answers
    return [page("answer", chapter="1", answers=first), *[page("answer", answers=r) for r in rest]]


def _pending(text):
    """(mark point texts, warnings) of the pending question whose original text is `text`."""
    for r in connect().execute("SELECT * FROM staged_items WHERE kind = 'question' AND status = 'pending'"):
        if json.loads(r["raw_json"])["question"] == text:
            return [mp["text"] for mp in json.loads(r["record_json"])["mark_points"]], json.loads(r["warnings_json"])
    raise AssertionError(f"no pending question {text!r}")


def _summary():
    conn = connect()
    return "\n".join(line for f in conn.execute("SELECT id FROM source_files WHERE status != 'superseded'").fetchall()
                     for line in format_summary(file_summary(conn, f["id"])))


def _more_than_one(warnings):
    return any("More than one answer" in w for w in warnings)


def test_duplicate_labels_are_paired_by_order(env):
    _book(env, _key([a("1", points=("main answer",)), a("2", points=("two",))], [a("1", points=("ext answer",))]))
    points, warnings = _pending(MAIN)
    assert points == ["main answer"] and PAIRED_BY_ORDER_WARNING in warnings and not _more_than_one(warnings)
    points, warnings = _pending(EXT)
    assert points == ["ext answer"] and PAIRED_BY_ORDER_WARNING in warnings and not _more_than_one(warnings)
    points, warnings = _pending("Second question")
    assert points == ["two"] and PAIRED_BY_ORDER_WARNING not in warnings
    text = _summary()
    assert "  Duplicate labels paired by order: 2" in text and "Two answer sources" not in text
    assert "Answers without a question" not in text


def test_unequal_counts_are_left_as_they_were(env):
    _book(env, _key([a("1", points=("first",)), a("2", points=("two",))],
                    [a("1", points=("second",))], [a("1", points=("third",))]))
    for text in (MAIN, EXT):
        points, warnings = _pending(text)
        assert _more_than_one(warnings) and PAIRED_BY_ORDER_WARNING not in warnings
        assert points == ["first"]  # the first answer found, as before
    assert "Duplicate labels paired by order" not in _summary()


def test_approving_one_never_drops_the_other_and_leaves_it_untouched(env):
    folder = _book(env, _key([a("1", points=("main answer",)), a("2", points=("two",))],
                             [a("1", points=("ext answer",))]))
    conn = connect()
    approved = next(r for r in conn.execute("SELECT * FROM staged_items WHERE status = 'pending'")
                    if json.loads(r["raw_json"])["question"] == MAIN)
    staging.approve(conn, approved["id"])
    before = connect().execute("SELECT item_id, record_json, warnings_json FROM staged_items WHERE id = ?",
                               (approved["id"],)).fetchone()
    assert reassemble([str(folder), "--apply"], out=lambda _line: None) == 0
    conn = connect()
    row = conn.execute("SELECT status, item_id, record_json, warnings_json FROM staged_items WHERE id = ?",
                       (approved["id"],)).fetchone()
    assert row["status"] == "approved" and tuple(row) == (row["status"],) + tuple(before)  # untouched
    pending_questions = [json.loads(r["raw_json"])["question"] for r in conn.execute(
        "SELECT raw_json FROM staged_items WHERE kind = 'question' AND status = 'pending'")]
    assert sorted(pending_questions) == ["Extension question", "Second question"]  # one Q1 each, no extra
    points, warnings = _pending(EXT)
    assert points == ["ext answer"] and PAIRED_BY_ORDER_WARNING in warnings and not _more_than_one(warnings)


def test_numbering_overrides_win_and_pairing_uses_the_overridden_label(env):
    # Both Q1s are filed under Q7 in the key by the override (nothing else uses Q7); the two Q7 answers are
    # then paired by order. Q2 in the book has no answer here.
    yaml_text = SOURCE_YAML + 'numbering_overrides:\n  "qns_c01.pdf": {"1": "7"}\n'
    _book(env, _key([a("7", points=("A",))], [a("7", points=("B",))]), yaml_text)
    assert _pending(MAIN)[0] == ["A"] and _pending(EXT)[0] == ["B"]


def test_an_exam_paper_with_a_repeated_number_pairs_its_own_key_by_order(env):
    folder = make_source(env, "papers", PAPERS_YAML,
                         {"2024_test_eoy.pdf": ["Instructions", "Part 1", "Part 2", "Key 1", "Key 2"]})
    ingest(folder, [page("other", type_reason="instructions"),
                    page(questions=[q("1", question=MAIN, marks=1)]),
                    page(questions=[q("1", question=EXT, marks=1)]),
                    page("answer", answers=[a("1", points=("first",))]),
                    page("answer", answers=[a("1", points=("second",))])])
    assert _pending(MAIN)[0] == ["first"] and _pending(EXT)[0] == ["second"]
    assert "  Duplicate labels paired by order: 2" in _summary()
