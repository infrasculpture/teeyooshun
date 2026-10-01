"""The ingest command end to end: made-up PDFs, a fake API client, a temporary database."""

import json

import pytest

from app.db import connect
from app.ingest.render import image_size
from app.pricing import estimate_usd
from ingest_helpers import (BOOK_PAGES, PAPERS_YAML, SOURCE_YAML, USAGE, a, all_responses, book, ingest, make_pdf,
                            make_source, page, q)


def db():
    return connect()


def test_full_run_stages_matched_questions(env):
    folder = book(env)
    code, client, lines = ingest(folder, all_responses())
    assert code == 0, lines
    assert len(client.requests) == 5
    assert client.requests[0]["messages"][0]["content"][0]["type"] == "image"
    assert "Chapter or unit on the previous page: 01" in client.messages_text(1)

    conn = db()
    items = conn.execute("SELECT * FROM staged_items WHERE status = 'pending' ORDER BY id").fetchall()
    assert [i["item_id"] for i in items] == [
        "science-sec1-test-book-qns_c01-q1a", "science-sec1-test-book-qns_c01-q1b", "science-sec1-test-book-qns_c01-q2"]
    q1b = json.loads(items[1]["record_json"])
    assert q1b["question"] == "Explain why the sky is blue."
    assert q1b["topic"] == "sci-01-scientific-endeavour" and q1b["source"] == "assessment_book"
    assert len(q1b["mark_points"]) == 2
    assert json.loads(items[0]["warnings_json"]) == []
    assert json.loads(items[1]["page_refs_json"]) == [2, 3]
    # Nothing reaches the question bank before approval.
    assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0
    # Q9 in the key has no question.
    assert "1 answers without a question" in "\n".join(lines)


def test_cover_page_keeps_nothing(env):
    folder = book(env)
    ingest(folder, all_responses())
    conn = db()
    cover = conn.execute("""SELECT p.* FROM pages p JOIN source_files f ON f.id = p.file_id
                            WHERE f.filename = 'qns_c01.pdf' AND p.page_no = 1""").fetchone()
    assert (cover["page_type"], cover["status"]) == ("cover", "skipped")
    assert cover["render_path"] is None and cover["raw_json"] is None
    assert not (env / "renders" / "test-book" / "qns_c01-v1" / "p001.png").exists()
    kept = env / "renders" / "test-book" / "qns_c01-v1" / "p002.png"
    assert kept.exists() and max(image_size(kept)) == 1568


def test_tokens_and_cost_are_logged(env):
    ingest(book(env), all_responses())
    conn = db()
    assert conn.execute("SELECT COUNT(*) FROM api_calls WHERE purpose = 'ingest'").fetchone()[0] == 5
    p = conn.execute("SELECT * FROM pages WHERE page_type = 'content' LIMIT 1").fetchone()
    assert p["input_tokens"] == 2000 and p["output_tokens"] == 500
    assert p["cost_usd"] == pytest.approx(estimate_usd("claude-sonnet-5-5", USAGE))


def test_rerun_of_unchanged_files_makes_no_calls(env):
    folder = book(env)
    ingest(folder, all_responses())
    code, client, lines = ingest(folder, [])
    assert code == 0 and client.requests == []
    assert sum("unchanged since the last run" in line for line in lines) == 2


def test_changed_file_supersedes_old_items(env):
    folder = book(env)
    ingest(folder, all_responses())
    make_pdf(folder / "qns_c01.pdf", ["Name: ____", "Q1 revised", "Q1 cont, Q2"])
    code, client, _ = ingest(folder, BOOK_PAGES["qns_c01.pdf"])
    assert code == 0 and len(client.requests) == 3
    conn = db()
    versions = conn.execute("SELECT version, status FROM source_files WHERE filename = 'qns_c01.pdf' ORDER BY version").fetchall()
    assert [(v["version"], v["status"]) for v in versions] == [(1, "superseded"), (2, "done")]
    statuses = [r["status"] for r in conn.execute(
        "SELECT i.status FROM staged_items i JOIN source_files f ON f.id = i.file_id ORDER BY i.id")]
    assert statuses == ["superseded"] * 3 + ["pending"] * 3
    # The unchanged answer key still matches the new version.
    new_items = conn.execute("SELECT warnings_json FROM staged_items WHERE status = 'pending'").fetchall()
    assert all("No matching mark scheme" not in i["warnings_json"] for i in new_items)


def test_force_reads_an_unchanged_file_again_as_a_new_version(env):
    from app.ingest import staging
    folder = book(env)
    ingest(folder, all_responses())
    conn = db()
    q1a = conn.execute("SELECT id FROM staged_items WHERE item_id LIKE '%-q1a'").fetchone()["id"]
    staging.approve(conn, q1a)
    code, client, lines = ingest(folder, BOOK_PAGES["qns_c01.pdf"], only_file="qns_c01.pdf", force=True)
    assert code == 0 and len(client.requests) == 3  # every page read again; answers.pdf untouched
    assert any("qns_c01.pdf: questions (forced, version 2)" in line for line in lines)
    conn = db()
    versions = conn.execute("SELECT version, status FROM source_files WHERE filename = 'qns_c01.pdf' ORDER BY version").fetchall()
    assert [(v["version"], v["status"]) for v in versions] == [(1, "superseded"), (2, "done")]
    old = conn.execute("SELECT i.status FROM staged_items i JOIN source_files f ON f.id = i.file_id "
                       "WHERE f.version = 1 AND f.filename = 'qns_c01.pdf' ORDER BY i.id").fetchall()
    assert [r["status"] for r in old] == ["approved", "superseded", "superseded"]  # approved items stay approved
    assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 1  # still in the bank
    assert conn.execute("SELECT COUNT(*) FROM staged_items WHERE status = 'pending'").fetchone()[0] == 3
    # The next run without --force skips it again.
    _, client, _ = ingest(folder, [])
    assert client.requests == []


def test_dry_run_with_force_counts_unchanged_files(env):
    folder = book(env)
    ingest(folder, all_responses())
    code, client, lines = ingest(folder, [], dry=True, force=True)
    assert code == 0 and client.requests == []
    assert any("qns_c01.pdf: questions, 3 pages, forced" in line for line in lines)
    assert any("Would process 5 pages" in line for line in lines)


def test_upside_down_page_is_rotated_and_read_again(env):
    folder = make_source(env, "rot", SOURCE_YAML, {"qns_c01.pdf": ["Q1"]})
    code, client, lines = ingest(folder, [page(rotation=180), page(questions=[q("1")])])
    assert code == 0 and len(client.requests) == 2
    p = db().execute("SELECT * FROM pages").fetchone()
    assert p["rotation"] == 180 and p["input_tokens"] == 4000
    assert any("rotated 180" in line for line in lines)


def test_failed_page_is_retried_once_then_marked_failed_and_resumed(env):
    folder = make_source(env, "fail", SOURCE_YAML, {"qns_c01.pdf": ["Q1", "Q2"]})
    code, client, _ = ingest(folder, ["TRUNCATED", "TRUNCATED", page(questions=[q("2")])])
    assert code == 0 and len(client.requests) == 3  # page 1 tried twice, page 2 once
    conn = db()
    f = conn.execute("SELECT * FROM source_files").fetchone()
    assert f["status"] == "failed"
    assert "1 page(s) failed; run the command again to retry them." in json.loads(f["warnings_json"])
    # A second run only retries the failed page.
    code, client, _ = ingest(folder, [page(questions=[q("1")])])
    assert len(client.requests) == 1
    f = db().execute("SELECT * FROM source_files").fetchone()
    assert f["status"] == "done" and json.loads(f["warnings_json"]) == []
    assert db().execute("SELECT COUNT(*) FROM staged_items WHERE status = 'pending'").fetchone()[0] == 2


def test_name_box_on_a_later_page_is_skipped_with_a_warning(env):
    folder = make_source(env, "papers", PAPERS_YAML, {"2024_test_eoy.pdf": ["Name", "Name again + Q1", "Q2"]})
    code, _, _ = ingest(folder, [page("cover"), page("cover", cover_other=True),
                                 page(questions=[q("2", topic="sci-05-ray-model-of-light")])])
    assert code == 0
    conn = db()
    warnings = json.loads(conn.execute("SELECT warnings_json FROM source_files").fetchone()["warnings_json"])
    assert warnings == ["Page 2 has a name box and was skipped. It also had questions or answers: check them by hand."]
    assert conn.execute("SELECT COUNT(*) FROM pages WHERE page_type = 'cover' AND render_path IS NULL "
                        "AND raw_json IS NULL").fetchone()[0] == 2
    assert not list((env / "renders" / "papers" / "2024_test_eoy-v1").glob("p00[12].png"))


def test_exam_paper_matches_its_own_key_and_takes_model_topic(env):
    folder = make_source(env, "papers", PAPERS_YAML, {"2024_test_eoy.pdf": ["Q1", "Key"]})
    code, _, _ = ingest(folder, [page(questions=[q("1", topic="sci-05-ray-model-of-light")]),
                                 page("answer", answers=[a("1")])])
    assert code == 0
    item = db().execute("SELECT * FROM staged_items").fetchone()
    record = json.loads(item["record_json"])
    assert record["topic"] == "sci-05-ray-model-of-light" and record["source"] == "publisher_paper"
    assert json.loads(item["warnings_json"]) == []


def test_file_without_units_entry_is_unassigned(env):
    folder = make_source(env, "nounit", SOURCE_YAML, {"qns_c07.pdf": ["Q1"]})
    _, _, lines = ingest(folder, [page(questions=[q("1")])])
    conn = db()
    record = json.loads(conn.execute("SELECT record_json FROM staged_items").fetchone()["record_json"])
    assert record["topic"] == "unassigned"
    assert "no 'units' entry for 07" in conn.execute("SELECT warnings_json FROM source_files").fetchone()["warnings_json"]


def test_dry_run_makes_no_calls_and_stages_nothing(env):
    folder = book(env)
    code, client, lines = ingest(folder, [], dry=True)
    assert code == 0 and client.requests == []
    assert any("qns_c01.pdf: questions, 3 pages, new" in line for line in lines)
    assert any("Would process 5 pages" in line for line in lines)
    assert db().execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 0


def test_bad_source_yaml_stops_before_anything(env):
    folder = make_source(env, "bad", SOURCE_YAML.replace("level: sec1\n", ""), {"qns_c01.pdf": ["Q1"]})
    code, client, lines = ingest(folder, [])
    assert code == 1 and client.requests == []
    assert "'level' is missing" in lines[0]
