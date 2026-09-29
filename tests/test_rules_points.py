"""Points-mode rules, using made-up model output (no API calls)."""

from app.marking.rules import apply_points, quote_in_answer, score

ANSWER = "The balloon expands because the particles gain energy and move further apart."


def point(pid, awarded, quote="", reversed_=False):
    return {"id": pid, "awarded": awarded, "evidence_quote": quote, "reason": "test", "reversed": reversed_}


def output(*points, **extra):
    base = {"points": list(points), "missing_key_terms": [], "filler_quotes": [],
            "misread_question": False, "borderline": False, "borderline_reason": "",
            "full_marks_answer": "", "technique_note": ""}
    base.update(extra)
    return base


# ---- quote check ----

def test_quote_ignores_case_whitespace_and_edge_punctuation():
    assert quote_in_answer("  THE BALLOON   expands.", ANSWER)


def test_quote_straightens_curly_quotes():
    assert quote_in_answer("it’s hot", "I think it's hot today")


def test_quote_not_in_answer_fails():
    assert not quote_in_answer("the balloon shrinks", ANSWER)


def test_empty_quote_never_matches():
    assert not quote_in_answer("", ANSWER)
    assert not quote_in_answer(" . ", ANSWER)


# ---- awarding ----

def test_full_marks_with_real_quotes(points_question):
    r = apply_points(points_question, output(
        point("MP1", True, "balloon expands"),
        point("MP2", True, "particles gain energy and move further apart"),
    ), ANSWER)
    assert r["marks_awarded"] == 2
    assert r["valid_points"] == 2 and r["counted_points"] == 2
    assert r["flags"] == []


def test_invented_quote_is_not_awarded_and_flagged(points_question):
    r = apply_points(points_question, output(
        point("MP1", True, "balloon expands"),
        point("MP2", True, "particles vibrate faster"),  # not in the answer
    ), ANSWER)
    assert r["marks_awarded"] == 1
    assert "evidence_mismatch" in r["flags"]
    mp2 = r["detail"]["points"][1]
    assert mp2["awarded"] is False and mp2["not_awarded_because"] == "evidence_mismatch"


def test_reversed_point_is_not_awarded_even_if_model_says_awarded(points_question):
    answer = "The balloon shrinks."
    r = apply_points(points_question, output(
        point("MP1", True, "balloon shrinks", reversed_=True),
        point("MP2", False),
    ), answer)
    assert r["marks_awarded"] == 0
    assert "reversed" in r["flags"]


def test_requires_withdraws_dependent_point(points_question):
    # MP2 requires MP1. MP1's quote is invented, so MP2 must be withdrawn too.
    r = apply_points(points_question, output(
        point("MP1", True, "the balloon pops"),
        point("MP2", True, "particles gain energy and move further apart"),
    ), ANSWER)
    assert r["marks_awarded"] == 0
    assert "requires_unmet" in r["flags"]
    assert r["detail"]["points"][1]["not_awarded_because"] == "requires_unmet"


def test_requires_chain_is_followed(points_question):
    points_question["marks"] = 3
    points_question["mark_points"].append(
        {"id": "MP3", "text": "So pressure inside increases", "requires": ["MP2"]}
    )
    answer = "The balloon expands. Particles move further apart so pressure inside increases."
    r = apply_points(points_question, output(
        point("MP1", False),
        point("MP2", True, "particles move further apart"),
        point("MP3", True, "pressure inside increases"),
    ), answer)
    assert r["marks_awarded"] == 0  # MP1 missing -> MP2 withdrawn -> MP3 withdrawn


def test_choose_cap_is_normal_not_a_clamp():
    q = {"id": "t", "source": "assessment_book", "marking_mode": "points", "marks": 2, "choose": 2,
         "mark_points": [{"id": f"MP{i}", "text": f"point {i}"} for i in (1, 2, 3)]}
    answer = "alpha. beta. gamma."
    r = score(q, output(point("MP1", True, "alpha"), point("MP2", True, "beta"),
                        point("MP3", True, "gamma")), answer)
    assert r["marks_awarded"] == 2
    assert r["valid_points"] == 3 and r["counted_points"] == 2
    assert "clamped" not in r["flags"]
    assert r["confidence"] == "high"  # no drop for hitting the cap


def test_unknown_and_missing_points_are_flagged(points_question):
    r = apply_points(points_question, output(point("MP1", True, "balloon expands"), point("MP9", True, "x")), ANSWER)
    assert r["marks_awarded"] == 1
    assert "unknown_point" in r["flags"]
    assert "missing_point" in r["flags"]  # MP2 was never reported


def test_filler_quotes_not_in_answer_are_dropped(points_question):
    r = apply_points(points_question, output(
        point("MP1", False), point("MP2", False),
        filler_quotes=["because the", "made up filler"],
    ), ANSWER)
    assert r["detail"]["filler_quotes"] == ["because the"]
