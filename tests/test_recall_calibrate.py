"""The recall calibration runner and the made-up recall set (docs/recall_set_v1.json). The API is always faked."""

import json
import shutil
from pathlib import Path

from app import app_settings, calibrate, revise
from app.marking import rules
from conftest import fake_mark

SET = Path(__file__).parent.parent / "docs" / "recall_set_v1.json"


def data():
    return json.loads(SET.read_text(encoding="utf-8"))


# ---- The file ---------------------------------------------------------------------------------------------

def test_the_recall_set_is_valid_and_has_twelve_cases_of_every_kind():
    d = data()
    assert calibrate.is_recall_file(SET) and calibrate.check_recall_file(SET, d) == []
    assert d["calibration_set"] == "recall_set_v1" and len(d["cases"]) == 12
    notes = " ".join(c["note"].lower() for c in d["cases"])
    for kind in ("paraphrase", "definition", "partial", "example line", "factually wrong", "no penalty"):
        assert kind in notes


def test_the_set_says_it_is_made_up():
    d = data()
    assert "MADE-UP" in d["note"] and all(c["source_ref"] == "Invented example" for c in d["cards"])


def test_a_question_file_is_not_a_recall_file():
    assert not calibrate.is_recall_file(Path(__file__).parent / "fixtures" / "sample_calibration.json")
    assert not calibrate.is_recall_file(Path(__file__).parent / "fixtures" / "does-not-exist.json")


def test_problems_in_a_recall_file_are_all_listed(tmp_path):
    d = data()
    d["cases"][0]["card"] = "no-such-card"
    d["cases"][1]["expect"]["points"]["P9"] = "covered"
    d["cases"][2]["expect"]["box_move"] = "sideways"
    d["cases"][3]["expect"]["extras_include"] = ["scary"]
    d["cases"][4]["answer"] = " "
    d["cases"].append(dict(d["cases"][5]))   # same id twice
    problems = "\n".join(calibrate.check_recall_file(SET, d))
    for text in ("unknown card", "bad expectation for point 'P9'", "box_move must be", "extras_include may only",
                 "needs an id and an answer", "duplicate id"):
        assert text in problems
    assert calibrate.check_recall_file(SET, {"cards": [], "cases": []}) != []


# ---- Judging a case ---------------------------------------------------------------------------------------

def _check(case_id, output):
    d = data()
    case = next(c for c in d["cases"] if c["id"] == case_id)
    card = next(c for c in d["cards"] if c["id"] == case["card"])
    question = revise.card_as_question(card, revise.check_units(card))
    result = rules.score(question, output, case["answer"], None, {})
    result.update({"model": "fake", "usage": None})
    return case, {"result": result}


def _output(points, others=()):
    return {"points": [{"id": i, "status": s, "evidence_quote": q, "reason": "", "reversed": False}
                       for i, s, q in points],
            "missing_key_terms": [], "filler_quotes": [], "misread_question": False, "borderline": False,
            "borderline_reason": "", "one_to_add": "", "full_marks_answer": "", "technique_note": "",
            "other_points": [{"quote": q, "classification": c, "card_id": "", "where_on_card": "", "reason": ""}
                             for q, c in others]}


def test_a_case_that_comes_out_as_expected_is_ok():
    out = _output([("P1", "covered", "executed the rebel leaders"), ("P2", "missed", ""), ("P3", "missed", "")])
    case, check = _check("R01", out)
    row = calibrate.judge_recall(case, check, app_settings.DEFAULTS)
    assert row["ok"] and row["problems"] == [] and row["box_move"] == "stay" and row["score"] == 1


def test_each_kind_of_difference_is_reported():
    out = _output([("P1", "partial", "executed the rebel leaders"), ("P2", "missed", ""), ("P3", "missed", "")],
                  [("heads on display in public", "unverified")])
    case, check = _check("R01", out)
    row = calibrate.judge_recall(case, check, app_settings.DEFAULTS)
    problems = "\n".join(row["problems"])
    assert not row["ok"]
    assert "P1: expected covered, got partial" in problems
    assert "found an extra statement classed unverified, which should not appear" in problems
    assert "box move: expected stay, got down" in problems   # half a point of three is under 30%


def test_the_wrong_answer_case_needs_a_wrong_statement():
    out = _output([("P1", "missed", ""), ("P2", "missed", ""), ("P3", "missed", "")])
    case, check = _check("R10", out)
    row = calibrate.judge_recall(case, check, app_settings.DEFAULTS)
    assert not row["ok"] and "factually wrong" in "\n".join(row["problems"])
    out = _output([("P1", "missed", ""), ("P2", "missed", ""), ("P3", "missed", "")],
                  [("cold water makes it dissolve faster", "incorrect")])
    case, check = _check("R10", out)
    assert calibrate.judge_recall(case, check, app_settings.DEFAULTS)["ok"]


# ---- Running a set ---------------------------------------------------------------------------------------------

def test_main_runs_a_recall_set_in_a_temporary_database_and_saves_the_results(tmp_path, monkeypatch, capsys):
    file = tmp_path / "calibration" / "recall_set_v1.json"
    file.parent.mkdir()
    shutil.copy(SET, file)
    real_db = tmp_path / "real.db"
    monkeypatch.setenv("DB_PATH", str(real_db))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    seen = []

    def fake_check(conn, card, text, **kwargs):
        seen.append(card["id"])
        question = revise.card_as_question(card, revise.check_units(card))
        result = fake_mark(conn, question, text)
        return {"result": result}

    monkeypatch.setattr("app.calibrate.revise.typed_check", fake_check)
    assert calibrate.main([str(file)]) == 0
    out = capsys.readouterr().out
    assert len(seen) == 12 and "Cases: 12  checked: 12" in out and "R12" in out
    saved = list((file.parent / "results").glob("recall_set_v1-*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text(encoding="utf-8"))["summary"]["cases"] == 12
    assert not real_db.exists()   # the made-up cards never went near the real database


def test_only_runs_one_case_and_a_bad_file_runs_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "real.db"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    calls = []
    monkeypatch.setattr("app.calibrate.revise.typed_check",
                        lambda conn, card, text, **kw: calls.append(card["id"]) or {"result": fake_mark(
                            conn, revise.card_as_question(card, revise.check_units(card)), text)})
    assert calibrate.main([str(SET), "--only", "R06"]) == 0 and calls == ["cal-rec-sci-solvent"]
    capsys.readouterr()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"kind": "recall", "cards": [], "cases": []}), encoding="utf-8")
    assert calibrate.main([str(bad)]) == 1 and calls == ["cal-rec-sci-solvent"]
    assert "needs a non-empty 'cards' list" in capsys.readouterr().err
