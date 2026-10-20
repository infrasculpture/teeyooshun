"""Made-up rows for the parent dashboard tests: written straight into the tables, so every timestamp is under
the test's control. No API calls, no real source material."""

import json
from datetime import datetime, timezone

NOW = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)   # Tue 20 Oct 2026, 18:00 in Singapore


def add_attempt(conn, question_id, at, marks, max_marks, *, parent=None, with_help=0, seconds=None,
                answer="an answer", detail=None, level=None, tutor=None, valid=None, counted=None) -> int:
    """One marked attempt. `at` is a UTC stamp such as 2026-10-15T04:00:00Z."""
    result = {"detail": detail if detail is not None else {}}
    cur = conn.execute(
        """INSERT INTO attempts (question_id, question_version, parent_attempt_id, answer_text, marks_awarded,
               max_marks, valid_points, counted_points, level_awarded, result_json, tutor_json, with_help,
               seconds_taken, created_at)
           VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (question_id, parent, answer, marks, max_marks, marks if valid is None else valid,
         marks if counted is None else counted, level, json.dumps(result),
         json.dumps(tutor) if tutor is not None else None, with_help, seconds, at))
    conn.commit()
    return cur.lastrowid


def add_api_call(conn, at, purpose, model, *, input_tokens=0, output_tokens=0, cache_write=0, cache_read=0) -> None:
    conn.execute(
        """INSERT INTO api_calls (created_at, purpose, model, input_tokens, output_tokens,
               cache_creation_input_tokens, cache_read_input_tokens) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (at, purpose, model, input_tokens, output_tokens, cache_write, cache_read))
    conn.commit()


def add_session(conn, started_at, steps, *, ended_at=None, kind="daily") -> int:
    """steps: [{"key", "status", "started_at", "done_at"}] in one block."""
    plan = {"blocks": [{"steps": steps}], "log": []}
    cur = conn.execute(
        "INSERT INTO sessions (kind, day, started_at, ended_at, length_planned, plan_json) VALUES (?, ?, ?, ?, 30, ?)",
        (kind, started_at[:10], started_at, ended_at, json.dumps(plan)))
    conn.commit()
    return cur.lastrowid


def add_event(conn, attempt_id, event, actor, at="2026-10-18T04:00:00Z") -> None:
    conn.execute("INSERT INTO attempt_events (attempt_id, event, actor, created_at) VALUES (?, ?, ?, ?)",
                 (attempt_id, event, actor, at))
    conn.commit()


def add_recall(conn, card_id, at, covered, total, method="typed", set_id=None) -> None:
    conn.execute(
        """INSERT INTO recall_attempts (card_id, card_version, set_id, method, points_total, points_covered,
               box_before, box_after, created_at) VALUES (?, 1, ?, ?, ?, ?, 1, 2, ?)""",
        (card_id, set_id, method, total, covered, at))
    conn.commit()
