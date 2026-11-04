"""Recall ladder, step 2: box caps by format, the stuck rule, retired cards, "failed today", and due ordering by topic
weakness. Made-up cards; no API."""

from datetime import datetime, timedelta, timezone

import pytest

from app import revise
from revise_helpers import CLIMATE, RAIN, add_cards, make_card, point, settings

NOW = datetime(2026, 10, 6, 9, 0, 0, tzinfo=timezone.utc)   # 17:00 on 6 October in Singapore
OTHER = make_card("card-other", "Define other.", {"format": "bullets", "points": [point("Other thing.", ["other"])]},
                  topic="geo-other")


def _attempt(conn, card, covered, when=NOW, **kw):
    units = revise.check_units(card)
    return revise.record_attempt(conn, card, method=kw.pop("method", "paper"),
                                 covered_ids=[u["id"] for u in units[:covered]], units_total=len(units),
                                 settings=kw.pop("settings", settings()), now=when, **kw)


def _progress(conn, card_id):
    return dict(conn.execute("SELECT * FROM card_progress WHERE card_id = ?", (card_id,)).fetchone())


def _make_stuck(conn, card, when=NOW - timedelta(days=10)):
    for k in range(4):
        _attempt(conn, card, 0, when + timedelta(minutes=k))


# ---- Box caps by format ---------------------------------------------------------------------------

@pytest.mark.parametrize("box, move, fmt, expected", [
    (1, "up", "wordbank", 2), (2, "up", "wordbank", 2), (4, "up", "wordbank", 4),   # never lowers a higher card
    (2, "up", "gaps", 3), (3, "up", "gaps", 3), (1, "up", "match", 2), (3, "up", "match", 3),
    (3, "up", "starters", 4), (4, "up", "free", 5), (5, "up", "free", 5),
    (2, "up", None, 3), (4, "up", "paper", 5),                                        # no cap: as before
    (3, "stay", "wordbank", 3), (3, "down", "wordbank", 2), (1, "down", "gaps", 1),    # only a pass is capped
])
def test_a_pass_never_takes_a_card_above_its_formats_highest_box(box, move, fmt, expected):
    assert revise.next_box_for(box, move, fmt) == expected


def test_record_attempt_applies_the_cap_and_stores_the_format(conn):
    add_cards(conn, [CLIMATE])
    _attempt(conn, CLIMATE, 1, fmt="wordbank")
    assert _progress(conn, "card-climate")["box"] == 2
    _attempt(conn, CLIMATE, 1, fmt="wordbank")                    # a second word-bank pass: still box 2
    assert _progress(conn, "card-climate")["box"] == 2
    _attempt(conn, CLIMATE, 1, fmt="gaps")
    assert _progress(conn, "card-climate")["box"] == 3
    _attempt(conn, CLIMATE, 1, fmt="gaps")                        # fill the gaps stops at 3
    assert _progress(conn, "card-climate")["box"] == 3
    _attempt(conn, CLIMATE, 1, fmt="starters")
    assert _progress(conn, "card-climate")["box"] == 4
    row = conn.execute("SELECT format FROM recall_attempts ORDER BY id").fetchall()
    assert [r["format"] for r in row] == ["wordbank", "wordbank", "gaps", "gaps", "starters"]


def test_half_points_and_helps_are_recorded(conn):
    add_cards(conn, [RAIN])
    attempt_id = _attempt(conn, RAIN, 1, points_covered=1.5, fmt="gaps", help_starts=2, low_energy=True)
    row = conn.execute("SELECT * FROM recall_attempts WHERE id = ?", (attempt_id,)).fetchone()
    assert (row["points_covered"], row["format"], row["help_starts"], row["with_help"], row["low_energy"]) == \
        (1.5, "gaps", 2, 1, 1)
    assert row["box_after"] == 1   # 50% is between the 30% and 60% lines: stays


# ---- Stuck ----------------------------------------------------------------------------------------

def test_four_fails_in_a_row_make_a_card_stuck(conn):
    add_cards(conn, [CLIMATE])
    for k in range(3):
        _attempt(conn, CLIMATE, 0, NOW + timedelta(minutes=k))
    assert (_progress(conn, "card-climate")["consecutive_fails"], _progress(conn, "card-climate")["stuck"]) == (3, 0)
    _attempt(conn, CLIMATE, 0, NOW + timedelta(minutes=5))
    p = _progress(conn, "card-climate")
    assert (p["consecutive_fails"], p["stuck"], p["stuck_at"]) == (4, 1, "2026-10-06T09:05:00Z")


def test_a_pass_or_a_stay_resets_the_count(conn):
    add_cards(conn, [RAIN])
    for k in range(3):
        _attempt(conn, RAIN, 0, NOW + timedelta(minutes=k))
    _attempt(conn, RAIN, 3, NOW + timedelta(minutes=4))           # a pass
    assert _progress(conn, "card-rain")["consecutive_fails"] == 0
    _attempt(conn, RAIN, 0, NOW + timedelta(minutes=5))
    _attempt(conn, RAIN, 1, NOW + timedelta(minutes=6))           # 1 of 3 is 33%: a stay, not a fail
    _attempt(conn, RAIN, 0, NOW + timedelta(minutes=7))
    p = _progress(conn, "card-rain")
    assert (p["consecutive_fails"], p["stuck"]) == (1, 0)


def test_the_threshold_is_a_setting(conn):
    add_cards(conn, [CLIMATE])
    for k in range(2):
        _attempt(conn, CLIMATE, 0, NOW + timedelta(minutes=k), settings=settings(stuck_threshold=2))
    assert _progress(conn, "card-climate")["stuck"] == 1


def test_a_stuck_card_stays_stuck_until_a_parent_acts(conn):
    add_cards(conn, [CLIMATE])
    _make_stuck(conn, CLIMATE)
    _attempt(conn, CLIMATE, 1, NOW)                               # e.g. a parent quiz pass
    p = _progress(conn, "card-climate")
    assert (p["stuck"], p["consecutive_fails"]) == (1, 0)


def test_marking_again_can_take_back_the_fail_that_made_a_card_stuck(conn):
    add_cards(conn, [CLIMATE])
    _make_stuck(conn, CLIMATE)
    attempt = conn.execute("SELECT * FROM recall_attempts ORDER BY id DESC LIMIT 1").fetchone()
    check = {"covered_count": 1, "covered_ids": ["P1"], "key_terms_missed": [], "result": {}, "api_call_id": None}
    revise.apply_remark(conn, attempt, check, settings())
    p = _progress(conn, "card-climate")
    assert (p["stuck"], p["stuck_at"], p["consecutive_fails"]) == (0, None, 0)


# ---- Stuck and retired cards are left out ------------------------------------------------------------

def test_stuck_cards_are_in_no_set_warm_up_learn_list_or_count(conn):
    add_cards(conn, [CLIMATE, RAIN])
    _make_stuck(conn, CLIMATE)
    assert revise.build_set(conn, "geography", [], settings(), now=NOW) == ["card-rain"]
    assert revise.build_set(conn, "geography", [], settings(exam_mode=True), now=NOW) == ["card-rain"]
    assert revise.due_cards(conn, "geography", 5, now=NOW) == []
    assert revise.due_only(conn, settings(), now=NOW) == []
    assert revise.learn_cards(conn, "geo-intro", now=NOW) == ["card-rain"]
    assert revise.list_subjects(conn, NOW)[0]["cards"] == 1 and revise.list_topics(conn, "geography", NOW)[0]["cards"] == 1
    assert revise.next_due(conn, "geography", []) is None


def test_retired_cards_are_left_out_but_their_history_is_kept(conn):
    add_cards(conn, [CLIMATE, RAIN])
    _attempt(conn, CLIMATE, 0, NOW - timedelta(days=10))          # due, and would be offered
    assert "card-climate" in revise.build_set(conn, "geography", [], settings(), now=NOW)
    conn.execute("UPDATE card_progress SET retired = 1 WHERE card_id = 'card-climate'")
    conn.commit()
    assert revise.build_set(conn, "geography", [], settings(), now=NOW) == ["card-rain"]
    assert revise.build_set(conn, "geography", [], settings(exam_mode=True), now=NOW) == ["card-rain"]
    assert revise.due_cards(conn, "geography", 5, now=NOW) == [] and revise.learn_cards(conn, "geo-intro", now=NOW) == ["card-rain"]
    assert conn.execute("SELECT COUNT(*) FROM recall_attempts WHERE card_id = 'card-climate'").fetchone()[0] == 1


# ---- A card failed today does not come back today -------------------------------------------------------

def test_a_card_failed_today_is_not_offered_again_today(conn):
    add_cards(conn, [CLIMATE, RAIN])
    _attempt(conn, CLIMATE, 0, NOW - timedelta(hours=1))
    assert revise.learn_cards(conn, "geo-intro", now=NOW) == ["card-rain"]
    assert revise.learn_cards(conn, "geo-intro", now=NOW + timedelta(days=1)) == ["card-rain", "card-climate"]   # new first
    assert revise.build_set(conn, "geography", [], settings(exam_mode=True, cards_per_set=2), now=NOW) \
        == ["card-rain", "card-climate"]   # exam mode is "revise anyway": it ignores the failed-today rule
    conn.execute("UPDATE card_progress SET due_at = ? WHERE card_id = 'card-climate'", ("2026-10-06T08:00:00Z",))
    conn.commit()
    assert revise.due_cards(conn, "geography", 5, now=NOW) == []
    assert "card-climate" not in revise.build_set(conn, "geography", [], settings(), now=NOW)
    assert revise.due_cards(conn, "geography", 5, now=NOW + timedelta(days=1)) == ["card-climate"]


def test_today_is_the_singapore_day(conn):
    add_cards(conn, [CLIMATE, RAIN])
    _attempt(conn, CLIMATE, 0, datetime(2026, 10, 5, 17, 30, tzinfo=timezone.utc))   # 01:30 on the 6th in Singapore
    assert revise.failed_today(conn, NOW) == ["card-climate"]
    conn.execute("DELETE FROM recall_attempts")
    _attempt(conn, CLIMATE, 0, datetime(2026, 10, 5, 15, 30, tzinfo=timezone.utc))   # 23:30 on the 5th
    assert revise.failed_today(conn, NOW) == []


def test_a_parent_quiz_fail_or_a_pass_does_not_hide_a_card(conn):
    add_cards(conn, [CLIMATE, RAIN])
    _attempt(conn, CLIMATE, 0, NOW - timedelta(hours=1), method="parent_quiz")
    _attempt(conn, RAIN, 3, NOW - timedelta(hours=1))
    assert revise.failed_today(conn, NOW) == []


# ---- Due ordering: weakest topic first, then most overdue -----------------------------------------------------

def test_when_more_cards_are_due_than_fit_the_weakest_topic_comes_first(conn):
    add_cards(conn, [CLIMATE, OTHER])
    _attempt(conn, CLIMATE, 1, NOW - timedelta(days=10))          # topic geo-intro: all covered; box 2, due 8 days ago
    _attempt(conn, OTHER, 0, NOW - timedelta(days=3))             # topic geo-other: nothing covered; box 1, due 2 days ago
    # Most overdue alone would put climate first.
    assert revise.due_cards(conn, "geography", 5, now=NOW) == ["card-other", "card-climate"]
    assert revise.build_set(conn, "geography", [], settings(), now=NOW) == ["card-other", "card-climate"]
    assert revise.due_only(conn, settings(), now=NOW) == ["card-other", "card-climate"]
    assert revise.due_cards(conn, "geography", 1, now=NOW) == ["card-other"]


def test_topic_recall_rate_uses_the_last_two_weeks_and_falls_back_to_all_time(conn):
    add_cards(conn, [CLIMATE, OTHER])
    _attempt(conn, CLIMATE, 1, NOW - timedelta(days=20))
    _attempt(conn, CLIMATE, 0, NOW - timedelta(days=1))
    _attempt(conn, OTHER, 1, NOW - timedelta(days=30))            # nothing recent: its whole history is used
    assert revise.topic_rates(conn, NOW) == {"geo-intro": 0.0, "geo-other": 1.0}


# ---- The long-card limit is a setting --------------------------------------------------------------------------

def test_the_long_card_limit_comes_from_the_settings(conn):
    add_cards(conn, [RAIN, CLIMATE])
    assert revise.build_set(conn, "geography", [], settings(), now=NOW) == ["card-rain", "card-climate"]
    assert revise.build_set(conn, "geography", [], settings(long_card_points=2), now=NOW) == ["card-rain"]
