"""Confidence: base by source, one step down if any drop condition applies, Low is the floor."""

import pytest

from app.marking.rules import confidence


@pytest.mark.parametrize("source, expected", [
    ("assessment_book", "high"),
    ("school", "medium"),
    ("publisher_paper", "medium"),
    ("generated", "low"),
])
def test_base_by_source(source, expected):
    assert confidence(source, "points", [])[0] == expected


@pytest.mark.parametrize("flag", ["borderline", "evidence_mismatch", "clamped", "level_lowered"])
def test_each_drop_flag_drops_one_step(flag):
    assert confidence("assessment_book", "points", [flag])[0] == "medium"


def test_lorms_alone_drops_one_step():
    assert confidence("assessment_book", "lorms", [])[0] == "medium"


def test_several_causes_still_drop_only_one_step():
    label, reason = confidence("assessment_book", "lorms", ["borderline", "evidence_mismatch", "clamped"])
    assert label == "medium"
    assert "borderline" in reason and "evidence_mismatch" in reason and "lorms" in reason


def test_low_is_the_floor():
    assert confidence("generated", "lorms", ["borderline"])[0] == "low"


def test_informational_flags_do_not_drop():
    assert confidence("assessment_book", "points", ["reversed", "requires_unmet", "recounted"])[0] == "high"
