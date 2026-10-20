"""app.reset_student: activity is wiped, content and setup survive, a dry run changes nothing."""

import pytest

from app import reset_student
from app.db import connect
from app.importer import import_questions

NOW = "2026-01-01T00:00:00Z"
QID = "sci-test-points-q1"  # from the made-up fixture file


def _x(conn, sql, *params):
    return conn.execute(sql, params).lastrowid


@pytest.fixture
def filled(conn, points_question):
    import_questions(conn, [points_question])
    # Content and setup that must survive.
    _x(conn, "INSERT INTO revision_cards (id, subject, level, topic, format, recall, record_json, content_hash, imported_at, updated_at)"
             " VALUES ('card-1', 'science', 'sec1', 't', 'list', 'r', '{}', 'h', ?, ?)", NOW, NOW)
    _x(conn, "INSERT INTO api_calls (created_at, purpose, model) VALUES (?, 'marking', 'm')", NOW)
    _x(conn, "INSERT INTO app_settings (key, value, updated_at) VALUES ('current_level', '\"sec1\"', ?)", NOW)
    _x(conn, "INSERT INTO topic_status (topic, status, updated_at) VALUES ('t', 'taught', ?)", NOW)
    _x(conn, "INSERT INTO reward_items (name, cost, created_at) VALUES ('Game time', 10, ?)", NOW)
    _x(conn, "INSERT INTO question_help (question_id, question_version, tier, help_json, created_at) VALUES (?, 1, 1, '{}', ?)", QID, NOW)
    _x(conn, "INSERT INTO card_help (card_id, card_version, help_json, created_at) VALUES ('card-1', 1, '{}', ?)", NOW)
    _x(conn, "INSERT INTO mcq_explanations (question_id, question_version, chosen_option, why_correct, created_at) VALUES (?, 1, 'A', 'x', ?)", QID, NOW)
    # Student activity.
    session = _x(conn, "INSERT INTO sessions (day, started_at, length_planned, plan_json) VALUES ('2026-01-01', ?, 30, '{}')", NOW)
    rset = _x(conn, "INSERT INTO revise_sets (card_ids_json, created_at, session_id) VALUES ('[]', ?, ?)", NOW, session)
    _x(conn, "INSERT INTO help_views (card_id, card_version, set_id, opened_at) VALUES ('card-1', 1, ?, ?)", rset, NOW)
    _x(conn, "INSERT INTO help_flags (card_id, card_version, created_at) VALUES ('card-1', 1, ?)", NOW)
    conn.execute("UPDATE reward_meta SET value = '2020-01-01T00:00:00Z' WHERE key = 'started_at'")
    _x(conn, "INSERT INTO reward_meta (key, value) VALUES ('companion_image', 'pic.png')")
    first = _x(conn, "INSERT INTO attempts (question_id, answer_text, marks_awarded, max_marks, result_json, created_at)"
                     " VALUES (?, 'a', 1, 2, '{}', ?)", QID, NOW)
    _x(conn, "INSERT INTO attempts (question_id, parent_attempt_id, answer_text, marks_awarded, max_marks, result_json, created_at)"
             " VALUES (?, ?, 'b', 2, 2, '{}', ?)", QID, first, NOW)
    _x(conn, "INSERT INTO attempt_events (attempt_id, event, actor, created_at) VALUES (?, 'marking_looks_wrong', 'student', ?)", first, NOW)
    _x(conn, "INSERT INTO attempt_events (attempt_id, event, actor, created_at) VALUES (?, 'parent_disagree', 'parent', ?)", first, NOW)
    _x(conn, "INSERT INTO hint_uses (question_id, tier, attempt_id, created_at) VALUES (?, 1, ?, ?)", QID, first, NOW)
    _x(conn, "INSERT INTO discussions (conv_key, kind, scope_id, attempt_id, stage, messages_json, created_at, updated_at)"
             " VALUES ('k', 'question', ?, ?, 'after', '[]', ?, ?)", QID, first, NOW, NOW)
    _x(conn, "INSERT INTO recall_attempts (card_id, card_version, method, points_total, points_covered, box_before, box_after, created_at)"
             " VALUES ('card-1', 1, 'typed', 2, 1, 1, 2, ?)", NOW)
    _x(conn, "INSERT INTO card_progress (card_id, box, last_attempt_at, due_at) VALUES ('card-1', 2, ?, ?)", NOW, NOW)
    _x(conn, "INSERT INTO reward_events (ref, kind, day, session_id, at, created_at) VALUES ('step:1', 'step_done', '2026-01-01', ?, ?, ?)", session, NOW, NOW)
    _x(conn, "INSERT INTO reward_unlocks (item, cost, bought_at) VALUES ('frame-1', 5, ?)", NOW)
    _x(conn, "INSERT INTO reward_equipped (slot, item, updated_at) VALUES ('frame', 'frame-1', ?)", NOW)
    _x(conn, "INSERT INTO commissions (day, key, position) VALUES ('2026-01-01', 'c1', 1)")
    _x(conn, "INSERT INTO reward_requests (item_id, name, cost, requested_at) VALUES (1, 'Game time', 10, ?)", NOW)
    conn.commit()
    return conn


def _use_db(conn, monkeypatch):
    """main() opens and closes its own connection, so point it at the same file."""
    path = conn.execute("PRAGMA database_list").fetchone()["file"]
    monkeypatch.setattr(reset_student, "connect", lambda: connect(path))


def _counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in reset_student.all_tables(conn)}


def test_every_table_is_classified(conn):
    # A new table in a future schema file fails here until it is put in WIPE, KEEP or PARTIAL.
    assert reset_student.unclassified(conn) == []


def test_reset_wipes_activity_and_keeps_content(filled, monkeypatch, capsys):
    before = _counts(filled)
    _use_db(filled, monkeypatch)
    assert reset_student.main(["--yes"]) == 0
    after = _counts(filled)
    for table in reset_student.WIPE:
        assert before[table] > 0 and after[table] == 0, table
    for table in reset_student.KEEP:
        assert after[table] == before[table], table
    meta = dict(filled.execute("SELECT key, value FROM reward_meta").fetchall())
    assert meta["started_at"] > "2020-01-01T00:00:00Z" and meta["companion_image"] == "pic.png"
    out = capsys.readouterr().out
    assert f"question {QID}" in out  # the upheld report was listed
    assert "Deleted:" in out


def test_dry_run_and_no_yes_change_nothing(filled, monkeypatch, capsys):
    before = _counts(filled)
    _use_db(filled, monkeypatch)
    assert reset_student.main(["--dry-run"]) == 0
    assert _counts(filled) == before
    assert reset_student.main([]) == 1  # refuses without --yes
    assert _counts(filled) == before
    assert filled.execute("SELECT value FROM reward_meta WHERE key = 'started_at'").fetchone()[0] == "2020-01-01T00:00:00Z"
    assert "attempts" in capsys.readouterr().out
