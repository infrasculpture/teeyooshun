"""Revise mode: box movement and due dates. Made-up cards, no API calls."""

import json
from datetime import datetime, timezone

import pytest

from app import revise
from revise_helpers import CLIMATE, RAIN, add_cards, settings

NOW = datetime(2026, 10, 6, 9, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("covered, total, move", [
    (1, 1, "up"), (0.5, 1, "stay"), (0, 1, "down"),   # a single point: covered, half-covered, missed
    (3, 5, "up"),                                      # exactly 60% goes up
    (2.5, 5, "stay"), (1.5, 5, "stay"),                # 50% and exactly 30% stay
    (1.4, 5, "down"), (0, 3, "down"),                  # 28% goes down
    (2, 3, "up"), (1, 3, "stay"),
    (4.5, 10, "stay"), (5.9, 10, "stay"), (6, 10, "up"),
    (0, 0, "down"),
])
def test_box_move_bands(covered, total, move):
    assert revise.box_move(covered, total, settings()) == move


def test_box_move_bands_are_settings():
    s = settings(recall_up_pct=80, recall_down_pct=50)
    assert [revise.box_move(c, 10, s) for c in (8, 7, 5, 4.9)] == ["up", "stay", "stay", "down"]


def test_next_box_up_stay_or_down_one():
    assert [revise.next_box(b, "up") for b in (1, 2, 4, 5)] == [2, 3, 5, 5]
    assert [revise.next_box(b, "stay") for b in (1, 3, 5)] == [1, 3, 5]
    assert [revise.next_box(b, "down") for b in (1, 2, 3, 5)] == [1, 1, 2, 4]  # one box down, never below box 1


def _attempt(conn, card, covered, total=None, s=None, now=NOW, method="paper"):
    units = revise.check_units(card)
    attempt_id = revise.record_attempt(conn, card, method=method, covered_ids=[u["id"] for u in units[:covered]],
                                       units_total=total or len(units), settings=s or settings(), now=now)
    return conn.execute("SELECT * FROM recall_attempts WHERE id = ?", (attempt_id,)).fetchone()


def test_new_card_that_is_known_moves_to_box_2_and_is_due_in_two_days(conn):
    add_cards(conn, [CLIMATE])
    attempt = _attempt(conn, dict(CLIMATE, version=1), 1)
    assert (attempt["box_before"], attempt["box_after"]) == (1, 2)
    assert (attempt["points_covered"], attempt["points_total"], attempt["method"]) == (1, 1, "paper")
    assert json.loads(attempt["covered_ids_json"]) == ["P1"] and attempt["card_version"] == 1
    assert attempt["command_word"] == "define"
    progress = revise.get_progress(conn, CLIMATE["id"])
    assert progress["box"] == 2 and progress["last_attempt_at"] == "2026-10-06T09:00:00Z"
    assert progress["due_at"] == "2026-10-08T09:00:00Z"  # box 2: 2 days


def test_poor_recall_goes_down_one_box_not_to_box_1(conn):
    add_cards(conn, [RAIN])
    for _ in range(3):
        _attempt(conn, RAIN, 3)
    assert revise.get_progress(conn, RAIN["id"])["box"] == 4
    attempt = _attempt(conn, RAIN, 0)  # nothing covered
    assert (attempt["box_before"], attempt["box_after"]) == (4, 3)
    assert revise.get_progress(conn, RAIN["id"])["due_at"] == "2026-10-10T09:00:00Z"  # box 3: 4 days


def test_middle_recall_stays_in_the_box_with_the_same_interval(conn):
    add_cards(conn, [RAIN])
    for _ in range(2):
        _attempt(conn, RAIN, 3)
    attempt = _attempt(conn, RAIN, 1)  # 1 of 3 = 33%
    assert (attempt["box_before"], attempt["box_after"]) == (3, 3)
    assert revise.get_progress(conn, RAIN["id"])["due_at"] == "2026-10-10T09:00:00Z"  # box 3: 4 days


def test_half_credit_is_stored_and_counts_for_the_move(conn):
    add_cards(conn, [RAIN])
    units = revise.check_units(RAIN)
    attempt_id = revise.record_attempt(conn, dict(RAIN, version=1), method="typed", covered_ids=["P1"],
                                       units_total=len(units), settings=settings(), now=NOW, points_covered=1.5)
    attempt = conn.execute("SELECT * FROM recall_attempts WHERE id = ?", (attempt_id,)).fetchone()
    assert attempt["points_covered"] == 1.5 and attempt["box_after"] == 1  # 50% from box 1: stays in box 1
    attempt_id = revise.record_attempt(conn, dict(RAIN, version=1), method="typed", covered_ids=["P1", "P2"],
                                       units_total=3, settings=settings(), now=NOW, points_covered=2.5)
    assert conn.execute("SELECT box_after FROM recall_attempts WHERE id = ?", (attempt_id,)).fetchone()[0] == 2


def test_box_5_is_the_top(conn):
    add_cards(conn, [CLIMATE])
    for _ in range(6):
        last = _attempt(conn, CLIMATE, 1)
    assert last["box_after"] == 5 and revise.get_progress(conn, CLIMATE["id"])["due_at"] == "2026-10-22T09:00:00Z"


def test_intervals_and_thresholds_come_from_settings(conn):
    add_cards(conn, [RAIN])
    strict = _attempt(conn, RAIN, 2, s=settings(recall_up_pct=80))  # 2 of 3 = 67%: not enough to go up
    assert strict["box_after"] == 1
    lenient = _attempt(conn, RAIN, 2, s=settings(recall_up_pct=60, box_intervals=[1, 7, 14, 30, 60]))
    assert lenient["box_after"] == 2
    assert revise.get_progress(conn, RAIN["id"])["due_at"] == "2026-10-13T09:00:00Z"  # 7 days
