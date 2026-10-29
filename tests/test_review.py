"""Review page: parent PIN, approve, reject, edit, approve-all-clean, render images."""

import json

import pytest

from app.db import utc_now
from app.importer import get_question
from conftest import PARENT_PIN

GOOD = {
    "id": "science-sec1-test-book-qns_c01-q1a", "subject": "science", "level": "sec1",
    "topic": "sci-01-scientific-endeavour", "source": "assessment_book", "source_ref": "Test Book, p.5, Q1(a)",
    "context": "", "question": "State one safety rule.", "marks": 1, "command_word": "state", "figure": "none",
    "marking_mode": "points", "mark_points": [{"id": "MP1", "text": "Wear goggles", "key_terms": ["goggles"]}],
}


def _file(conn, tmp_path) -> int:
    now = utc_now()
    source_id = conn.execute("INSERT INTO sources (path, slug, title, subject, level, config_json, created_at, updated_at) "
                             "VALUES ('/x', 'test-book', 'Test Book', 'science', 'sec1', '{}', ?, ?)", (now, now)).lastrowid
    file_id = conn.execute("INSERT INTO source_files (source_id, filename, sha256, version, material_type, unit, status, created_at) "
                           "VALUES (?, 'qns_c01.pdf', 'abc', 1, 'questions', '01', 'done', ?)", (source_id, now)).lastrowid
    conn.commit()
    return file_id


def _stage(conn, file_id, record, warnings=()) -> int:
    now = utc_now()
    staged_id = conn.execute(
        """INSERT INTO staged_items (file_id, kind, item_id, unit, qnum, part, status, record_json, warnings_json,
                                     page_refs_json, raw_json, created_at, updated_at)
           VALUES (?, 'question', ?, '01', '1', 'a', 'pending', ?, ?, '[1]', '{}', ?, ?)""",
        (file_id, record["id"], json.dumps(record), json.dumps(list(warnings)), now, now)).lastrowid
    conn.commit()
    return staged_id


@pytest.fixture
def staged(bank, tmp_path):
    file_id = _file(bank, tmp_path)
    return file_id, _stage(bank, file_id, GOOD)


def login(client):
    client.post("/parent/login", data={"pin": PARENT_PIN, "next": "/"}, follow_redirects=False)


def test_review_needs_the_pin(client, staged):
    _file_id, staged_id = staged
    r = client.get("/review", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/parent/login")
    assert client.post(f"/review/items/{staged_id}/approve").status_code == 403


def test_approve_writes_to_the_bank_and_records_reviewer(client, bank, staged):
    file_id, staged_id = staged
    login(client)
    assert "Wear goggles" in client.get(f"/review/items/{staged_id}").text
    r = client.post(f"/review/items/{staged_id}/approve", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/review/files/{file_id}"
    assert get_question(bank, GOOD["id"])["version"] == 1
    item = bank.execute("SELECT * FROM staged_items WHERE id = ?", (staged_id,)).fetchone()
    assert item["status"] == "approved" and item["reviewed_by"] == "parent" and item["reviewed_at"]


def test_approving_a_changed_item_bumps_the_version(client, bank, staged):
    file_id, staged_id = staged
    login(client)
    client.post(f"/review/items/{staged_id}/approve")
    second = _stage(bank, file_id, dict(GOOD, question="State one safety rule in the lab."))
    client.post(f"/review/items/{second}/approve")
    question = get_question(bank, GOOD["id"])
    assert question["version"] == 2 and question["question"].endswith("in the lab.")


def test_item_with_errors_cannot_be_approved(client, bank, staged):
    file_id, _ = staged
    bad = _stage(bank, file_id, dict(GOOD, mark_points=[]), ["No matching mark scheme found in the answer key"])
    login(client)
    r = client.post(f"/review/items/{bad}/approve")
    assert r.status_code == 422 and "Can&#39;t approve" in r.text
    assert bank.execute("SELECT status FROM staged_items WHERE id = ?", (bad,)).fetchone()["status"] == "pending"
    assert get_question(bank, GOOD["id"]) is None


def test_reject(client, bank, staged):
    _file_id, staged_id = staged
    login(client)
    client.post(f"/review/items/{staged_id}/reject")
    assert bank.execute("SELECT status FROM staged_items WHERE id = ?", (staged_id,)).fetchone()["status"] == "rejected"
    assert get_question(bank, GOOD["id"]) is None


def test_edit_is_checked_and_saved(client, bank, staged):
    _file_id, staged_id = staged
    login(client)
    r = client.post(f"/review/items/{staged_id}/edit", data={"record": "{ not json"})
    assert r.status_code == 422 and "Not valid JSON" in r.text
    edited = dict(GOOD, marks=2)  # now fewer mark points than marks
    client.post(f"/review/items/{staged_id}/edit", data={"record": json.dumps(edited)})
    item = bank.execute("SELECT * FROM staged_items WHERE id = ?", (staged_id,)).fetchone()
    assert item["edited"] == 1 and json.loads(item["record_json"])["marks"] == 2
    warnings = json.loads(item["warnings_json"])
    assert any("Fewer mark points (1) than marks (2)" in w for w in warnings)


def test_approve_all_clean_skips_items_with_warnings(client, bank, staged):
    file_id, clean_id = staged
    warned = _stage(bank, file_id, dict(GOOD, id="science-sec1-test-book-qns_c01-q1b"), ["Model unsure: smudge"])
    login(client)
    r = client.post(f"/review/files/{file_id}/approve-clean")
    assert "Approved 1 item(s) without warnings." in r.text
    statuses = {row["id"]: row["status"] for row in bank.execute("SELECT id, status FROM staged_items")}
    assert statuses == {clean_id: "approved", warned: "pending"}


def _page(conn, file_id, render_path) -> int:
    page_id = conn.execute("INSERT INTO pages (file_id, page_no, render_path, status, created_at) "
                           "VALUES (?, 1, ?, 'done', ?)", (file_id, render_path, utc_now())).lastrowid
    conn.commit()
    return page_id


def test_render_images_are_parent_only_and_stay_in_renders(client, bank, staged, tmp_path):
    file_id, _ = staged
    png = tmp_path / "data" / "renders" / "test-book" / "qns_c01-v1" / "p001.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    good = _page(bank, file_id, "renders/test-book/qns_c01-v1/p001.png")
    assert client.get(f"/review/render/{good}").status_code == 403
    login(client)
    r = client.get(f"/review/render/{good}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    (tmp_path / "data" / "secret.txt").write_text("no")
    bank.execute("UPDATE pages SET render_path = '../data/secret.txt' WHERE id = ?", (good,))
    bank.commit()
    assert client.get(f"/review/render/{good}").status_code == 404


# ---- Send approved questions back to pending (the button on the file page) ----

def _approve(client, staged_id):
    assert client.post(f"/review/items/{staged_id}/approve", follow_redirects=False).status_code == 303


def test_sending_approved_questions_back_needs_the_pin(client, staged):
    file_id, _staged_id = staged
    assert client.post(f"/review/files/{file_id}/unapprove").status_code == 403


def test_the_button_only_shows_when_something_is_approved(client, staged):
    file_id, staged_id = staged
    login(client)
    assert "Send approved questions back to pending" not in client.get(f"/review/files/{file_id}").text
    _approve(client, staged_id)
    assert "Send approved questions back to pending" in client.get(f"/review/files/{file_id}").text


def test_sending_back_removes_the_question_from_the_bank(client, bank, staged):
    file_id, staged_id = staged
    login(client)
    _approve(client, staged_id)
    r = client.post(f"/review/files/{file_id}/unapprove")
    assert r.status_code == 200 and "Sent 1 approved question(s) back to pending." in r.text
    assert bank.execute("SELECT status FROM staged_items WHERE id = ?", (staged_id,)).fetchone()["status"] == "pending"
    assert get_question(bank, GOOD["id"]) is None


def test_sending_back_leaves_attempted_questions_unless_ticked(client, bank, staged):
    file_id, staged_id = staged
    login(client)
    _approve(client, staged_id)
    bank.execute("INSERT INTO attempts (question_id, answer_text, marks_awarded, max_marks, result_json, created_at) "
                 "VALUES (?, 'a', 1, 1, '{}', ?)", (GOOD["id"], utc_now()))
    bank.commit()
    assert "Also retire the 1 she has attempted" in client.get(f"/review/files/{file_id}").text
    r = client.post(f"/review/files/{file_id}/unapprove")
    assert "Sent 0 approved question(s)" in r.text and "Left 1 she has attempted" in r.text
    assert bank.execute("SELECT status FROM staged_items WHERE id = ?", (staged_id,)).fetchone()["status"] == "approved"
    client.post(f"/review/files/{file_id}/unapprove", data={"include_attempted": "1"})
    assert bank.execute("SELECT status FROM staged_items WHERE id = ?", (staged_id,)).fetchone()["status"] == "pending"
    assert bank.execute("SELECT retired FROM questions WHERE id = ?", (GOOD["id"],)).fetchone()["retired"] == 1
    assert bank.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1

def test_one_approved_question_can_be_taken_out_of_the_bank(client, bank, staged):
    file_id, staged_id = staged
    login(client)
    assert client.post(f"/review/items/{staged_id}/unapprove").status_code == 422  # still pending
    _approve(client, staged_id)
    assert "Take out of the bank and send back to pending" in client.get(f"/review/items/{staged_id}").text
    r = client.post(f"/review/items/{staged_id}/unapprove")
    assert r.status_code == 200 and "Removed from the question bank and back to pending." in r.text
    assert bank.execute("SELECT status FROM staged_items WHERE id = ?", (staged_id,)).fetchone()["status"] == "pending"
    assert get_question(bank, GOOD["id"]) is None
    assert "Take out of the bank" not in client.get(f"/review/items/{staged_id}").text  # pending: the usual buttons


def test_one_attempted_question_is_retired_not_deleted(client, bank, staged):
    file_id, staged_id = staged
    login(client)
    _approve(client, staged_id)
    bank.execute("INSERT INTO attempts (question_id, answer_text, marks_awarded, max_marks, result_json, created_at) "
                 "VALUES (?, 'a', 1, 1, '{}', ?)", (GOOD["id"], utc_now()))
    bank.commit()
    assert "attempted this 1 time(s)" in client.get(f"/review/items/{staged_id}").text
    r = client.post(f"/review/items/{staged_id}/unapprove")
    assert "Hidden from practice" in r.text
    assert bank.execute("SELECT retired FROM questions WHERE id = ?", (GOOD["id"],)).fetchone()["retired"] == 1
    assert bank.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1


def test_taking_one_question_out_needs_the_pin(client, staged):
    _file_id, staged_id = staged
    assert client.post(f"/review/items/{staged_id}/unapprove").status_code == 403