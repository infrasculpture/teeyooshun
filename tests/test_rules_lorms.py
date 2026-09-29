"""LORMS-mode rules, using made-up model output (no API calls)."""

import pytest

from app.marking.rules import apply_lorms, count_rule, marks_from_count, rule_counts_supported_only

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


def test_borderline_minimum_is_1(lorms_question):
    r = apply_lorms(lorms_question, output([], 1, 1, borderline=True), ANSWER)
    assert r["marks_awarded"] == 1 and r["level_awarded"] == 1
    assert "borderline_lowered" not in r["flags"]


def test_borderline_never_raises_a_zero(lorms_question):
    r = apply_lorms(lorms_question, output([], 0, 0, borderline=True), ANSWER)
    assert r["marks_awarded"] == 0 and r["level_awarded"] == 0
