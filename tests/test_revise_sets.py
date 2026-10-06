"""Revise mode: which cards go into a set. Made-up cards, no API calls."""

from datetime import datetime, timedelta, timezone

from app import practice, revise
from app.importer import import_questions
from app.ingest import staging
from app.topics import set_topic_status
from conftest import ONE_MARK_QUESTION
from ingest_helpers import REVISION_YAML, card, card_page, ingest, make_source, pt
from revise_helpers import CLIMATE, COMPARE, EVENTS, FLOODS, MAP, RAIN, add_cards, settings

NOW = datetime(2026, 10, 6, 9, 0, 0, tzinfo=timezone.utc)
GEO = [CLIMATE, RAIN, FLOODS, COMPARE, MAP]  # book order


def _seen(conn, card_record, covered, when):
    units = revise.check_units(card_record)
    revise.record_attempt(conn, card_record, method="paper", covered_ids=[u["id"] for u in units[:covered]],
                          units_total=len(units), settings=settings(), now=when)


def test_new_cards_come_in_book_order_up_to_the_set_size(conn):
    add_cards(conn, GEO)
    assert revise.build_set(conn, "geography", [], settings(), now=NOW) == [c["id"] for c in GEO[:4]]
    assert revise.build_set(conn, "geography", [], settings(cards_per_set=2), now=NOW) == ["card-climate", "card-rain"]


def test_due_cards_come_first_longest_overdue_first_then_new(conn):
    add_cards(conn, GEO)
    _seen(conn, COMPARE, 0, NOW - timedelta(days=3))   # box 1: due 2 days ago
    _seen(conn, FLOODS, 0, NOW - timedelta(days=5))    # box 1: due 4 days ago
    _seen(conn, RAIN, 3, NOW - timedelta(hours=1))     # box 2: not due for 2 days
    ids = revise.build_set(conn, "geography", [], settings(), now=NOW)
    assert ids == ["card-floods", "card-compare", "card-climate", "card-map"]  # due, due, then new in book order
    assert "card-rain" not in ids


def test_only_the_chosen_subject_and_topics(conn):
    add_cards(conn, GEO + [EVENTS])
    assert revise.build_set(conn, "history", [], settings(), now=NOW) == ["card-events"]
    assert revise.build_set(conn, "geography", ["his-u1-temasek-to-singapore"], settings(), now=NOW) == []


def test_exam_mode_ignores_due_dates_and_takes_the_weakest_first(conn):
    add_cards(conn, [CLIMATE, RAIN, FLOODS])
    _seen(conn, CLIMATE, 1, NOW - timedelta(hours=2))  # box 2, not due
    _seen(conn, RAIN, 3, NOW - timedelta(hours=3))
    _seen(conn, RAIN, 3, NOW - timedelta(hours=1))     # box 3, not due
    assert revise.build_set(conn, "geography", [], settings(), now=NOW) == ["card-floods"]  # normal: only the new card
    exam = revise.build_set(conn, "geography", [], settings(exam_mode=True), now=NOW)
    assert exam == ["card-floods", "card-climate", "card-rain"]  # never seen, then box 2, then box 3
    assert revise.build_set(conn, "geography", [], settings(), exam_mode=True, now=NOW) == exam  # the start-page toggle


def test_a_topic_switched_off_is_hidden_in_revise_and_practise(conn):
    add_cards(conn, [CLIMATE, EVENTS])
    import_questions(conn, [ONE_MARK_QUESTION])
    assert [s["subject"] for s in revise.list_subjects(conn)] == ["geography", "history"]
    set_topic_status(conn, "geo-intro", "not_taught")
    set_topic_status(conn, ONE_MARK_QUESTION["topic"], "not_taught")
    assert [s["subject"] for s in revise.list_subjects(conn)] == ["history"]
    assert revise.build_set(conn, "geography", [], settings(), now=NOW) == []
    assert revise.build_set(conn, "geography", [], settings(exam_mode=True), now=NOW) == []
    assert practice.next_question_id(conn, "science") is None and practice.list_subjects(conn) == []
    # "Taught" and "not set" are both offered.
    set_topic_status(conn, "geo-intro", "taught")
    set_topic_status(conn, ONE_MARK_QUESTION["topic"], "")
    assert revise.build_set(conn, "geography", [], settings(), now=NOW) == ["card-climate"]
    assert practice.next_question_id(conn, "science") == ONE_MARK_QUESTION["id"]


def test_due_only_is_for_the_parent_quiz(conn):
    add_cards(conn, [CLIMATE, EVENTS, RAIN])
    assert revise.due_only(conn, settings(), now=NOW) == []
    _seen(conn, EVENTS, 0, NOW - timedelta(days=2))
    assert revise.due_only(conn, settings(), now=NOW) == ["card-events"]


def test_only_approved_cards_are_offered_and_approval_keeps_book_order(env):
    folder = make_source(env, "test-guide", REVISION_YAML, {"rev_u01.pdf": ["Table 1", "Table 2"]})
    rows = [card(f"Define term {n}.", points=[pt(f"Meaning {n}.")]) for n in ("one", "two", "three")]
    ingest(folder, [card_page(rows[:2]), card_page(rows[2:])])
    from app.db import connect
    conn = connect()
    staged = conn.execute("SELECT * FROM staged_items WHERE kind = 'card' ORDER BY id").fetchall()
    assert revise.build_set(conn, "geography", [], settings(), now=NOW) == []  # staged, not yet approved
    for item in (staged[2], staged[0]):  # approved out of order
        staging.approve(conn, item["id"])
    assert revise.build_set(conn, "geography", [], settings(), now=NOW) == [staged[0]["item_id"], staged[2]["item_id"]]
    key = conn.execute("SELECT sort_key FROM revision_cards WHERE id = ?", (staged[2]["item_id"],)).fetchone()[0]
    assert key.startswith("rev_u01.pdf:0002:")
