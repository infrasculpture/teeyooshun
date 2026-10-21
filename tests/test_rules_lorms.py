"""LORMS-mode rules, using made-up model output (no API calls)."""

import pytest

from app.marking.rules import apply_lorms, claim_in_answer, confidence, count_rule, marks_from_count, rule_counts_supported_only

ANSWER = (
    "The market was disorderly. The notice lists many new rules, which suggests there were problems. "
    "The market was also busy."
)
UNSUPPORTED_RULE = "2 marks for 1 inference unsupported; 3 marks for 2 inferences unsupported"
SUPPORTED_RULE = "4 marks for 1 supported inference, 5 marks for 2"


def feature(claim, supported=False, support=""):
    return {"claim": claim, "supported": supported, "support_quote": support, "explained": True}


def output(features, level, marks, **extra):
    base = {"features": features, "describes_without_inference": False,
            "proposed_level": level, "proposed_marks": marks, "descriptor_matched": "",
            "why_not_higher": "", "borderline": False, "borderline_reason": "",
            "peel_map": {"point": "", "evidence": "", "explain": "", "link": "MISSING"},
            "full_marks_answer": "", "technique_note": ""}
    base.update(extra)
    return base


def text_levels(question):
    """The fixture question with every level using award_rules text only (no count field)."""
    for lv in question["lorms"]:
        lv.pop("count", None)
    question["lorms"][2]["award_rules"] = SUPPORTED_RULE
    return question


SUPPORTED_ONE = feature("The market was disorderly", True, "notice lists many new rules")
UNSUPPORTED_ONE = feature("The market was also busy")


# ---- reading the rule text ----

@pytest.mark.parametrize("text, expected", [
    (SUPPORTED_RULE, True),
    (UNSUPPORTED_RULE, False),
    ("Inference, unsupported", False),
    ("Inference, un-supported", False),
    ("Inference, un supported", False),
    ("Inference not supported by the source", False),
    ("Inference, supported by the source", True),
    ("2 marks for 1 inference, 3 marks for 2", False),
])
def test_supported_word_detection(text, expected):
    assert rule_counts_supported_only(text) is expected


def test_unsupported_rule_text_counts_all_features():
    basis, table = count_rule({"award_rules": UNSUPPORTED_RULE})
    assert basis == "any"
    assert table == {1: 2, 2: 3}


def test_supported_rule_text_counts_supported_only():
    basis, table = count_rule({"award_rules": SUPPORTED_RULE})
    assert basis == "supported"
    assert table == {1: 4, 2: 5}


def test_uncountable_rule_text():
    assert count_rule({"award_rules": "E.g. 'There are many ships.'"}) is None


def test_structured_count_used_instead_of_text():
    level = {"award_rules": "4 marks for 1 supported inference", "count": {"basis": "any", "marks_by_count": {"1": 2, "2": 3}}}
    assert count_rule(level) == ("any", {1: 2, 2: 3})


def test_marks_from_count_caps_at_largest_entry():
    assert marks_from_count({1: 4, 2: 5}, 3) == 5
    assert marks_from_count({1: 4, 2: 5}, 0) is None


# ---- text path through apply_lorms ----

def test_text_path_level2_counts_unsupported_features(lorms_question):
    q = text_levels(lorms_question)
    r = apply_lorms(q, output([SUPPORTED_ONE, UNSUPPORTED_ONE], 2, 3), ANSWER)
    assert r["level_awarded"] == 2
    assert r["marks_awarded"] == 3  # two features counted, one of them unsupported


def test_text_path_level3_counts_supported_only(lorms_question):
    q = text_levels(lorms_question)
    r = apply_lorms(q, output([SUPPORTED_ONE, UNSUPPORTED_ONE], 3, 5), ANSWER)
    assert r["level_awarded"] == 3
    assert r["marks_awarded"] == 4  # only one supported inference -> 4, lower than proposed 5
    assert "recounted" in r["flags"]


# ---- structured path through apply_lorms ----

def test_structured_supported_basis(lorms_question):
    r = apply_lorms(lorms_question, output([SUPPORTED_ONE, UNSUPPORTED_ONE], 3, 5), ANSWER)
    assert r["marks_awarded"] == 4


def test_structured_any_basis(lorms_question):
    lorms_question["lorms"][1]["count"] = {"basis": "any", "marks_by_count": {"1": 2, "2": 3}}
    r = apply_lorms(lorms_question, output([SUPPORTED_ONE, UNSUPPORTED_ONE], 2, 3), ANSWER)
    assert r["marks_awarded"] == 3


def test_structured_field_wins_over_contradicting_text(lorms_question):
    # Text says supported only; the structured field says any feature counts.
    lorms_question["lorms"][2]["award_rules"] = SUPPORTED_RULE
    lorms_question["lorms"][2]["count"] = {"basis": "any", "marks_by_count": {"1": 4, "2": 5}}
    r = apply_lorms(lorms_question, output([SUPPORTED_ONE, UNSUPPORTED_ONE], 3, 5), ANSWER)
    assert r["marks_awarded"] == 5


# ---- quotes, clamping and lowering ----

def test_invented_support_quote_makes_feature_unsupported(lorms_question):
    fake = feature("The market was disorderly", True, "soldiers guarded the gates")
    r = apply_lorms(lorms_question, output([fake], 3, 4), ANSWER)
    assert "evidence_mismatch" in r["flags"]
    assert "level_lowered" in r["flags"]  # no supported inference left for level 3
    assert r["level_awarded"] == 2 and r["marks_awarded"] == 2


def test_support_identical_to_claim_is_unsupported(lorms_question):
    same = feature("The market was disorderly", True, "the  MARKET was disorderly.")
    r = apply_lorms(lorms_question, output([same], 3, 4), ANSWER)
    assert r["detail"]["features"][0]["supported"] is False
    assert "self_supported" in r["flags"]
    assert "evidence_mismatch" not in r["flags"]  # the quote is real, it just isn't support
    assert r["level_awarded"] == 2 and r["marks_awarded"] == 2  # no supported inference left for L3


def test_support_contained_in_claim_is_unsupported(lorms_question):
    part = feature("The market was disorderly", True, "disorderly")
    r = apply_lorms(lorms_question, output([part], 3, 4), ANSWER)
    assert r["detail"]["features"][0]["supported"] is False
    assert "self_supported" in r["flags"]


def test_invented_claim_is_dropped(lorms_question):
    r = apply_lorms(lorms_question, output([feature("The market was closed")], 2, 2), ANSWER)
    assert r["detail"]["dropped_features"]
    assert "evidence_mismatch" in r["flags"]


def test_marks_outside_level_range_are_clamped(lorms_question):
    r = apply_lorms(lorms_question, output([SUPPORTED_ONE], 3, 7), ANSWER)
    assert "clamped" in r["flags"]
    assert r["marks_awarded"] == 4


def test_level_lowered_to_uncountable_level_uses_its_minimum(lorms_question):
    r = apply_lorms(lorms_question, output([], 2, 2), ANSWER)  # level 2 but no inferences at all
    assert "level_lowered" in r["flags"]
    assert r["level_awarded"] == 1 and r["marks_awarded"] == 1


def test_level_zero_gives_zero(lorms_question):
    r = apply_lorms(lorms_question, output([], 0, 0), ANSWER)
    assert r["level_awarded"] == 0 and r["marks_awarded"] == 0
    assert "clamped" not in r["flags"]


# ---- borderline: one mark less than proposed, level from the ranges ----

SUPPORTED_TWO = feature("there were problems", True, "notice lists many new rules")


def test_borderline_proposed_5_gives_4_at_level_3(lorms_question):
    r = apply_lorms(lorms_question, output([SUPPORTED_ONE, SUPPORTED_TWO], 3, 5, borderline=True), ANSWER)
    assert r["marks_awarded"] == 4
    assert r["level_awarded"] == 3
    assert "borderline_lowered" in r["flags"] and "borderline" in r["flags"]


def test_borderline_proposed_4_gives_3_at_level_2(lorms_question):
    r = apply_lorms(lorms_question, output([SUPPORTED_ONE], 3, 4, borderline=True), ANSWER)
    assert r["marks_awarded"] == 3
    assert r["level_awarded"] == 2
    assert "borderline_lowered" in r["flags"]


def test_borderline_not_lowered_twice_after_recount(lorms_question):
    # Proposed 5, but only one supported inference: the recount already gives 4 (= 5 - 1).
    r = apply_lorms(lorms_question, output([SUPPORTED_ONE], 3, 5, borderline=True), ANSWER)
    assert r["marks_awarded"] == 4 and r["level_awarded"] == 3
    assert "recounted" in r["flags"]
    assert "borderline_lowered" not in r["flags"]


# ---- claims verify by key words in one sentence; support stays verbatim ----

COMPOUND = "The notice will protect girls in the brothels and also stop abuse of the coolie workers. It was strict."


def test_claim_verifies_by_key_words_in_one_sentence():
    assert claim_in_answer("protect girls from brothels and stop coolie abuse", COMPOUND)
    assert claim_in_answer("The notice will protect girls", COMPOUND)  # an exact phrase still works


def test_claim_key_words_must_share_a_sentence():
    split = "The rules protect girls. Brothels were closed. Coolie abuse stopped."
    assert not claim_in_answer("protect girls from brothels and stop coolie abuse", split)


def test_claim_with_a_missing_key_word_or_one_key_word_fails():
    assert not claim_in_answer("protect girls from brothels and stop opium trade", COMPOUND)
    assert not claim_in_answer("the coolie of", COMPOUND)  # one key word, not as written: not enough to go on
    assert not claim_in_answer("", COMPOUND)


def test_reworded_claim_is_kept_but_support_must_still_be_verbatim(lorms_question):
    claim = "protect girls from brothels and stop coolie abuse"
    ok = feature(claim, True, "protect girls in the brothels")
    r = apply_lorms(lorms_question, output([ok], 3, 4), COMPOUND)
    assert r["marks_awarded"] == 4 and not r["detail"]["dropped_features"]
    assert "evidence_mismatch" not in r["flags"]

    reworded_support = feature(claim, True, "protect girls from brothels")  # not word-for-word in the answer
    r = apply_lorms(lorms_question, output([reworded_support], 3, 4), COMPOUND)
    assert "evidence_mismatch" in r["flags"]
    assert r["detail"]["features"][0]["supported"] is False


# ---- a failed check lowers the award by one when the level can't be recounted ----

def _uncountable_level_2(question):
    question["lorms"][1]["award_rules"] = "Any valid inference, unsupported"
    return question


def test_failed_claim_lowers_one_mark_on_an_uncountable_level(lorms_question):
    q = _uncountable_level_2(lorms_question)
    fake = feature("Aliens attacked the market")  # not in the answer
    r = apply_lorms(q, output([SUPPORTED_ONE, fake], 2, 3), ANSWER)
    assert r["marks_awarded"] == 2 and r["level_awarded"] == 2
    assert "evidence_mismatch" in r["flags"] and "evidence_lowered" in r["flags"]


def test_failed_support_also_lowers_one_mark(lorms_question):
    q = _uncountable_level_2(lorms_question)
    bad_support = feature("The market was disorderly", True, "a quote that is not there")
    r = apply_lorms(q, output([bad_support], 2, 3), ANSWER)
    assert r["marks_awarded"] == 2 and "evidence_lowered" in r["flags"]


def test_evidence_lowering_never_goes_below_1(lorms_question):
    r = apply_lorms(lorms_question, output([feature("Aliens attacked")], 1, 1), ANSWER)  # level 1: "E.g." only
    assert r["marks_awarded"] == 1 and "evidence_lowered" not in r["flags"]


def test_no_evidence_lowering_when_everything_verifies(lorms_question):
    q = _uncountable_level_2(lorms_question)
    r = apply_lorms(q, output([SUPPORTED_ONE], 2, 3), ANSWER)
    assert r["marks_awarded"] == 3 and "evidence_lowered" not in r["flags"]


def test_countable_level_recounts_instead_of_lowering(lorms_question):
    fake = feature("Aliens attacked the market", True, "notice lists many new rules")
    r = apply_lorms(lorms_question, output([SUPPORTED_ONE, fake], 3, 5), ANSWER)
    assert r["marks_awarded"] == 4 and "recounted" in r["flags"]
    assert "evidence_lowered" not in r["flags"]


def test_evidence_lowering_is_not_repeated_by_borderline(lorms_question):
    q = _uncountable_level_2(lorms_question)
    fake = feature("Aliens attacked the market")
    r = apply_lorms(q, output([SUPPORTED_ONE, fake], 2, 3, borderline=True), ANSWER)
    assert r["marks_awarded"] == 2 and "borderline_lowered" not in r["flags"]


def test_borderline_torn_up_keeps_the_proposal(lorms_question):
    # Proposed 4, torn with 5: the lower is 4 = the proposal, so nothing is lowered.
    r = apply_lorms(lorms_question, output([SUPPORTED_ONE], 3, 4, borderline=True, borderline_other_marks=5), ANSWER)
    assert r["marks_awarded"] == 4 and r["level_awarded"] == 3
    assert "borderline" in r["flags"] and "borderline_lowered" not in r["flags"]


def test_borderline_torn_down_gives_the_other_mark(lorms_question):
    # Proposed 5, torn with 4: awards 4, still level 3.
    r = apply_lorms(lorms_question, output([SUPPORTED_ONE, SUPPORTED_TWO], 3, 5, borderline=True,
                                           borderline_other_marks=4), ANSWER)
    assert r["marks_awarded"] == 4 and r["level_awarded"] == 3
    assert "borderline_lowered" in r["flags"] and "borderline" in r["flags"]


def test_borderline_across_levels_follows_the_ranges(lorms_question):
    # Proposed 4 (level 3), torn with 3 (level 2): awards 3 and the level follows.
    r = apply_lorms(lorms_question, output([SUPPORTED_ONE], 3, 4, borderline=True, borderline_other_marks=3), ANSWER)
    assert r["marks_awarded"] == 3 and r["level_awarded"] == 2
    assert "borderline_lowered" in r["flags"]


def test_borderline_without_the_field_falls_back_to_one_less(lorms_question):
    r = apply_lorms(lorms_question, output([SUPPORTED_ONE], 3, 4, borderline=True), ANSWER)
    assert r["marks_awarded"] == 3 and r["level_awarded"] == 2
    assert "borderline_lowered" in r["flags"]


def test_borderline_other_marks_is_ignored_when_not_borderline(lorms_question):
    r = apply_lorms(lorms_question, output([SUPPORTED_ONE], 3, 4, borderline_other_marks=2), ANSWER)
    assert r["marks_awarded"] == 4 and "borderline" not in r["flags"]


def test_borderline_still_lowers_confidence(lorms_question):
    r = apply_lorms(lorms_question, output([SUPPORTED_ONE], 3, 4, borderline=True, borderline_other_marks=5), ANSWER)
    assert confidence("assessment_book", r["flags"])[0] == "medium"


def test_borderline_minimum_is_1(lorms_question):
    r = apply_lorms(lorms_question, output([], 1, 1, borderline=True), ANSWER)
    assert r["marks_awarded"] == 1 and r["level_awarded"] == 1
    assert "borderline_lowered" not in r["flags"]


def test_borderline_never_raises_a_zero(lorms_question):
    r = apply_lorms(lorms_question, output([], 0, 0, borderline=True), ANSWER)
    assert r["marks_awarded"] == 0 and r["level_awarded"] == 0
