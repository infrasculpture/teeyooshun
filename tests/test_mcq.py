"""Multiple-choice questions: code-only marking, and figure questions left out of practice."""

import pytest

from app import practice
from app.importer import import_questions, validate_question
from app.marking.mcq import mark_mcq

MCQ = {
    "id": "sci-test-mcq", "subject": "science", "level": "sec1", "topic": "sci-05-ray-model-of-light",
    "source": "assessment_book", "question": "Which surface reflects light best?", "marks": 1,
    "marking_mode": "mcq", "correct_option": "C",
    "options": [{"label": "A", "text": "Black cloth"}, {"label": "B", "text": "Brown paper"},
                {"label": "C", "text": "Plane mirror"}, {"label": "D", "text": "Wooden board"}],
}


def test_mark_mcq():
    right, wrong = mark_mcq(MCQ, "C"), mark_mcq(MCQ, "A")
    assert (right["marks_awarded"], wrong["marks_awarded"]) == (1, 0)
    assert right["confidence"] == "high" and right["api_call_id"] is None and right["model"] == "code"


def test_mcq_validation():
    assert validate_question(MCQ) == []
    assert any("correct_option" in p for p in validate_question(dict(MCQ, correct_option="E")))
    assert any("at least two options" in p for p in validate_question(dict(MCQ, options=[])))


@pytest.fixture
def mcq_client(client, bank):
    import_questions(bank, [MCQ])
    return client


def test_question_page_shows_options_but_not_the_answer(mcq_client):
    page = mcq_client.get("/q/sci-test-mcq").text
    assert page.count('type="radio"') == 4 and "Plane mirror" in page
    assert "correct_option" not in page and "✓" not in page


def test_answering_an_mcq_makes_no_api_call(mcq_client, bank, fake_marker):
    r = mcq_client.post("/q/sci-test-mcq/answer", data={"answer": "B"}, follow_redirects=False)
    page = mcq_client.get(r.headers["location"]).text
    assert "Not this time" in page and "Plane mirror" in page
    assert "Have another go" not in page
    assert fake_marker == []  # the API marker was never called
    row = bank.execute("SELECT * FROM attempts").fetchone()
    assert (row["answer_text"], row["marks_awarded"], row["model"]) == ("B", 0, "code")


def test_invalid_choice_is_refused(mcq_client, bank):
    r = mcq_client.post("/q/sci-test-mcq/answer", data={"answer": "Z"})
    assert r.status_code == 422
    assert bank.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0


def test_only_drawing_questions_and_worked_examples_are_left_out_of_practice(bank):
    topic = "sci-05-ray-model-of-light"
    import_questions(bank, [dict(MCQ, id="sci-test-draw", answer_format="draw_or_label"),
                            dict(MCQ, id="sci-test-worked", worked_example=True)])
    assert practice.next_question_id(bank, "science", topic) is None
    assert all(t["topic"] != topic for t in practice.list_topics(bank, "science"))
    # Table questions and questions that depend on a figure are typed like any other.
    import_questions(bank, [dict(MCQ, id="sci-test-table", answer_format="table"),
                            dict(MCQ, id="sci-test-figure", figure="needed_to_answer")])
    assert practice.next_question_id(bank, "science", topic) == "sci-test-figure"
    assert [t["count"] for t in practice.list_topics(bank, "science") if t["topic"] == topic] == [2]
