"""python -m app.ingest.unapprove: one file's approved questions back to pending, without API calls.
Made-up data only."""

import json

from app.db import connect, utc_now
from app.importer import import_questions
from app.ingest import staging
from app.ingest.reassemble import main as reassemble
from app.ingest.unapprove import main as unapprove
from app.practice import practice_filter
from ingest_helpers import all_responses, book, ingest

Q1A = "science-sec1-test-book-qns_c01-q1a"
OTHER_QUESTION = {"id": "other-file-q1", "subject": "science", "level": "sec1", "topic": "sci-01-scientific-endeavour",
                  "source": "assessment_book", "question": "Name it.", "marks": 1, "marking_mode": "points",
                  "mark_points": [{"id": "MP1", "text": "Diffusion", "key_terms": ["diffusion"]}]}


def _approved_book(env):
    """The made-up book ingested, with all three of qns_c01.pdf's questions approved."""
    folder = book(env)
    ingest(folder, all_responses())
    conn = connect()
    for row in conn.execute("SELECT id FROM staged_items WHERE status = 'pending'").fetchall():
        staging.approve(conn, row["id"])
    assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 3
    return folder, conn


def _attempt(conn, question_id):
    conn.execute("INSERT INTO attempts (question_id, answer_text, marks_awarded, max_marks, result_json, created_at) "
                 "VALUES (?, 'her answer', 1, 2, '{}', ?)", (question_id, utc_now()))
    conn.commit()


def _run(folder, *args):
    lines = []
    code = unapprove([str(folder), "--file", "qns_c01.pdf", *args], out=lines.append)
    return code, "\n".join(lines)


def _statuses(conn, filename="qns_c01.pdf"):
    return [r["status"] for r in conn.execute(
        "SELECT i.status FROM staged_items i JOIN source_files f ON f.id = i.file_id WHERE f.filename = ? "
        "AND f.status != 'superseded' ORDER BY i.id", (filename,))]


def _add_other_file(conn):
    """A second questions file in the same source with one approved item that is also in the bank."""
    source = conn.execute("SELECT id FROM sources").fetchone()["id"]
    now = utc_now()
    file_id = conn.execute(
        "INSERT INTO source_files (source_id, filename, sha256, version, material_type, unit, status, created_at, "
        "layout, slide_order) VALUES (?, 'qns_c02.pdf', 'x', 1, 'questions', '02', 'done', ?, 'a4', '')",
        (source, now)).lastrowid
    conn.execute("""INSERT INTO staged_items (file_id, kind, item_id, unit, qnum, part, status, record_json,
                                              warnings_json, page_refs_json, raw_json, created_at, updated_at)
                    VALUES (?, 'question', 'other-file-q1', '02', '1', '', 'approved', ?, '[]', '[]', '{}', ?, ?)""",
                 (file_id, json.dumps(OTHER_QUESTION), now, now))
    import_questions(conn, [OTHER_QUESTION])
    conn.commit()


def test_only_the_named_files_approved_items_change(env):
    folder, conn = _approved_book(env)
    _add_other_file(conn)
    code, text = _run(folder)
    assert code == 0 and "Done: 3 item(s) are pending again" in text
    conn = connect()
    assert _statuses(conn) == ["pending"] * 3
    assert _statuses(conn, "qns_c02.pdf") == ["approved"]
    assert [r["id"] for r in conn.execute("SELECT id FROM questions")] == ["other-file-q1"]  # the other file's stays
    assert conn.execute("SELECT COUNT(*) FROM question_versions").fetchone()[0] == 1
    # A pending item is reviewable again: its approval record is cleared.
    row = conn.execute("SELECT reviewed_by, reviewed_at FROM staged_items WHERE item_id = ?", (Q1A,)).fetchone()
    assert row["reviewed_by"] is None and row["reviewed_at"] is None


def test_attempted_items_are_skipped_by_default(env):
    folder, conn = _approved_book(env)
    _attempt(conn, Q1A)
    code, text = _run(folder)
    assert code == 0
    assert "Skipped: she has attempted these" in text and Q1A in text and "1 attempt(s)" in text
    conn = connect()
    q1a = conn.execute("SELECT status FROM staged_items WHERE item_id = ?", (Q1A,)).fetchone()
    assert q1a["status"] == "approved"
    bank = conn.execute("SELECT retired FROM questions WHERE id = ?", (Q1A,)).fetchone()
    assert bank["retired"] == 0
    assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 1  # the other two were removed
    assert sorted(_statuses(conn)) == ["approved", "pending", "pending"]
    assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1


def test_include_attempted_retires_the_question_and_keeps_her_history(env):
    folder, conn = _approved_book(env)
    _attempt(conn, Q1A)
    live = f"SELECT COUNT(*) FROM questions q WHERE {practice_filter(conn)} AND q.id = ?"
    assert conn.execute(live, (Q1A,)).fetchone()[0] == 1
    code, text = _run(folder, "--include-attempted")
    assert code == 0 and "Retire (hidden from practice, history kept)" in text
    conn = connect()
    assert _statuses(conn) == ["pending"] * 3
    bank = conn.execute("SELECT retired, version FROM questions WHERE id = ?", (Q1A,)).fetchone()
    assert (bank["retired"], bank["version"]) == (1, 1)  # still there, with its versions
    assert conn.execute("SELECT COUNT(*) FROM question_versions WHERE question_id = ?", (Q1A,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM attempts WHERE question_id = ?", (Q1A,)).fetchone()[0] == 1
    assert conn.execute(live, (Q1A,)).fetchone()[0] == 0  # never served in practice
    assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 1  # the other two were deleted


def test_approving_the_rebuilt_item_brings_a_retired_question_back(env):
    folder, conn = _approved_book(env)
    _attempt(conn, Q1A)
    assert _run(folder, "--include-attempted")[0] == 0
    assert reassemble([str(folder), "--file", "qns_c01.pdf", "--apply"], out=lambda line: None) == 0
    conn = connect()
    new = conn.execute("SELECT id FROM staged_items WHERE item_id = ? AND status = 'pending'", (Q1A,)).fetchone()
    staging.approve(conn, new["id"])
    assert conn.execute("SELECT retired FROM questions WHERE id = ?", (Q1A,)).fetchone()["retired"] == 0


def test_dry_run_changes_nothing(env):
    folder, conn = _approved_book(env)
    _attempt(conn, Q1A)

    def dump(c):
        return [[tuple(r) for r in c.execute(sql)] for sql in (
            "SELECT * FROM staged_items ORDER BY id", "SELECT id, version, retired FROM questions ORDER BY id",
            "SELECT * FROM question_versions ORDER BY question_id", "SELECT * FROM attempts ORDER BY id")]

    before = dump(conn)
    code, text = _run(folder, "--dry-run", "--include-attempted")
    assert code == 0 and "Dry run: nothing was changed." in text
    assert "Delete from the question bank" in text and "Retire (hidden from practice" in text
    assert dump(connect()) == before


def test_only_the_current_version_of_a_file_is_looked_at(env):
    folder = book(env)
    ingest(folder, all_responses())
    conn = connect()
    q1a = conn.execute("SELECT id FROM staged_items WHERE item_id = ?", (Q1A,)).fetchone()["id"]
    staging.approve(conn, q1a)
    ingest(folder, all_responses()[2:], only_file="qns_c01.pdf", force=True)  # version 2; version 1 keeps its approval
    code, text = _run(folder)
    assert code == 0 and "version 2): 0 approved question(s)" in text and "Nothing to do." in text
    conn = connect()
    old = conn.execute("SELECT i.status FROM staged_items i JOIN source_files f ON f.id = i.file_id "
                       "WHERE f.version = 1 AND f.filename = 'qns_c01.pdf' AND i.item_id = ?", (Q1A,)).fetchone()
    assert old["status"] == "approved"
    assert conn.execute("SELECT COUNT(*) FROM questions WHERE id = ?", (Q1A,)).fetchone()[0] == 1


def test_a_file_that_is_not_a_questions_file_is_refused(env):
    folder, conn = _approved_book(env)
    lines = []
    assert unapprove([str(folder), "--file", "answers.pdf"], out=lines.append) == 1
    assert "not a questions file" in lines[0]
    assert unapprove([str(folder), "--file", "missing.pdf"], out=lines.append) == 1
    assert connect().execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 3


def test_importing_a_question_again_clears_retired(bank, points_question):
    qid = points_question["id"]
    bank.execute("UPDATE questions SET retired = 1 WHERE id = ?", (qid,))
    import_questions(bank, [points_question])  # same content
    assert tuple(bank.execute("SELECT retired, version FROM questions WHERE id = ?", (qid,)).fetchone()) == (0, 1)
    bank.execute("UPDATE questions SET retired = 1 WHERE id = ?", (qid,))
    points_question["question"] += " (corrected)"
    import_questions(bank, [points_question])  # changed content: next version
    assert tuple(bank.execute("SELECT retired, version FROM questions WHERE id = ?", (qid,)).fetchone()) == (0, 2)
