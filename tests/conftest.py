import copy
import json
from pathlib import Path

import pytest

from app.db import connect, init_db
from app.importer import import_questions, load_file
from app.marking import rules
from app.parent import reset_lockout
from app.topics import load_topics

FIXTURE = Path(__file__).parent / "fixtures" / "sample_calibration.json"
PARENT_PIN = "482915"

# Extra made-up questions for practice tests.
ONE_MARK_QUESTION = {
    "id": "sci-test-one-mark", "subject": "science", "level": "sec1",
    "topic": "sci-07-particulate-nature", "source": "assessment_book",
    "question": "Name the process.", "marks": 1, "marking_mode": "points",
    "mark_points": [{"id": "MP1", "text": "Diffusion", "key_terms": ["diffusion"]}],
}
CAP_QUESTION = {
    "id": "sci-test-cap", "subject": "science", "level": "sec1",
    "topic": "sci-01-scientific-endeavour", "source": "assessment_book",
    "question": "Give any two examples.", "marks": 2, "choose": 2, "marking_mode": "points",
    "mark_points": [{"id": f"MP{i}", "text": w, "key_terms": [w]} for i, w in enumerate(["alpha", "beta", "gamma"], 1)],
}


@pytest.fixture
def conn(tmp_path):
    """A fresh, empty database in a temporary folder."""
    db = tmp_path / "test.db"
    init_db(db)
    c = connect(db)
    yield c
    c.close()


@pytest.fixture
def sample_data():
    """The made-up fixture file, loaded fresh for each test (safe to modify)."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def points_question(sample_data):
    return copy.deepcopy(sample_data["questions"][0])


@pytest.fixture
def lorms_question(sample_data):
    return copy.deepcopy(sample_data["questions"][1])


# ---- Practice loop: question bank, fake marker, web client ----

def fake_mark(conn, question, answer, *, purpose="marking", client=None):
    """Stands in for the real marker: no API calls.

    Points mode awards a mark point when its first key term appears in the answer.
    LORMS mode reports the first sentence as one unsupported inference.
    An answer containing "FAIL" behaves like a failed API call.
    """
    base = {"question_id": question["id"], "model": "fake", "api_call_id": None, "usage": None}
    if "FAIL" in answer:
        return {**base, "mode": question["marking_mode"], "marks_awarded": None,
                "max_marks": question["marks"], "valid_points": None, "counted_points": None,
                "level_awarded": None, "confidence": None, "confidence_reason": "",
                "flags": ["api_error"], "error": "fake failure", "model_output": None, "detail": {}}
    common = {"borderline": False, "borderline_reason": "",
              "full_marks_answer": "FULL MARKS MODEL ANSWER", "technique_note": "TECHNIQUE NOTE"}
    if question["marking_mode"] == "points":
        points = []
        for mp in question["mark_points"]:
            term = (mp.get("key_terms") or [mp["text"]])[0]
            hit = term.lower() in answer.lower()
            points.append({"id": mp["id"], "awarded": hit, "evidence_quote": term if hit else "",
                           "reason": "fake reason", "reversed": False})
        output = {"points": points, "missing_key_terms": [], "filler_quotes": [],
                  "misread_question": False, **common}
    else:
        output = {"features": [{"claim": answer.split(".")[0], "supported": False,
                                "support_quote": "", "explained": False}],
                  "describes_without_inference": False, "proposed_level": 2, "proposed_marks": 2,
                  "descriptor_matched": "Inference, unsupported", "why_not_higher": "No evidence.",
                  "peel_map": {"point": "p", "evidence": "MISSING", "explain": "e", "link": "MISSING"},
                  **common}
    result = rules.score(question, output, answer)
    result.update({**base, "error": None, "model_output": output})
    return result


@pytest.fixture
def fake_marker(monkeypatch):
    """Replace the real marker. Returns a list recording each call."""
    calls = []

    def wrapper(conn, question, answer, **kwargs):
        calls.append({"question": question, "answer": answer})
        return fake_mark(conn, question, answer, **kwargs)

    monkeypatch.setattr("app.marking.marker.mark_answer", wrapper)
    return calls


@pytest.fixture
def bank(tmp_path, monkeypatch):
    """A database holding the fixture questions, used by the app via DB_PATH."""
    db = tmp_path / "app.db"
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PARENT_PIN", PARENT_PIN)
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    reset_lockout()
    init_db(db)
    c = connect(db)
    load_topics(c)
    import_questions(c, load_file(FIXTURE)["questions"])
    yield c
    c.close()
    reset_lockout()


@pytest.fixture
def client(bank, fake_marker):
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


def attempt_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
