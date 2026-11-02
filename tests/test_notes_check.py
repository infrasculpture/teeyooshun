"""The recall rubric: Revise typed recall and generated questions (covered / partial / missed, the whole card in
the request, and each extra statement classified on_card / in_notes / unverified / incorrect). Ingested exam
questions keep Practice marking. Made-up cards; the API is always faked."""

import json
from datetime import datetime, timezone

from app import feedback, revise
from app.config import get_settings
from app.marking import marker, notes, prompts, rules
from conftest import ONE_MARK_QUESTION
from ingest_helpers import FakeClient
from revise_helpers import CLIMATE, RAIN, add_cards, make_card, point, settings

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
CLIMATE_SENTENCE = "Climate is the average weather of a place over a long period."
CARDS = {"card-climate": "Define climate."}


def _output(points=(), others=(), **extra):
    """Model output: points are (id, status, quote[, reversed]); others are (quote, classification, card_id)."""
    out = {"points": [{"id": p[0], "status": p[1], "evidence_quote": p[2], "reason": "", "reversed": len(p) > 3 and p[3]}
                      for p in points],
           "missing_key_terms": [], "filler_quotes": [], "misread_question": False, "borderline": False,
           "borderline_reason": "", "one_to_add": "", "full_marks_answer": "", "technique_note": "",
           "other_points": [{"quote": q, "classification": c, "card_id": cid, "where_on_card": "", "reason": "because"}
                            for q, c, cid in others]}
    out.update(extra)
    return out


def _rain_points(*covered, partial=()):
    quotes = {"P1": "evaporates from the sea", "P2": "condenses into clouds", "P3": "falls as rain"}
    return [(p, "covered" if p in covered else "partial" if p in partial else "missed",
             quotes[p] if p in covered or p in partial else "") for p in ("P1", "P2", "P3")]


def _score(output, answer, *, own="card-rain", marks=3, cards=CARDS):
    units = revise.check_units(RAIN)
    question = {**revise.card_as_question(RAIN, units), "marks": marks}
    question["id"] = own
    return rules.score(question, output, answer, None, cards)


ANSWER = f"Water evaporates from the sea and condenses into clouds. {CLIMATE_SENTENCE}"


# ---- Covered, partial, missed ------------------------------------------------------------------

def test_partial_is_half_credit_and_the_exact_score_is_kept():
    result = _score(_output(_rain_points("P1", partial=("P2",))), ANSWER)
    assert result["detail"]["score"] == 1.5
    assert result["marks_awarded"] == 1 and result["counted_points"] == 1  # attempts store whole marks (rounded down)
    p1, p2, p3 = result["detail"]["points"]
    assert (p1["status"], p1["awarded"], p1["partial"], p1["credit"]) == ("covered", True, False, 1.0)
    assert (p2["status"], p2["awarded"], p2["partial"], p2["credit"]) == ("partial", False, True, 0.5)
    assert (p3["status"], p3["credit"]) == ("missed", 0.0)


def test_a_partial_or_covered_claim_needs_her_exact_words():
    result = _score(_output([("P1", "partial", "evaporates from the lake"), ("P2", "covered", "condenses into clouds"),
                             ("P3", "missed", "")]), ANSWER)
    assert result["detail"]["score"] == 1.0 and "evidence_mismatch" in result["flags"]
    assert result["detail"]["points"][0]["not_awarded_because"] == "evidence_mismatch"


def test_a_reversed_point_scores_nothing_and_is_logged_as_incorrect():
    points = [("P1", "covered", "evaporates from the sea", True), ("P2", "missed", ""), ("P3", "missed", "")]
    result = _score(_output(points), "Water evaporates from the sea.")
    assert result["detail"]["classification_counts"]["incorrect"] == 1 and result["detail"]["score"] == 0


def test_the_score_never_goes_above_the_cap():
    result = _score(_output(_rain_points("P1", "P2", "P3")), "Water evaporates from the sea, condenses into clouds, "
                    "falls as rain.", marks=2)
    assert result["detail"]["score"] == 2 and result["valid_points"] == 3 and result["counted_points"] == 2


def test_one_to_add_is_passed_through():
    result = _score(_output(_rain_points("P1"), one_to_add="Add that the vapour cools."), ANSWER)
    assert result["detail"]["one_to_add"] == "Add that the vapour cools."


# ---- The extra statements: shown, never scored ---------------------------------------------------

def test_extras_are_classified_and_never_change_the_score():
    out = _output(_rain_points("P1"), [(CLIMATE_SENTENCE, "in_notes", "card-climate"),
                                       ("The sea is salty", "unverified", ""), ("Rain is solid", "incorrect", ""),
                                       ("clouds are made of droplets", "on_card", "")])
    result = _score(out, f"Water evaporates from the sea. {CLIMATE_SENTENCE} The sea is salty. Rain is solid. "
                         "Also clouds are made of droplets.")
    assert result["detail"]["score"] == 1 and result["marks_awarded"] == 1
    assert result["detail"]["classification_counts"] == {
        "on_card": 1, "in_notes": 1, "unverified": 1, "also_on_card": 1, "incorrect": 1}
    assert result["detail"]["other_points"][0]["card_prompt"] == "Define climate."


def test_the_quote_must_really_be_in_her_answer_to_be_listed():
    out = _output(_rain_points("P1"), [("Climate is average weather", "in_notes", "card-climate")])
    result = _score(out, "Water evaporates from the sea.")
    assert result["detail"]["other_points"] == []


def test_in_notes_needs_a_real_card_and_naming_the_checked_card_means_on_card():
    unknown = _score(_output(_rain_points("P1"), [(CLIMATE_SENTENCE, "in_notes", "card-nope")]), ANSWER)
    assert unknown["detail"]["other_points"][0]["classification"] == "unverified"
    own = _score(_output(_rain_points("P1"), [(CLIMATE_SENTENCE, "in_notes", "card-rain")]), ANSWER)
    assert own["detail"]["other_points"][0]["classification"] == "on_card" and own["detail"]["score"] == 1


def test_a_statement_a_mark_point_already_used_is_not_listed_twice():
    out = _output(_rain_points("P1"), [("evaporates from the sea", "on_card", "")])
    assert _score(out, ANSWER)["detail"]["other_points"] == []


# ---- What is sent ----------------------------------------------------------------------------------

def _block(request, tag):
    return next(b["text"] for b in request["messages"][0]["content"] if b["text"].startswith(f"<{tag}>"))


def test_the_whole_card_goes_to_the_marker_examples_and_notes_included(bank):
    card = make_card("card-heads", "Describe how rebels were punished.",
                     {"format": "list", "points": [point("Rebels were executed.", ["executed"],
                                                         sub=["Heads were displayed in public."])]},
                     examples=["The leader's head was placed on a pole at the market."], notes="Remark: made-up note.")
    add_cards(bank, [card])
    client = FakeClient([_output([("P1", "covered", "executed")])])
    revise.typed_check(bank, card, "They were executed.", client=client)
    shown = json.loads(_block(client.requests[0], "revision_card").split("\n", 1)[1].rsplit("\n", 1)[0])
    assert shown["prompt"] == "Describe how rebels were punished."
    assert shown["answer"]["points"][0]["sub"][0]["text"] == "Heads were displayed in public."
    assert shown["examples"] == ["The leader's head was placed on a pole at the market."]
    assert shown["notes"] == "Remark: made-up note."
    assert '"checked_card_id": "card-heads"' in _block(client.requests[0], "mark_scheme")


def test_the_recall_prompt_is_idea_based_and_makes_the_model_check_the_whole_card(bank):
    system = prompts.system_prompt({**revise.card_as_question(RAIN, revise.check_units(RAIN))})
    assert "own words" in system and "Synonyms and paraphrase count" in system
    assert "Key terms are required only where the term itself is the point" in system
    assert "BEFORE you classify" in system and "check the whole card" in system
    assert "Never say that something is 'not in your notes'" in system
    assert "partial" in system
    assert "strict but fair school examiner" not in system   # not the exam prompt


def test_recall_checks_send_the_topic_notes_as_a_second_cached_system_block(bank):
    add_cards(bank, [CLIMATE, RAIN])
    client = FakeClient([_output(_rain_points("P1"), [(CLIMATE_SENTENCE, "in_notes", "card-climate")]),
                         _output([("P1", "covered", "average weather")])])
    check = revise.typed_check(bank, RAIN, ANSWER, client=client)
    revise.typed_check(bank, CLIMATE, "It is the average weather.", client=client)
    first, second = client.requests[0]["system"], client.requests[1]["system"]
    assert len(first) == 2 and first[1]["cache_control"] == {"type": "ephemeral"}
    assert "card-climate" in first[1]["text"] and "Define climate." in first[1]["text"]
    assert first[1]["text"] == second[1]["text"] and first[0]["text"] == second[0]["text"]  # one cache per topic
    assert check["covered_ids"] == ["P1"] and check["covered_count"] == 1  # her climate sentence is shown, not scored


def test_typed_check_returns_partial_ids_and_the_half_point_score(bank):
    add_cards(bank, [RAIN])
    client = FakeClient([_output(_rain_points("P1", partial=("P2",)))])
    check = revise.typed_check(bank, RAIN, ANSWER, client=client)
    assert (check["covered_ids"], check["partial_ids"], check["covered_count"]) == (["P1"], ["P2"], 1.5)


# ---- Model, effort and cost -------------------------------------------------------------------------

def test_recall_model_defaults_to_the_marking_model(monkeypatch):
    monkeypatch.setenv("MARKING_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("MARKING_EFFORT", "medium")
    monkeypatch.setenv("LIGHT_MODEL", "claude-haiku-4-5")
    monkeypatch.delenv("RECALL_MODEL", raising=False)
    monkeypatch.delenv("RECALL_EFFORT", raising=False)
    s = get_settings()
    assert (s.recall_model, s.recall_effort) == ("claude-opus-5-5", "medium")
    monkeypatch.setenv("RECALL_MODEL", "   ")
    assert get_settings().recall_model == "claude-opus-5-5"   # blank is the same as not set


def test_recall_model_can_be_the_light_model_or_a_named_model(monkeypatch):
    monkeypatch.setenv("MARKING_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("MARKING_EFFORT", "high")
    monkeypatch.setenv("LIGHT_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("LIGHT_EFFORT", "")
    monkeypatch.setenv("RECALL_MODEL", "light")
    s = get_settings()
    assert (s.recall_model, s.recall_effort) == ("claude-haiku-4-5", "")   # Haiku rejects effort: none is sent
    monkeypatch.setenv("RECALL_MODEL", "claude-sonnet-5-5")
    assert get_settings().recall_model == "claude-sonnet-5-5"
    monkeypatch.setenv("RECALL_EFFORT", "low")
    assert get_settings().recall_effort == "low"


def test_typed_check_uses_recall_model_and_logs_under_recall(bank, monkeypatch):
    monkeypatch.setenv("MARKING_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("LIGHT_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("RECALL_MODEL", "claude-sonnet-5-5")
    add_cards(bank, [RAIN])
    client = FakeClient([_output(_rain_points("P1"))])
    revise.typed_check(bank, RAIN, ANSWER, client=client)
    assert client.requests[0]["model"] == "claude-sonnet-5-5"
    logged = bank.execute("SELECT purpose, question_id FROM api_calls").fetchone()
    assert tuple(logged) == ("recall", "card-rain")


def test_without_recall_model_typed_check_uses_the_marking_model(bank, monkeypatch):
    monkeypatch.setenv("MARKING_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("LIGHT_MODEL", "claude-haiku-4-5")
    monkeypatch.delenv("RECALL_MODEL", raising=False)
    add_cards(bank, [RAIN])
    client = FakeClient([_output(_rain_points("P1"))])
    revise.typed_check(bank, RAIN, ANSWER, client=client)
    assert client.requests[0]["model"] == "claude-opus-5-5"


# ---- Generated questions use the recall rubric; ingested questions keep Practice marking -----------------

GENERATED = {**ONE_MARK_QUESTION, "id": "gen-card-climate-q1", "source": "generated", "topic": "geo-intro",
             "marks": 2, "origin_card_id": "card-climate", "origin_card_version": 1,
             "mark_points": [{"id": "MP1", "text": "Average weather", "key_terms": ["average weather"]},
                             {"id": "MP2", "text": "Over a long period", "key_terms": []}]}


def test_an_ingested_question_still_uses_the_practice_prompt_and_a_generated_one_the_recall_rubric(bank):
    add_cards(bank, [CLIMATE])
    client = FakeClient([{"points": [{"id": "MP1", "awarded": True, "evidence_quote": "diffusion", "reason": "",
                                      "reversed": False}], "missing_key_terms": [], "filler_quotes": [],
                          "misread_question": False, "borderline": False, "borderline_reason": "",
                          "full_marks_answer": "", "technique_note": ""},
                         _output([("MP1", "covered", "average weather"), ("MP2", "missed", "")])])
    marker.mark_answer(bank, ONE_MARK_QUESTION, "It is diffusion.", client=client)
    marker.mark_answer(bank, GENERATED, "It is the average weather.", client=client)
    ingested, generated = client.requests
    assert len(ingested["system"]) == 1
    assert ingested["system"][0]["text"] == prompts.POINTS_SYSTEM.format(subject="Science")
    assert prompts.output_schema(ONE_MARK_QUESTION) is prompts.POINTS_SCHEMA
    assert "other_points" not in prompts.output_schema(ONE_MARK_QUESTION)["properties"]
    assert "checked_card_id" not in ingested["messages"][0]["content"][0]["text"]
    assert not any(b["text"].startswith("<revision_card>") for b in ingested["messages"][0]["content"])

    assert len(generated["system"]) == 2   # recall rubric + the topic's notes
    assert generated["system"][0]["text"] == prompts.RECALL_SYSTEM.format(subject="Science")
    assert prompts.output_schema(GENERATED) is prompts.RECALL_SCHEMA
    card = json.loads(_block(generated, "revision_card").split("\n", 1)[1].rsplit("\n", 1)[0])
    assert card["examples"] == ["Singapore is hot and wet all year."]   # the origin card, examples included
    assert '"checked_card_id": "card-climate"' in _block(generated, "mark_scheme")


def test_a_generated_question_is_scored_with_half_credit_rounded_down_and_no_box_logic(bank):
    add_cards(bank, [CLIMATE])
    out = _output([("MP1", "covered", "average weather"), ("MP2", "partial", "a while")])
    result = marker.mark_answer(bank, GENERATED, "It is the average weather over a while.", client=FakeClient([out]))
    assert result["detail"]["score"] == 1.5 and result["marks_awarded"] == 1
    assert bank.execute("SELECT COUNT(*) FROM recall_attempts").fetchone()[0] == 0
    assert tuple(bank.execute("SELECT purpose FROM api_calls").fetchone()) == ("recall",)


def test_a_generated_question_whose_card_is_gone_is_still_marked(bank):
    out = _output([("MP1", "covered", "average weather"), ("MP2", "missed", "")])
    client = FakeClient([out])
    result = marker.mark_answer(bank, GENERATED, "It is the average weather.", client=client)
    assert result["marks_awarded"] == 1
    assert not any(b["text"].startswith("<revision_card>") for b in client.requests[0]["messages"][0]["content"])


def test_notes_are_the_topics_approved_cards_in_book_order_and_nothing_else(bank):
    other = {**CLIMATE, "id": "card-other", "topic": "geo-weather"}
    add_cards(bank, [CLIMATE, other, RAIN])
    cards = notes.topic_notes(bank, "geo-intro")
    assert [n["card_id"] for n in cards] == ["card-climate", "card-rain"]
    assert cards[1]["points"][1] == "Water vapour cools and condenses into clouds. (This happens as the air rises.)"
    assert notes.render_notes(cards) == notes.render_notes(notes.topic_notes(bank, "geo-intro"))


# ---- Feedback lists for a generated question -----------------------------------------------------------

def test_generated_question_feedback_gets_the_same_lists():
    out = _output([("MP1", "covered", "Diffusion"), ("MP2", "missed", "")],
                  [(CLIMATE_SENTENCE, "in_notes", "card-climate"), ("The sea is salty", "unverified", ""),
                   ("clouds are made of droplets", "on_card", "")])
    result = rules.score(GENERATED, out, f"Diffusion. {CLIMATE_SENTENCE} The sea is salty. clouds are made of droplets.",
                         None, CARDS)
    view = feedback._points_view(result)
    assert [o["card_prompt"] for o in view["in_notes"]] == ["Define climate."]
    assert [o["quote"] for o in view["unverified"]] == ["The sea is salty"]
    assert [o["quote"] for o in view["on_card"]] == ["clouds are made of droplets"] and view["incorrect"] == []


# ---- Logging for the dashboard ------------------------------------------------------------------

def _attempt(conn, check_points, others, answer=ANSWER):
    result = _score(_output(check_points, others), answer)
    covered = [p["id"] for p in result["detail"]["points"] if p["awarded"]]
    attempt_id = revise.record_attempt(
        conn, RAIN, method="typed", covered_ids=covered, units_total=3, settings=settings(), typed_text=answer,
        result=result, points_covered=result["detail"]["score"], now=NOW)
    return attempt_id, conn.execute("SELECT * FROM recall_attempts WHERE id = ?", (attempt_id,)).fetchone()


def test_extras_do_not_help_the_box(conn):
    add_cards(conn, [CLIMATE, RAIN])
    _, row = _attempt(conn, _rain_points("P1"), [(CLIMATE_SENTENCE, "in_notes", "card-climate")])
    assert (row["points_covered"], row["box_after"]) == (1, 1)   # 33%: stays in box 1


def test_partial_points_can_lift_a_card(conn):
    add_cards(conn, [RAIN])
    _, row = _attempt(conn, _rain_points("P1", partial=("P2", "P3")))
    assert (row["points_covered"], row["box_after"]) == (2, 2)   # 1 + 0.5 + 0.5 = 67%


def test_classifications_are_logged_with_the_attempt_and_can_be_totalled(conn):
    add_cards(conn, [CLIMATE, RAIN])
    _attempt(conn, _rain_points("P1"), [(CLIMATE_SENTENCE, "in_notes", "card-climate"),
                                        ("The sea is salty", "unverified", "")],
             answer=f"{ANSWER} The sea is salty.")
    _attempt(conn, _rain_points("P1", "P2"), [("Rain is solid", "incorrect", "")], answer=f"{ANSWER} Rain is solid.")
    assert notes.classification_totals(conn, "2026-10-05T00:00:00Z", "2026-10-06T00:00:00Z") == {
        "on_card": 3, "in_notes": 1, "unverified": 1, "incorrect": 1}
    assert notes.classification_totals(conn, "2026-10-06T00:00:00Z", "2026-10-07T00:00:00Z")["on_card"] == 0
