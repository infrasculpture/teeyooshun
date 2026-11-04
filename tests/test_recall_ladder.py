"""The recall ladder's code-only parts (app/recall_ladder.py): format by box and card length, blanks, word banks,
fill-the-gaps checking, starters. Made-up cards; no API."""

from app import app_settings, recall_ladder as ladder, revise
from revise_helpers import CLIMATE, COMPARE, FLOODS, MAP, RAIN, add_cards, make_card, point


def _unit(text, key_terms=()):
    return {"id": "P1", "text": text, "detail": [], "key_terms": list(key_terms), "any_of": False}


# ---- Format by box and card length --------------------------------------------------------------

def _format(card, box, **kw):
    return ladder.choose_format(card, box, revise.check_units(card), self_tick=revise.is_self_tick_only(card), **kw)


def test_format_follows_the_box():
    assert [_format(CLIMATE, b) for b in (1, 2, 3, 4, 5)] == \
        [ladder.WORDBANK, ladder.GAPS, ladder.STARTERS, ladder.FREE, ladder.FREE]


def test_long_cards_never_go_beyond_starters():
    assert len(revise.check_units(RAIN)) == 3
    assert _format(RAIN, 4, long_limit=4) == ladder.FREE
    assert _format(RAIN, 4, long_limit=2) == ladder.STARTERS and _format(RAIN, 5, long_limit=2) == ladder.STARTERS
    assert _format(RAIN, 1, long_limit=2) == ladder.WORDBANK   # the early boxes are unchanged


def test_low_energy_uses_only_the_easy_formats():
    assert _format(CLIMATE, 1, low_energy=True) == ladder.WORDBANK
    assert [_format(CLIMATE, b, low_energy=True) for b in (2, 3, 4, 5)] == [ladder.GAPS] * 4


def test_a_card_with_nothing_to_blank_uses_starters():
    assert _format(FLOODS, 1) == ladder.STARTERS and _format(FLOODS, 2) == ladder.STARTERS
    assert _format(FLOODS, 1, low_energy=True) == ladder.STARTERS


def test_image_cards_stay_self_tick():
    assert _format(MAP, 1) == ladder.PAPER and _format(MAP, 5) == ladder.PAPER


# ---- Blanks ----------------------------------------------------------------------------------------

def test_blanks_come_from_the_highlighted_terms():
    blanked = ladder.blank_unit(revise.check_units(RAIN)[1])
    assert blanked["segments"] == [{"text": "Water vapour cools and "}, {"blank": 0}, {"text": " into clouds."}]
    assert blanked["answers"] == ["condenses"]


def test_blank_keeps_the_cards_own_spelling_and_blanks_a_term_once():
    blanked = ladder.blank_unit(_unit("Water Evaporates, then water evaporates again.", ["evaporates"]))
    assert blanked["answers"] == ["Evaporates"]


def test_at_most_three_blanks_in_text_order():
    blanked = ladder.blank_unit(_unit("Red, green, blue and yellow are colours.", ["blue", "red", "green", "yellow"]))
    assert blanked["answers"] == ["Red", "green", "blue"]


def test_without_a_usable_term_the_longest_content_word_is_blanked():
    assert ladder.blank_unit(_unit("Plants take in carbon dioxide for photosynthesis."))["answers"] == ["photosynthesis"]
    # a highlighted term that isn't in the text is no use: same fallback
    assert ladder.blank_unit(_unit("Plants make sugar by photosynthesis in leaves.", ["chlorophyll"]))["answers"] == \
        ["photosynthesis"]


def test_fallback_phrase_joins_neighbouring_content_words_only():
    assert ladder.blank_unit(_unit("The red blood cells carry oxygen."))["answers"] == ["cells carry oxygen"]


def test_fallback_never_blanks_the_first_word():
    assert ladder.blank_unit(_unit("Photosynthesis happens in leaves."))["answers"] == ["happens"]


def test_short_points_blank_their_last_content_word_and_one_word_points_are_not_blanked():
    assert ladder.blank_unit(_unit("Heat flows outward"))["answers"] == ["outward"]
    assert ladder.blank_unit(_unit("Damage")) is None
    assert ladder.blank_unit(_unit("")) is None


def test_table_row_without_terms_still_gets_a_blank():
    assert ladder.blank_unit(revise.check_units(COMPARE)[1])["answers"] == ["averages"]


def test_filled_text_gives_back_the_cards_wording():
    for unit in revise.check_units(RAIN):
        assert ladder.filled_text(unit, ladder.blank_unit(unit)) == unit["text"]


# ---- Word bank -------------------------------------------------------------------------------------

POOL = ["evaporates", "condenses", "clouds", "droplets", "precipitation", "humid"]


def test_bank_has_the_answers_once_plus_distractors_from_the_pool():
    unit = revise.check_units(RAIN)[1]
    bank = ladder.build_bank(["condenses"], POOL, unit["text"], seed=7)
    assert bank.count("condenses") == 1 and len(bank) == 4
    assert set(bank) - {"condenses"} <= {"evaporates", "droplets", "precipitation", "humid"}   # not "clouds": in the text


def test_bank_never_offers_a_word_that_contains_or_hides_inside_an_answer():
    bank = ladder.build_bank(["condenses"], ["condense", "dense", "condensesss", "humid"], "x", seed=1)
    assert sorted(bank) == ["condenses", "humid"]


def test_bank_is_the_same_on_reload_and_has_no_pool_means_just_the_answers():
    one = ladder.build_bank(["a thing", "b thing"], POOL, "text", ladder.stable_seed(1, "card", "P1"))
    assert one == ladder.build_bank(["a thing", "b thing"], POOL, "text", ladder.stable_seed(1, "card", "P1"))
    assert sorted(ladder.build_bank(["x", "y"], [], "text", 3)) == ["x", "y"]


def test_distractors_are_drawn_from_other_cards_in_the_same_topic(conn):
    add_cards(conn, [CLIMATE, RAIN, COMPARE, FLOODS, make_card("card-other", "Define x.", {
        "format": "bullets", "points": [point("Other topic word.", ["zebra"])]}, topic="geo-other")])
    pool = ladder.distractor_pool(conn, CLIMATE)
    assert set(pool) == {"long term", "evaporates", "condenses", "droplets"}
    assert "average weather" not in pool and "zebra" not in pool


# ---- Fill the gaps: checking -------------------------------------------------------------------------

def test_case_punctuation_and_hyphens_do_not_matter():
    assert ladder.answer_matches("CONDENSES.", "condenses")
    assert ladder.answer_matches("Low-lying", "low lying") and ladder.answer_matches("watervapour", "water vapour")


def test_a_small_slip_in_a_long_word_is_accepted():
    assert ladder.answer_matches("condenes", "condenses")        # a letter missing
    assert ladder.answer_matches("condneses", "condenses")       # two letters swapped
    assert ladder.answer_matches("water vapor", "water vapour")  # word by word


def test_a_wrong_word_or_a_slip_in_a_short_word_is_rejected():
    assert not ladder.answer_matches("evaporates", "condenses")
    assert not ladder.answer_matches("cut", "cat") and not ladder.answer_matches("ion", "ions")
    assert not ladder.answer_matches("", "condenses")
    assert not ladder.answer_matches("condneses", "condenses", tolerant=False)   # the word bank is exact


def test_listed_alternatives_are_accepted():
    assert ladder.answer_matches("steam", "water vapour", ["steam"])
    blanked = ladder.blank_unit({**_unit("Water vapour cools.", ["water vapour"]), "alternatives": {"Water Vapour": ["steam"]}})
    assert blanked["alternatives"] == [["steam"]]
    assert ladder.score_blanks(["Steam"], blanked)["score"] == 1.0


def test_blank_scores_are_whole_half_or_none():
    blanked = ladder.blank_unit(_unit("Plants absorb carbon dioxide during photosynthesis.",
                                      ["carbon dioxide", "photosynthesis"]))
    assert ladder.score_blanks(["carbon dioxide", "photosynthesis"], blanked) == {"results": [True, True], "score": 1.0}
    assert ladder.score_blanks(["carbon dioxide", "wrong"], blanked) == {"results": [True, False], "score": 0.5}
    assert ladder.score_blanks(["no", "wrong"], blanked)["score"] == 0.0
    assert ladder.score_blanks([], blanked)["score"] == 0.0
    three = ladder.blank_unit(_unit("Red, green and blue.", ["red", "green", "blue"]))
    assert ladder.score_blanks(["red", "green", "x"], three)["score"] == 0.5
    assert ladder.score_blanks(["red", "x", "x"], three)["score"] == 0.0
    one = ladder.blank_unit(_unit("Water evaporates from the sea.", ["evaporates"]))
    assert ladder.score_blanks(["evaporates"], one)["score"] == 1.0 and ladder.score_blanks(["x"], one)["score"] == 0.0


# ---- Starters and "Give me a start" -------------------------------------------------------------------

def test_starter_is_the_words_up_to_the_first_term_between_three_and_six():
    p1, p2, p3 = revise.check_units(RAIN)
    assert ladder.starter_split(p2) == ("Water vapour cools and", "condenses into clouds.")
    assert ladder.starter_split(p1) == ("Water evaporates from", "the sea.")     # term too early: at least 3 words
    assert ladder.starter_split(p3) == ("Droplets join and", "fall as rain.")
    assert ladder.starter_split(_unit("One two three four five six seven eight.")) == \
        ("One two three four five", "six seven eight.")
    assert ladder.starter_split(_unit("Damage")) == ("", "Damage")


def test_a_start_reveals_a_few_more_words_but_never_the_whole_point():
    p2 = revise.check_units(RAIN)[1]
    assert ladder.reveal_start(p2, 4) == "Water vapour cools and condenses into"
    assert ladder.reveal_start(p2, 0) == "Water vapour cools and"
    assert ladder.reveal_start(_unit("Short point here"), 3) == "Short point"


def test_a_start_caps_the_point_at_half():
    assert ladder.cap_score(1.0, True) == 0.5 and ladder.cap_score(0.5, True) == 0.5 and ladder.cap_score(0.0, True) == 0.0
    assert ladder.cap_score(1.0, False) == 1.0


# ---- Settings and schema ---------------------------------------------------------------------------

def test_new_settings_have_defaults_and_are_checked():
    d = app_settings.DEFAULTS
    assert (d["low_energy_enabled"], d["stuck_threshold"], d["long_card_points"]) == (True, 4, 4)
    assert app_settings.validate(dict(d)) == []
    assert app_settings.validate({**d, "stuck_threshold": 1}) and app_settings.validate({**d, "long_card_points": 13})
    assert app_settings.validate({**d, "low_energy_enabled": "yes"})


def test_settings_form_reads_the_new_fields():
    values = app_settings.from_form({"stuck_threshold": "6", "long_card_points": "5", "low_energy_enabled": "on"})
    assert (values["stuck_threshold"], values["long_card_points"], values["low_energy_enabled"]) == (6, 5, True)
    assert app_settings.from_form({})["stuck_threshold"] == 4 and app_settings.from_form({})["low_energy_enabled"] is False


def test_schema_v23_adds_the_new_columns_and_tables(conn):
    cols = lambda t: {r["name"] for r in conn.execute(f"PRAGMA table_info({t})")}   # noqa: E731
    assert {"consecutive_fails", "stuck", "retired"} <= cols("card_progress")
    assert {"format", "help_starts", "low_energy"} <= cols("recall_attempts")
    assert "low_energy" in cols("revise_sets") and "low_energy" in cols("sessions")
    assert {"set_id", "card_id", "slot", "score", "start_used"} <= cols("recall_points")
    assert {"set_id", "card_id", "format"} <= cols("recall_work")
