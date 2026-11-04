"""Recall ladder, step 3: word bank and fill the gaps, one point at a time, through the real pages.
Made-up cards; no API calls (these formats are checked in code)."""

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from app import app_settings, revise
from revise_helpers import CLIMATE, FLOODS, MAP, RAIN, add_cards, settings

NOW = datetime.now(timezone.utc)


@pytest.fixture
def ladder_on(bank):
    assert app_settings.save(bank, {"recall_ladder": True}) == []


def start(client, **form) -> str:
    r = client.post("/revise/start", data={"subject": "geography", **form}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/study/0")
    return r.headers["location"][:-len("/study/0")]


def attempts(conn):
    return conn.execute("SELECT * FROM recall_attempts ORDER BY id").fetchall()


def progress(conn, card_id):
    return dict(conn.execute("SELECT * FROM card_progress WHERE card_id = ?", (card_id,)).fetchone())


def answer(client, base, i, slot, **fields):
    r = client.post(f"{base}/point/{i}/{slot}", data=fields, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"]


# ---- Word bank: a new card ---------------------------------------------------------------------------

def test_a_new_card_is_a_word_bank_one_point_at_a_time(client, bank, ladder_on):
    add_cards(bank, [CLIMATE, RAIN])
    base = start(client)
    html = client.get(f"{base}/recall/0").text
    assert "Word bank" in html and "Point 1 of 1" in html and "Define climate." in html
    assert f'action="{base}/point/0/1"' in html and 'name="b0"' in html and f'action="{base}/typed/0"' not in html
    words = re.findall(r'data-word="([^"]*)"', html)
    assert words.count("average weather") == 1                              # the answer, once
    assert {"evaporates", "condenses", "droplets"} <= set(words)             # wrong words from the same topic's other card
    assert html.count("average weather") == 2                               # only as that bank word (attribute and label)
    assert "Climate is the" in html and "Singapore is hot" not in html     # the rest of the card is not shown


def test_a_right_answer_gives_feedback_then_records_the_card_and_moves_it_up_one_box(client, bank, ladder_on):
    add_cards(bank, [CLIMATE])
    base = start(client)
    client.get(f"{base}/recall/0")
    assert attempts(bank) == []
    where = answer(client, base, 0, 1, b0="average weather")
    assert where == f"{base}/feedback/0/1"
    html = client.get(where).text
    assert "Yes, that" in html and "See how you did" in html and f'href="{base}/result/0"' in html
    [row] = attempts(bank)
    assert (row["format"], row["method"], row["points_covered"], row["points_total"]) == ("wordbank", "typed", 1, 1)
    assert (row["box_before"], row["box_after"]) == (1, 2) and row["typed_text"] is None
    assert client.get(f"{base}/recall/0", follow_redirects=False).headers["location"] == f"{base}/result/0"
    result = client.get(f"{base}/result/0").text
    assert "1 / 1" in result and "Known." in result


def test_a_wrong_answer_shows_the_cards_wording_gently_and_counts_a_fail(client, bank, ladder_on):
    add_cards(bank, [CLIMATE])
    base = start(client)
    client.get(f"{base}/recall/0")
    html = client.get(answer(client, base, 0, 1, b0="long period")).text
    assert "Not quite" in html and "You put “long period”. The card says “average weather”." in html
    [row] = attempts(bank)
    assert (row["points_covered"], row["box_after"]) == (0, 1)
    assert progress(bank, "card-climate")["consecutive_fails"] == 1


def test_points_are_asked_in_order_and_the_card_is_recorded_after_the_last(client, bank, ladder_on):
    add_cards(bank, [RAIN])
    base = start(client)
    html = client.get(f"{base}/recall/0").text
    assert "Point 1 of 3" in html and html.count('class="dot current"') == 1 and html.count('class="dot todo"') == 2
    # skipping ahead does nothing
    assert answer(client, base, 0, 2, b0="condenses") == f"{base}/recall/0"
    assert bank.execute("SELECT COUNT(*) FROM recall_points").fetchone()[0] == 0
    answer(client, base, 0, 1, b0="evaporates")
    assert answer(client, base, 0, 1, b0="evaporates") == f"{base}/recall/0"      # a repeat is ignored
    assert bank.execute("SELECT COUNT(*) FROM recall_points").fetchone()[0] == 1 and attempts(bank) == []
    html = client.get(f"{base}/recall/0").text
    assert "Point 2 of 3" in html and html.count('class="dot full"') == 1
    answer(client, base, 0, 2, b0="droplets")                                       # wrong
    last = client.get(answer(client, base, 0, 3, b0="droplets")).text              # right
    assert "See how you did" in last and last.count('class="dot full"') == 2 and last.count('class="dot miss"') == 1
    [row] = attempts(bank)
    assert json.loads(row["covered_ids_json"]) == ["P1", "P3"] and (row["points_covered"], row["points_total"]) == (2, 3)
    assert row["box_after"] == 2                                                    # 67%: up, capped at box 2 by the word bank
    detail = json.loads(row["result_json"])["detail"]["points"]
    assert [p["awarded"] for p in detail] == [True, False, True]


def test_the_result_page_marks_a_half_point(client, bank, ladder_on):
    add_cards(bank, [make_two_blank_card()])
    base = start(client)
    client.get(f"{base}/recall/0")
    answer(client, base, 0, 1, b0="carbon dioxide", b1="oxygen")                    # one of two blanks right: half
    [row] = attempts(bank)
    assert (row["points_covered"], row["box_after"]) == (0.5, 1)
    assert "½ partly" in client.get(f"{base}/result/0").text


def make_two_blank_card():
    from revise_helpers import make_card, point
    return make_card("card-two", "State what plants use.", {"format": "bullets", "points": [
        point("Plants absorb carbon dioxide during photosynthesis.", ["carbon dioxide", "photosynthesis"])]},
        command="state")


# ---- Fill the gaps: box 2 ----------------------------------------------------------------------------------

def _into_box_2(conn):
    revise.record_attempt(conn, CLIMATE, method="paper", covered_ids=["P1"], units_total=1, settings=settings(),
                          now=NOW - timedelta(days=5))
    assert progress(conn, "card-climate")["box"] == 2


def test_box_2_is_fill_the_gaps_with_a_first_letter_hint_and_spelling_slips_forgiven(client, bank, ladder_on):
    add_cards(bank, [CLIMATE])
    _into_box_2(bank)
    base = start(client)
    html = client.get(f"{base}/recall/0").text
    assert "Fill the gaps" in html and 'name="g0"' in html and "data-word" not in html and "First letter" in html
    assert "average weather" not in html
    hint = client.post(f"{base}/letter/0/1/0").text
    assert "Starts with <strong>a</strong>" in hint
    html = client.get(answer(client, base, 0, 1, g0="Averag Weather")).text         # a slip and a capital letter
    assert "Yes, that" in html
    [row] = [r for r in attempts(bank) if r["format"]]
    assert (row["format"], row["points_covered"], row["box_before"], row["box_after"]) == ("gaps", 1, 2, 3)


def test_fill_the_gaps_rejects_a_wrong_word(client, bank, ladder_on):
    add_cards(bank, [CLIMATE])
    _into_box_2(bank)
    base = start(client)
    client.get(f"{base}/recall/0")
    html = client.get(answer(client, base, 0, 1, g0="long period")).text
    assert "Not quite" in html and "The card says “average weather”." in html
    assert [r for r in attempts(bank) if r["format"]][0]["points_covered"] == 0


def test_the_hint_is_for_fill_the_gaps_only(client, bank, ladder_on):
    add_cards(bank, [CLIMATE])
    base = start(client)
    client.get(f"{base}/recall/0")                                                  # word bank
    assert "No hint here" in client.post(f"{base}/letter/0/1/0").text


# ---- When the ladder doesn't apply ------------------------------------------------------------------------------

def test_a_card_with_nothing_to_blank_goes_to_starters_instead(client, bank, ladder_on):
    add_cards(bank, [FLOODS])
    base = start(client)
    html = client.get(f"{base}/recall/0").text
    assert "Starters" in html and 'name="answer"' in html and "Word bank" not in html and f'action="{base}/typed/0"' not in html


def test_picture_cards_and_the_switch_being_off_keep_the_old_pages(client, bank, ladder_on):
    add_cards(bank, [MAP, CLIMATE])
    base = start(client)
    assert "Picture it, or sketch it on paper" in client.get(f"{base}/recall/0").text
    app_settings.save(bank, {"recall_ladder": False})
    assert f'action="{base}/typed/1"' in client.get(f"{base}/recall/1").text


def test_the_format_is_fixed_when_the_card_is_first_opened(client, bank, ladder_on):
    add_cards(bank, [CLIMATE])
    base = start(client)
    assert "Word bank" in client.get(f"{base}/recall/0").text
    bank.execute("INSERT INTO card_progress (card_id, box, last_attempt_at, due_at) VALUES ('card-climate', 2, 'x', 'x')")
    bank.commit()
    assert "Word bank" in client.get(f"{base}/recall/0").text
    assert bank.execute("SELECT format FROM recall_work").fetchone()["format"] == "wordbank"


# ---- Starters (box 3) and free recall (boxes 4 and 5): the model, one point at a time --------------------------------

def _in_box(conn, card_id, box):
    conn.execute("INSERT INTO card_progress (card_id, box, last_attempt_at, due_at) VALUES (?, ?, ?, ?)",
                 (card_id, box, "2000-01-01T00:00:00Z", "2000-01-01T00:00:00Z"))
    conn.commit()


def points_rows(conn):
    return conn.execute("SELECT * FROM recall_points ORDER BY slot").fetchall()


def test_box_3_is_starters_with_the_opening_words_shown(client, bank, ladder_on):
    add_cards(bank, [RAIN])
    _in_box(bank, "card-rain", 3)
    base = start(client)
    html = client.get(f"{base}/recall/0").text
    assert "Starters" in html and "Point 1 of 3" in html and "Water evaporates from" in html
    assert "the sea" not in html and 'name="answer"' in html and "Give me a start" in html and "I don't know" in html
    assert "Check on paper" in html


def test_starters_are_checked_one_point_at_a_time_and_the_card_moves_up(client, bank, ladder_on, fake_marker):
    add_cards(bank, [RAIN])
    _in_box(bank, "card-rain", 3)
    base = start(client)
    client.get(f"{base}/recall/0")
    html = client.get(answer(client, base, 0, 1, answer="the sea.")).text
    assert "Yes, that" in html and "Water evaporates from the sea." in html
    assert [c["answer"] for c in fake_marker] == ["Water evaporates from the sea."]
    assert fake_marker[0]["question"]["marks"] == 1                                  # just this point
    assert "Check on paper" not in client.get(f"{base}/recall/0").text            # only before the first point
    answer(client, base, 0, 2, answer="condenses into clouds")
    assert attempts(bank) == []
    answer(client, base, 0, 3, answer="join and fall as rain")
    assert len(fake_marker) == 3
    [row] = attempts(bank)
    assert (row["format"], row["points_covered"], row["points_total"], row["box_before"], row["box_after"]) == \
        ("starters", 3, 3, 3, 4)
    assert row["typed_text"].startswith("1. Water evaporates from the sea.\n2. Water vapour cools and condenses")
    assert "What you wrote" in client.get(f"{base}/result/0").text


def test_give_me_a_start_shows_more_words_caps_the_point_at_half_and_is_logged(client, bank, ladder_on, fake_marker):
    add_cards(bank, [CLIMATE])
    _in_box(bank, "card-climate", 3)
    base = start(client)
    client.get(f"{base}/recall/0")
    fragment = client.post(f"{base}/start/0/1").text
    assert "Climate is the average weather of a" in fragment and "at most half" in fragment
    client.post(f"{base}/start/0/1")                                                  # a second press changes nothing
    [pending] = points_rows(bank)
    assert (pending["start_used"], pending["score"]) == (1, None)
    html = client.get(f"{base}/recall/0").text                                        # a reload keeps the start
    assert "Climate is the average weather of a" in html and "Give me a start" not in html
    html = client.get(answer(client, base, 0, 1, answer="average weather")).text
    assert "Nearly" in html and "scored at most half" in html
    [row] = attempts(bank)
    assert (row["points_covered"], row["help_starts"], row["with_help"], row["box_after"]) == (0.5, 1, 1, 3)
    assert json.loads(row["covered_ids_json"]) == []
    assert progress(bank, "card-climate")["consecutive_fails"] == 0                    # a start is never a fail


def test_there_is_no_start_in_the_blank_formats(client, bank, ladder_on):
    add_cards(bank, [CLIMATE])
    base = start(client)
    client.get(f"{base}/recall/0")
    assert "at most half" not in client.post(f"{base}/start/0/1").text
    assert bank.execute("SELECT COUNT(*) FROM recall_points").fetchone()[0] == 0


def test_free_recall_shows_numbered_slots_and_credits_points_in_any_order(client, bank, ladder_on, fake_marker):
    add_cards(bank, [RAIN])
    _in_box(bank, "card-rain", 4)
    base = start(client)
    html = client.get(f"{base}/recall/0").text
    assert "Free recall" in html and "Point 1 of 3" in html and html.count('class="dot todo"') == 2
    assert "Water evaporates" not in html and "Give me a start" in html
    html = client.get(answer(client, base, 0, 1, answer="Droplets join and fall as rain.")).text
    assert "Yes, that" in html and "Droplets" in html and "Water evaporates" not in html   # she skipped ahead: fine
    assert [p["unit_id"] for p in points_rows(bank)] == ["P3"]
    # one answer can cover two points, and then nothing is left to ask
    html = client.get(answer(client, base, 0, 2, answer="Water evaporates from the sea and condenses.")).text
    assert "See how you did" in html
    assert [c["question"]["marks"] for c in fake_marker] == [3, 2]                      # the second asks only what is left
    [row] = attempts(bank)
    assert (row["format"], row["points_covered"], row["box_before"], row["box_after"]) == ("free", 3, 4, 5)
    assert json.loads(row["covered_ids_json"]) == ["P1", "P2", "P3"]


def test_free_recall_never_shows_a_point_that_was_not_matched(client, bank, ladder_on):
    add_cards(bank, [RAIN])
    _in_box(bank, "card-rain", 4)
    base = start(client)
    client.get(f"{base}/recall/0")
    html = client.get(answer(client, base, 0, 1, answer="Rain is wet.")).text
    assert "didn't match a point on the card yet" in html and "Water evaporates" not in html
    answer(client, base, 0, 2, answer="It is cold.")
    answer(client, base, 0, 3, answer="Clouds are grey.")
    [row] = attempts(bank)
    assert (row["points_covered"], row["box_before"], row["box_after"]) == (0, 4, 3)
    assert progress(bank, "card-rain")["consecutive_fails"] == 1


def test_a_long_card_stays_on_starters_in_the_top_boxes(client, bank, ladder_on):
    from revise_helpers import make_card, point
    long_card = make_card("card-long", "Describe the made-up long topic.", {"format": "list", "points": [
        point(f"Made-up point number {n}.") for n in range(1, 6)]}, command="describe")
    add_cards(bank, [long_card])
    _in_box(bank, "card-long", 5)
    base = start(client)
    assert "Starters" in client.get(f"{base}/recall/0").text


def test_when_the_check_fails_she_can_try_again_or_mark_it_herself(client, bank, ladder_on):
    add_cards(bank, [CLIMATE])
    _in_box(bank, "card-climate", 3)
    base = start(client)
    client.get(f"{base}/recall/0")
    r = client.post(f"{base}/point/0/1", data={"answer": "FAIL"})
    assert r.status_code == 503 and "Mark this point yourself instead" in r.text and "FAIL" in r.text
    assert points_rows(bank) == [] and attempts(bank) == []
    r = client.post(f"{base}/selfrate/0/1", data={"rating": "got"}, follow_redirects=False)
    assert r.headers["location"] == f"{base}/feedback/0/1"
    [row] = attempts(bank)
    assert (row["points_covered"], row["box_after"]) == (1, 4)


def test_i_dont_know_skips_the_point_without_calling_the_model(client, bank, ladder_on, fake_marker):
    add_cards(bank, [CLIMATE])
    _in_box(bank, "card-climate", 3)
    base = start(client)
    client.get(f"{base}/recall/0")
    r = client.post(f"{base}/point/0/1", data={"skip": "1"}, follow_redirects=False)
    html = client.get(r.headers["location"]).text
    assert "Not quite" in html and "average weather" in html and fake_marker == []
    assert attempts(bank)[0]["points_covered"] == 0


def test_an_empty_answer_is_asked_for_again(client, bank, ladder_on):
    add_cards(bank, [CLIMATE])
    _in_box(bank, "card-climate", 3)
    base = start(client)
    client.get(f"{base}/recall/0")
    r = client.post(f"{base}/point/0/1", data={"answer": "  "})
    assert r.status_code == 422 and "Write an answer before submitting." in r.text
