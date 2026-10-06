"""Revise mode: box movement and due dates. Made-up cards, no API calls."""

import json
from datetime import datetime, timezone

import pytest

from app import revise
from revise_helpers import CLIMATE, RAIN, add_cards, settings

NOW = datetime(2026, 10, 6, 9, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("covered, total, threshold, known", [
    (1, 1, 80, True), (0, 1, 80, False),          # a single point must be covered
    (3, 3, 80, True), (4, 5, 80, True), (2, 3, 80, False), (3, 5, 80, False),
    (2, 3, 60, True),                              # the threshold is a setting
    (0, 0, 80, False),
])
def test_is_known(covered, total, threshold, known):
    assert revise.is_known(covered, total, threshold) is known


def test_next_box_goes_up_one_or_back_to_one():
    assert [revise.next_box(b, True) for b in (1, 2, 4, 5)] == [2, 3, 5, 5]
    assert [revise.next_box(b, False) for b in (1, 3, 5)] == [1, 1, 1]


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


def test_missed_card_goes_back_to_box_1_and_is_due_tomorrow(conn):
    add_cards(conn, [RAIN])
    for _ in range(3):
        _attempt(conn, RAIN, 3)
    assert revise.get_progress(conn, RAIN["id"])["box"] == 4
    attempt = _attempt(conn, RAIN, 1)  # 1 of 3 points
    assert (attempt["box_before"], attempt["box_after"]) == (4, 1)
    assert revise.get_progress(conn, RAIN["id"])["due_at"] == "2026-10-07T09:00:00Z"


def test_box_5_is_the_top(conn):
    add_cards(conn, [CLIMATE])
    for _ in range(6):
        last = _attempt(conn, CLIMATE, 1)
    assert last["box_after"] == 5 and revise.get_progress(conn, CLIMATE["id"])["due_at"] == "2026-10-22T09:00:00Z"


def test_intervals_and_threshold_come_from_settings(conn):
    add_cards(conn, [RAIN])
    strict = _attempt(conn, RAIN, 2, s=settings(known_threshold=80))  # 2 of 3 = 67%
    assert strict["box_after"] == 1
    lenient = _attempt(conn, RAIN, 2, s=settings(known_threshold=60, box_intervals=[1, 7, 14, 30, 60]))
    assert lenient["box_after"] == 2
    assert revise.get_progress(conn, RAIN["id"])["due_at"] == "2026-10-13T09:00:00Z"  # 7 days
