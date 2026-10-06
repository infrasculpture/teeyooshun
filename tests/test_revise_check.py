"""Revise mode: check units, self-tick, typed checks through the points engine, command-word
notes and the underline step. Made-up cards; the API is always faked."""

import pytest

from app import revise
from ingest_helpers import FakeClient
from revise_helpers import CLIMATE, COMPARE, EVENTS, FLOODS, MAP, RAIN, add_cards, make_card, point


# ---- Check units (spec section 4) ------------------------------------------------------

def test_bullets_and_list_units_are_the_top_level_points():
    units = revise.check_units(RAIN)
    assert [u["id"] for u in units] == ["P1", "P2", "P3"]
    assert units[1]["text"] == "Water vapour cools and condenses into clouds."
    assert units[1]["detail"] == ["This happens as the air rises."] and units[1]["key_terms"] == ["condenses"]


def test_grouped_units_are_the_group_headings():
    units = revise.check_units(FLOODS)
    assert [u["text"] for u in units] == ["Damage", "Health"] and all(u["any_of"] for u in units)
    assert units[0]["detail"] == ["Homes are ruined.", "Roads are washed away."]


def test_table_units_are_the_rows_and_timeline_units_are_the_events():
    rows = revise.check_units(COMPARE)
    assert [u["text"] for u in rows] == ["Time; Weather: short term; Climate: long term",
                                         "Described by; Weather: today; Climate: averages"]
    assert rows[0]["key_terms"] == ["long term"] and rows[1]["key_terms"] == []
    assert [u["text"] for u in revise.check_units(EVENTS)] == ["1300: A settlement begins.", "1350: A wall is built."]


def test_image_card_is_one_unit_and_self_tick_only():
    assert len(revise.check_units(MAP)) == 1 and revise.is_self_tick_only(MAP)
    assert not revise.is_self_tick_only(RAIN)


# ---- Self-tick -------------------------------------------------------------------

def test_self_tick_counts_only_real_units_once():
    units = revise.check_units(RAIN)
    assert revise.tick_check(units, ["P1", "P3", "P3", "P9", "nonsense"]) == ["P1", "P3"]
    assert revise.tick_check(units, None) == []


# ---- Typed check: the points engine with the light model ----------------------------------------

def test_card_becomes_a_points_question_with_one_mark_per_unit():
    question = revise.card_as_question(RAIN, revise.check_units(RAIN))
    assert question["marking_mode"] == "points" and question["marks"] == 3
    assert question["question"] == "Explain how rain forms." and question["command_word"] == "explain"
    assert question["mark_points"][0] == {"id": "P1", "text": "Water evaporates from the sea.", "key_terms": ["evaporates"]}
    assert question["mark_points"][1]["text"].endswith("(This happens as the air rises.)")
    grouped = revise.card_as_question(FLOODS, revise.check_units(FLOODS))
    assert grouped["mark_points"][0]["alternatives"] == ["Homes are ruined.", "Roads are washed away."]
    assert "any one of the points listed as alternatives" in grouped["mark_points"][0]["text"]


def _marking(*points):
    return {"points": [{"id": i, "awarded": a, "evidence_quote": q, "reason": "", "reversed": False} for i, a, q in points],
            "missing_key_terms": ["droplets"], "filler_quotes": [], "misread_question": False, "borderline": False,
            "borderline_reason": "", "full_marks_answer": "", "technique_note": ""}


def test_typed_check_uses_the_points_engine_with_the_light_model(bank, monkeypatch):
    monkeypatch.setenv("LIGHT_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("MARKING_MODEL", "claude-sonnet-5-5")
    add_cards(bank, [RAIN])
    answer = "Water evaporates from the sea and condenses into clouds."
    client = FakeClient([_marking(("P1", True, "evaporates from the sea"), ("P2", True, "condenses into clouds"),
                                  ("P3", True, "falls as rain"))])  # P3's quote is not in her answer
    check = revise.typed_check(bank, RAIN, answer, client=client)
    assert check["covered_ids"] == ["P1", "P2"]  # the verbatim-quote rule refuses P3
    assert check["key_terms_missed"] == ["droplets"]
    assert "evidence_mismatch" in check["result"]["flags"]
    request = client.requests[0]
    assert request["model"] == "claude-haiku-4-5"  # a 3-mark question would otherwise use the marking model
    assert "<mark_scheme>" in request["messages"][0]["content"][0]["text"]
    logged = bank.execute("SELECT purpose, question_id, input_tokens FROM api_calls").fetchone()
    assert tuple(logged) == ("recall", "card-rain", 2000)


def test_failed_typed_check_raises_and_saves_nothing(bank):
    add_cards(bank, [RAIN])
    with pytest.raises(revise.CheckFailed):
        revise.typed_check(bank, RAIN, "Water evaporates.", client=FakeClient(["TRUNCATED"]))
    assert bank.execute("SELECT COUNT(*) FROM recall_attempts").fetchone()[0] == 0


# ---- Command-word notes: fixed text, only when relevant --------------------------------------

@pytest.mark.parametrize("command, text, units, shown", [
    ("define", "Climate is the average weather over a long time.", 1, False),
    ("define", "Climate is weather. It is over a long time. It is an average.", 1, True),
    ("explain", "Water evaporates and then it rains.", 3, True),
    ("explain", "Water evaporates because the sun heats the sea, so clouds form.", 3, False),
    ("state", "Evaporation.", 1, False),
    ("state", " ".join(["word"] * 20), 1, True),
    ("describe", "Homes ruined.", 2, True),
    ("describe", "Homes are ruined and roads are washed away by the water.", 2, False),
    ("compare", "Weather is short term. Climate is long term.", 2, True),
    ("compare", "Weather is short term whereas climate is long term.", 2, False),
    ("locate", "Anything at all.", 1, False),
    ("", "Anything at all.", 1, False),
])
def test_command_note_appears_only_when_relevant(command, text, units, shown):
    assert bool(revise.command_note(command, text, units)) is shown


def test_command_note_text():
    assert revise.command_note("define", "A. B. C.", 1) == "Define: one precise sentence is enough."
    assert revise.command_note("Explain", "It rains.", 2).startswith("Explain: give the reason")


# ---- Underline step ------------------------------------------------------------------

def test_underline_compares_tapped_words_with_key_terms():
    # RAIN lines: 0 "Water evaporates from the sea."  1 "Water vapour cools and condenses into clouds."
    #             2 "This happens as the air rises." (sub-point)  3 "Droplets join and fall as rain."
    result = revise.underline_result(RAIN, ["0-1", "0-4", "3-0", "2-0"])
    assert result == {"hits": ["evaporates", "droplets"], "misses": ["condenses"], "extras": ["sea", "This"]}


def test_a_key_term_of_several_words_is_spotted_by_any_of_its_words():
    assert revise.underline_result(CLIMATE, ["0-4"])["hits"] == ["average weather"]   # tapped "weather"
    assert revise.underline_result(CLIMATE, [])["misses"] == ["average weather"]
    assert revise.underline_words(CLIMATE)[0][3:5] == ["average", "weather"]


# ---- Split cards ---------------------------------------------------------------------

def test_split_cards_know_their_siblings(conn):
    parts = [make_card(f"card-causes{letter}", f"Explain the causes. ({name})",
                       {"format": "bullets", "points": [point("x")]}, split_from="Explain the causes.")
             for letter, name in (("a", "Natural"), ("b", "Human"))]
    add_cards(conn, parts + [CLIMATE])
    assert revise.siblings(conn, parts[1]) == (2, 2, ["card-causesa", "card-causesb"])
    assert revise.siblings(conn, CLIMATE) is None
