"""Notes-aware checks: Revise typed checks and generated questions classify each extra point as
on_card / in_notes / unverified / incorrect. Made-up cards; the API is always faked."""

from datetime import datetime, timezone

from app import feedback, revise
from app.marking import marker, notes, prompts, rules
from conftest import ONE_MARK_QUESTION
from ingest_helpers import FakeClient
from revise_helpers import CLIMATE, RAIN, add_cards, settings

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
CLIMATE_SENTENCE = "Climate is the average weather of a place over a long period."
CARDS = {"card-climate": "Define climate."}


def _output(points=(), others=()):
    """Model output: points are (id, awarded, quote, reversed); others are (quote, classification, card_id)."""
    return {"points": [{"id": i, "awarded": a, "evidence_quote": q, "reason": "", "reversed": r}
                       for i, a, q, r in points],
            "missing_key_terms": [], "filler_quotes": [], "misread_question": False, "borderline": False,
            "borderline_reason": "", "full_marks_answer": "", "technique_note": "",
            "other_points": [{"quote": q, "classification": c, "card_id": cid, "reason": "because"}
                             for q, c, cid in others]}


def _rain_points(*awarded):
    quotes = {"P1": "evaporates from the sea", "P2": "condenses into clouds", "P3": "falls as rain"}
    return [(p, p in awarded, quotes[p] if p in awarded else "", False) for p in ("P1", "P2", "P3")]


def _score(output, answer, *, own="card-rain", marks=3, cards=CARDS):
    units = revise.check_units(RAIN)
    question = {**revise.card_as_question(RAIN, units), "marks": marks}
    question["id"] = own
    return rules.score(question, output, answer, None, cards)


ANSWER = f"Water evaporates from the sea and condenses into clouds. {CLIMATE_SENTENCE}"


# ---- The four classes and how they count ---------------------------------------------------------

def test_an_in_notes_point_counts_as_covered_and_fills_a_missing_slot():
    out = _output(_rain_points("P1"), [(CLIMATE_SENTENCE, "in_notes", "card-climate")])
    result = _score(out, ANSWER)
    assert result["counted_points"] == 2 and result["valid_points"] == 2
    assert result["detail"]["classification_counts"] == {"on_card": 1, "in_notes": 1, "unverified": 0, "incorrect": 0}
    assert result["detail"]["other_points"][0]["card_prompt"] == "Define climate."


def test_in_notes_points_never_take_a_question_above_its_marks():
    out = _output(_rain_points("P1", "P2", "P3"), [(CLIMATE_SENTENCE, "in_notes", "card-climate")])
    result = _score(out, f"Water evaporates from the sea, condenses into clouds, falls as rain. {CLIMATE_SENTENCE}")
    assert result["valid_points"] == 4 and result["counted_points"] == 3 and result["marks_awarded"] == 3


def test_unverified_counts_neither_way_and_incorrect_is_only_logged():
    out = _output(_rain_points("P1"), [("The sea is salty", "unverified", ""), ("Rain is solid", "incorrect", "")])
    result = _score(out, "Water evaporates from the sea. The sea is salty. Rain is solid.")
    assert result["counted_points"] == 1
    assert result["detail"]["classification_counts"] == {"on_card": 1, "in_notes": 0, "unverified": 1, "incorrect": 1}


def test_a_reversed_card_point_is_logged_as_incorrect():
    points = [("P1", False, "evaporates from the sea", True), ("P2", False, "", False), ("P3", False, "", False)]
    result = _score(_output(points), "Water evaporates from the sea.")
    assert result["detail"]["classification_counts"]["incorrect"] == 1 and result["counted_points"] == 0


def test_the_quote_must_really_be_in_her_answer_to_get_credit():
    out = _output(_rain_points("P1"), [("Climate is average weather", "in_notes", "card-climate")])
    result = _score(out, "Water evaporates from the sea.")
    assert result["counted_points"] == 1 and result["detail"]["other_points"] == []
    assert "evidence_mismatch" in result["flags"]


def test_in_notes_needs_a_real_card_and_never_the_card_being_checked():
    unknown = _score(_output(_rain_points("P1"), [(CLIMATE_SENTENCE, "in_notes", "card-nope")]), ANSWER)
    assert unknown["counted_points"] == 1 and unknown["detail"]["other_points"][0]["classification"] == "unverified"
    own = _score(_output(_rain_points("P1"), [(CLIMATE_SENTENCE, "in_notes", "card-rain")]), ANSWER)
    assert own["counted_points"] == 1 and own["detail"]["other_points"] == []


def test_a_point_a_mark_point_already_used_is_not_counted_twice():
    out = _output(_rain_points("P1"), [("evaporates from the sea", "in_notes", "card-climate")])
    result = _score(out, ANSWER)
    assert result["counted_points"] == 1 and result["detail"]["other_points"] == []


def test_without_notes_marking_is_exactly_as_before():
    result = rules.score({**ONE_MARK_QUESTION}, _output([("MP1", True, "diffusion", False)]), "It is diffusion.")
    assert "other_points" not in result["detail"] and "classification_counts" not in result["detail"]


# ---- What is sent: notes only for recall checks and generated questions --------------------------------

def test_recall_checks_send_the_topic_notes_as_a_second_cached_system_block(bank):
    add_cards(bank, [CLIMATE, RAIN])
    client = FakeClient([_output(_rain_points("P1"), [(CLIMATE_SENTENCE, "in_notes", "card-climate")]),
                         _output([("P1", True, "average weather", False)])])
    check = revise.typed_check(bank, RAIN, ANSWER, client=client)
    revise.typed_check(bank, CLIMATE, "It is the average weather.", client=client)
    first, second = client.requests[0]["system"], client.requests[1]["system"]
    assert len(first) == 2 and first[1]["cache_control"] == {"type": "ephemeral"}
    assert "card-climate" in first[1]["text"] and "Define climate." in first[1]["text"]
    assert first[1]["text"] == second[1]["text"] and first[0]["text"] == second[0]["text"]  # one cache per topic
    assert '"checked_card_id": "card-rain"' in client.requests[0]["messages"][0]["content"][0]["text"]
    assert check["covered_ids"] == ["P1"] and check["covered_count"] == 2


def test_generated_questions_get_the_notes_and_in_notes_fills_a_mark_slot(bank):
    add_cards(bank, [CLIMATE])
    generated = {**ONE_MARK_QUESTION, "id": "gen-x-q1", "source": "generated", "topic": "geo-intro", "marks": 2,
                 "mark_points": [{"id": "MP1", "text": "Diffusion", "key_terms": ["diffusion"]},
                                 {"id": "MP2", "text": "Osmosis", "key_terms": ["osmosis"]}]}
    out = _output([("MP1", True, "Diffusion", False), ("MP2", False, "", False)],
                  [(CLIMATE_SENTENCE, "in_notes", "card-climate")])
    client = FakeClient([out])
    result = marker.mark_answer(bank, generated, f"Diffusion happens. {CLIMATE_SENTENCE}", client=client)
    assert len(client.requests[0]["system"]) == 2 and result["marks_awarded"] == 2
    assert result["detail"]["classification_counts"]["in_notes"] == 1


def test_ingested_exam_questions_are_sent_exactly_as_before(bank):
    client = FakeClient([{**_output([("MP1", True, "diffusion", False)])}])
    marker.mark_answer(bank, ONE_MARK_QUESTION, "It is diffusion.", client=client)
    request = client.requests[0]
    assert len(request["system"]) == 1 and request["system"][0]["text"] == prompts.POINTS_SYSTEM.format(subject="Science")
    assert "other_points" not in prompts.output_schema(ONE_MARK_QUESTION)["properties"]
    assert "other_points" in prompts.output_schema({**ONE_MARK_QUESTION, "source": "generated"})["properties"]
    assert "checked_card_id" not in request["messages"][0]["content"][0]["text"]


def test_notes_are_the_topics_approved_cards_in_book_order_and_nothing_else(bank):
    other = {**CLIMATE, "id": "card-other", "topic": "geo-weather"}
    add_cards(bank, [CLIMATE, other, RAIN])
    cards = notes.topic_notes(bank, "geo-intro")
    assert [n["card_id"] for n in cards] == ["card-climate", "card-rain"]
    assert cards[1]["points"][1] == "Water vapour cools and condenses into clouds. (This happens as the air rises.)"
    assert notes.render_notes(cards) == notes.render_notes(notes.topic_notes(bank, "geo-intro"))


# ---- Box movement uses covered points -----------------------------------------------------------

def _attempt(conn, check_points, others, answer=ANSWER):
    result = _score(_output(check_points, others), answer)
    covered = [p["id"] for p in result["detail"]["points"] if p["awarded"]]
    attempt_id = revise.record_attempt(
        conn, RAIN, method="typed", covered_ids=covered, units_total=3, settings=settings(), typed_text=answer,
        result=result, points_covered=result["counted_points"], now=NOW)
    return attempt_id, conn.execute("SELECT * FROM recall_attempts WHERE id = ?", (attempt_id,)).fetchone()


def test_an_in_notes_point_can_move_a_card_up_a_box(conn):
    add_cards(conn, [CLIMATE, RAIN])
    _, row = _attempt(conn, _rain_points("P1", "P2"), [(CLIMATE_SENTENCE, "in_notes", "card-climate")])
    assert (row["points_covered"], row["box_after"]) == (3, 2)


def test_an_unverified_point_does_not_help_the_box(conn):
    add_cards(conn, [CLIMATE, RAIN])
    _, row = _attempt(conn, _rain_points("P1", "P2"), [(CLIMATE_SENTENCE, "unverified", "")])
    assert (row["points_covered"], row["box_after"]) == (2, 1)  # 2 of 3 is under the 80% line


# ---- Feedback wording ---------------------------------------------------------------------------

def test_result_page_wording(client, bank):
    add_cards(bank, [CLIMATE, RAIN])
    answer = f"Water evaporates from the sea. {CLIMATE_SENTENCE} The sea is salty. Rain is solid."
    out = _output(_rain_points("P1"), [(CLIMATE_SENTENCE, "in_notes", "card-climate"),
                                       ("The sea is salty", "unverified", ""), ("Rain is solid", "incorrect", "")])
    result = _score(out, answer)
    set_id = revise.create_set(bank, card_ids=["card-rain"], subject="geography")
    revise.record_attempt(bank, RAIN, method="typed", covered_ids=["P1"], units_total=3, settings=settings(),
                          set_id=set_id, typed_text=answer, result=result, points_covered=result["counted_points"])
    html = client.get(f"/revise/sets/{set_id}/result/0").text
    assert "2 / 3" in html and "Your card also lists" in html and "✗ missed" not in html
    assert "Correct, also in your notes on Define climate." in html
    assert 'class="notice">“The sea is salty”' in html
    assert "This may well be right, but it isn’t in your notes. Check with your teacher." in html
    assert "Rain is solid" in html and "This isn’t right." in html


def test_generated_question_feedback_gets_the_same_lists():
    out = _output([("MP1", True, "diffusion", False)],
                  [(CLIMATE_SENTENCE, "in_notes", "card-climate"), ("The sea is salty", "unverified", "")])
    question = {**ONE_MARK_QUESTION, "source": "generated"}
    result = rules.score(question, out, f"Diffusion. {CLIMATE_SENTENCE} The sea is salty.", None, CARDS)
    view = feedback._points_view(result)
    assert [o["card_prompt"] for o in view["in_notes"]] == ["Define climate."]
    assert [o["quote"] for o in view["unverified"]] == ["The sea is salty"] and view["incorrect"] == []
    assert len(view["highlight_quotes"]) == len(view["highlight_labels"]) == 2


# ---- Logging for the dashboard ------------------------------------------------------------------

def test_classifications_are_logged_with_the_attempt_and_can_be_totalled(conn):
    add_cards(conn, [CLIMATE, RAIN])
    _attempt(conn, _rain_points("P1"), [(CLIMATE_SENTENCE, "in_notes", "card-climate"),
                                        ("The sea is salty", "unverified", "")],
             answer=f"{ANSWER} The sea is salty.")
    _attempt(conn, _rain_points("P1", "P2"), [("Rain is solid", "incorrect", "")], answer=f"{ANSWER} Rain is solid.")
    assert notes.classification_totals(conn, "2026-10-05T00:00:00Z", "2026-10-06T00:00:00Z") == {
        "on_card": 3, "in_notes": 1, "unverified": 1, "incorrect": 1}
    assert notes.classification_totals(conn, "2026-10-06T00:00:00Z", "2026-10-07T00:00:00Z")["on_card"] == 0
