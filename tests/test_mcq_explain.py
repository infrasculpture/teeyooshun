"""MCQ explanations: written once per (question version, chosen option), reused after that,
and never needed for the score. Fake API only."""

import pytest

from app.importer import get_question, import_questions
from app.marking import mcq_explain
from ingest_helpers import FakeClient
from test_mcq import MCQ

WRONG = {"why_correct": "A plane mirror gives regular reflection.", "why_chosen_wrong": "Brown paper scatters light.",
         "misconception": "You may be mixing up reflecting with being bright."}
RIGHT = {"why_correct": "A plane mirror gives regular reflection.", "why_chosen_wrong": "ignored", "misconception": "ignored"}


@pytest.fixture
def mcq(bank):
    import_questions(bank, [MCQ])
    return get_question(bank, MCQ["id"])


@pytest.fixture
def calls(client, monkeypatch):
    """Replace the API call for explanations. Returns the list of calls made; set calls.fail to make it fail."""
    from app.llm import LLMError

    class Calls(list):
        fail = False

    made = Calls()

    def fake(conn, **kwargs):
        made.append(kwargs)
        if made.fail:
            raise LLMError("api_error", "503: overloaded")
        chosen_right = "That is correct." in kwargs["content"][-1]["text"]
        return (RIGHT if chosen_right else WRONG), {"model": "fake-light", "api_call_id": None, "usage": {}}

    monkeypatch.setattr("app.marking.mcq_explain.structured_call", fake)
    return made


def answer(client, choice):
    r = client.post(f"/q/{MCQ['id']}/answer", data={"answer": choice}, follow_redirects=False)
    return r.headers["location"]


def test_wrong_choice_shows_both_explanations(client, mcq, calls):
    page = client.get(answer(client, "B")).text
    assert "Not this time" in page and "Plane mirror" in page  # the code-marked result
    assert "Why C is right" in page and "A plane mirror gives regular reflection." in page
    assert "Why B is not" in page and "Brown paper scatters light." in page
    assert "Watch out: You may be mixing up reflecting with being bright." in page
    assert calls[0]["purpose"] == "mcq_explanation" and calls[0]["question_id"] == MCQ["id"]


def test_right_choice_shows_only_why_it_is_right(client, mcq, calls, bank):
    page = client.get(answer(client, "C")).text
    assert "Correct" in page and "Why C is right" in page
    assert "Why C is not" not in page and "Watch out" not in page and "ignored" not in page
    row = bank.execute("SELECT * FROM mcq_explanations").fetchone()
    assert (row["chosen_option"], row["why_chosen_wrong"], row["misconception"]) == ("C", "", "")


def test_explanation_is_generated_once_per_choice_and_reused(client, mcq, calls, bank):
    answer(client, "B")
    answer(client, "B")
    assert len(calls) == 1  # the second time comes from the database
    answer(client, "A")
    assert len(calls) == 2  # a different choice gets its own explanation
    client.get(answer(client, "B"))
    assert len(calls) == 2  # and viewing feedback never calls the API
    assert bank.execute("SELECT COUNT(*) FROM mcq_explanations").fetchone()[0] == 2


def test_edited_question_gets_a_fresh_explanation(client, mcq, calls, bank):
    answer(client, "B")
    import_questions(bank, [dict(MCQ, question="Which surface reflects light most regularly?")])
    answer(client, "B")
    assert len(calls) == 2
    versions = [r["question_version"] for r in bank.execute("SELECT question_version FROM mcq_explanations ORDER BY 1")]
    assert versions == [1, 2]


def test_api_failure_still_shows_the_result_with_a_try_again_link(client, mcq, calls, bank):
    calls.fail = True
    location = answer(client, "B")
    page = client.get(location).text
    assert "Not this time" in page and "The correct answer is" in page and "Plane mirror" in page
    assert "Try again" in page and "Why C is right" not in page
    assert bank.execute("SELECT marks_awarded FROM attempts").fetchone()[0] == 0  # scored by code regardless
    assert bank.execute("SELECT COUNT(*) FROM mcq_explanations").fetchone()[0] == 0
    # The link tries once more; when the API is back, the explanation appears.
    calls.fail = False
    attempt_id = location.rsplit("/", 1)[1]
    r = client.post(f"/attempts/{attempt_id}/explain", data={"scope": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/attempts/{attempt_id}"
    page = client.get(location).text
    assert "Why B is not" in page and "Try again" not in page


def test_real_call_uses_the_light_model_caches_the_question_and_logs_tokens(bank, mcq, monkeypatch):
    monkeypatch.setenv("LIGHT_MODEL", "claude-haiku-4-5")
    fake = FakeClient([WRONG])
    result = mcq_explain.explain(bank, mcq, "B", client=fake)
    assert result == WRONG
    request = fake.requests[0]
    assert request["model"] == "claude-haiku-4-5"
    assert "effort" not in request["output_config"]  # LIGHT_EFFORT is blank by default
    content = request["messages"][0]["content"]
    assert content[0]["text"].startswith("<question>") and content[0]["cache_control"] == {"type": "ephemeral"}
    assert content[1]["text"] == "She chose option B. That is wrong."
    row = bank.execute("SELECT * FROM api_calls WHERE purpose = 'mcq_explanation'").fetchone()
    assert row["question_id"] == MCQ["id"] and row["input_tokens"] == 2000 and row["output_tokens"] == 500
    # Second time: no API call at all.
    assert mcq_explain.explain(bank, mcq, "B", client=FakeClient([])) == WRONG


def test_page_image_is_included_when_the_mcq_depends_on_a_diagram(bank, mcq, tmp_path):
    png = tmp_path / "data" / "renders" / "s" / "f-v1" / "p003.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    images = [{"page": 3, "display": "renders/s/f-v1/display/p003.jpg", "model": "renders/s/f-v1/p003.png"}]
    with_diagram = dict(mcq, figure="needed_to_answer", page_images=images)
    fake = FakeClient([WRONG, WRONG])
    mcq_explain.explain(bank, with_diagram, "B", client=fake)
    assert [c["type"] for c in fake.requests[0]["messages"][0]["content"]] == ["text", "image", "text", "text"]
    mcq_explain.explain(bank, dict(mcq, page_images=images), "A", client=fake)  # no diagram needed: no image
    assert [c["type"] for c in fake.requests[1]["messages"][0]["content"]] == ["text", "text"]


def test_schema_has_the_explanations_table(conn):
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'mcq_explanations'").fetchone()
