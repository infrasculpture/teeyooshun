"""Confidence: base by source, one step down if any drop flag fired, Low is the floor."""

import pytest

from app.marking.rules import confidence


@pytest.mark.parametrize("source, expected", [
    ("assessment_book", "high"),
    ("school", "medium"),
    ("publisher_paper", "medium"),
    ("generated", "low"),
])
def test_base_by_source(source, expected):
    assert confidence(source, [])[0] == expected


@pytest.mark.parametrize("flag", ["borderline", "evidence_mismatch", "clamped", "level_lowered", "borderline_lowered"])
def test_each_drop_flag_drops_one_step(flag):
    assert confidence("assessment_book", [flag])[0] == "medium"


def test_lorms_marking_alone_does_not_drop(lorms_question):
    from app.marking.rules import score
    output = {"features": [{"claim": "The market was disorderly", "supported": True,
                            "support_quote": "notice lists many new rules", "explained": True}],
              "describes_without_inference": False, "proposed_level": 3, "proposed_marks": 4,
              "descriptor_matched": "", "why_not_higher": "", "borderline": False, "borderline_reason": "",
              "peel_map": {"point": "", "evidence": "", "explain": "", "link": ""},
              "full_marks_answer": "", "technique_note": ""}
    answer = "The market was disorderly. The notice lists many new rules."
    result = score(lorms_question, output, answer)
    assert result["flags"] == []
    assert result["confidence"] == "medium"  # publisher paper base, no drop


def test_several_causes_still_drop_only_one_step():
    label, reason = confidence("assessment_book", ["borderline", "borderline_lowered", "evidence_mismatch", "clamped"])
    assert label == "medium"
    assert "borderline_lowered" in reason and "evidence_mismatch" in reason
    assert "lorms" not in reason


def test_low_is_the_floor():
    assert confidence("generated", ["borderline", "borderline_lowered"])[0] == "low"


def test_informational_flags_do_not_drop():
    assert confidence("assessment_book", ["reversed", "requires_unmet", "recounted"])[0] == "high"
