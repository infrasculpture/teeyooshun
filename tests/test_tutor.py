"""Tutor explanations on feedback pages (app/marking/tutor.py).

Stored fixtures (fixtures/tutor_outputs.json) stand in for the API: no test here makes a real call.
"""

import json
from pathlib import Path

import pytest

from app.llm import LLMError
from app.marking import tutor
from ingest_helpers import FakeClient
from test_feedback_steps import POINTS_Q, stored, submit  # noqa: F401  (stored is a fixture)
from test_mcq import MCQ

TUTOR = json.loads((Path(__file__).parent / "fixtures" / "tutor_outputs.json").read_text(encoding="utf-8"))
GOOD = TUTOR["points_missing_term"]
INFO = {"model": "fake-marking", "api_call_id": None, "usage": {}}


@pytest.fixture
def tutor_api(client, monkeypatch):
    """Replace the tutor API call. Returns the list of calls made; set .reply to change the output, .fail to fail."""
    class Calls(list):
        reply, fail = GOOD, False

    made = Calls()

    def fake(conn, **kwargs):
        made.append(kwargs)
        if made.fail:
            raise LLMError("api_error", "503: overloaded")
        return made.reply, INFO

    monkeypatch.setattr("app.marking.tutor.structured_call", fake)
    return made


def section(page, key, next_key=None):
    start = page.index(f'id="step-{key}"')
    return page[start:page.index(f'id="step-{next_key}"')] if next_key else page[start:]


def attempt_row(conn, attempt_id):
    return conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()


# ---- Safe display -------------------------------------------------------------------------------

def test_bold_is_rendered_and_everything_else_is_escaped():
    html = str(tutor.render_bold("The <b>balloon</b> **expands** [1] & [missing: <i>x</i>]"))
    assert "<strong>expands</strong>" in html
    assert "&lt;b&gt;balloon&lt;/b&gt;" in html and "<b>" not in html and "<i>" not in html
    assert '<span class="tutor-missing">[missing: &lt;i&gt;x&lt;/i&gt;]</span>' in html
    assert "&amp;" in html


def test_an_unclosed_bold_marker_and_empty_text_are_safe():
    assert str(tutor.render_bold("a **b")) == "a <strong>b</strong>"
    assert str(tutor.render_bold("")) == "" and str(tutor.render_bold(None)) == ""
    assert str(tutor.render_bold("a ** ** b")) == "a <strong> </strong> b"


# ---- Feedback page ---------------------------------------------------------------------------------

def test_each_part_appears_in_its_step(client, stored, tutor_api):
    page = client.get(submit(client, POINTS_Q, stored("points_missing_term"))).text
    assert len(tutor_api) == 1
    score = section(page, "score", "answer")
    assert "Your tutor says" in score and "Name the <strong>particles</strong>" in score
    answer = section(page, "answer", "missing")
    assert "The balloon <strong>expands</strong> [1] because it gets hot." in answer
    assert '<span class="tutor-missing">[missing: the particles move further apart]</span>' in answer
    assert "which is the change the examiner wanted" in answer
    assert "It repeats the question and names no particle idea." in answer
    missing = section(page, "missing", "full")
    assert "The idea that particles gain energy and move further apart" in missing
    assert "You said it gets hot but did not say" in missing
    assert "The <strong>particles</strong> gain energy and move <strong>further apart</strong>." in missing
    full = section(page, "full", "rewrite")
    assert "<strong>Particles</strong> gain energy and move <strong>further apart</strong> [1]." in full
    assert "Try again" not in page
    # The existing feedback is all still there.
    assert '<mark>expands</mark><sup class="mp">1</sup>' in answer and "The one thing to fix" in score


def test_the_steps_are_unchanged_by_the_explanation(client, stored, tutor_api):
    page = client.get(submit(client, POINTS_Q, stored("points_missing_term"))).text
    assert page.count("Got it →") == 4 and page.count('<section class="step') == 5


def test_hostile_markup_from_the_model_is_escaped_on_the_page(client, stored, tutor_api):
    tutor_api.reply = TUTOR["hostile_markup"]
    page = client.get(submit(client, POINTS_Q, stored("points_missing_term"))).text
    assert "<script>alert" not in page and "<img src=x" not in page and "<u>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "&lt;img src=x onerror=alert(2)&gt;" in page
    assert "<strong>bold</strong>" in page


def test_the_score_and_marking_are_untouched(client, stored, tutor_api, bank):
    location = submit(client, POINTS_Q, stored("points_missing_term"))
    row = attempt_row(bank, location.rsplit("/", 1)[1])
    assert (row["marks_awarded"], row["max_marks"]) == (1, 2)
    result = json.loads(row["result_json"])
    assert "tutor" not in json.dumps(result).lower()  # nothing about the explanation leaks into the marking record
    assert json.loads(row["tutor_json"])["coaching"] == GOOD["coaching"]
    assert row["tutor_model"] == "fake-marking" and row["tutor_at"]


def test_generated_once_then_the_page_never_calls_the_api(client, stored, tutor_api):
    location = submit(client, POINTS_Q, stored("points_missing_term"))
    for _ in range(3):
        client.get(location)
        client.get(location + "?all=1")
    assert len(tutor_api) == 1


def test_a_rewrite_gets_its_own_explanation(client, stored, tutor_api, bank):
    answer = stored("points_missing_point")
    first = submit(client, POINTS_Q, answer).rsplit("/", 1)[1]
    r = client.post(f"/attempts/{first}/rewrite", data={"answer": "The balloon expands.", "shown_at": ""},
                    follow_redirects=False)
    assert len(tutor_api) == 2
    assert attempt_row(bank, int(r.headers["location"].rsplit("/", 1)[1]))["tutor_json"]


# ---- Failure and "Try again" ----------------------------------------------------------------------------

def test_a_failed_call_keeps_the_feedback_and_offers_try_again(client, stored, tutor_api, bank):
    tutor_api.fail = True
    location = submit(client, POINTS_Q, stored("points_missing_term"))
    attempt_id = location.rsplit("/", 1)[1]
    page = client.get(location).text
    assert "Try again" in section(page, "score", "answer") and "Your tutor says" not in page
    assert "The one thing to fix" in page and '<mark>expands</mark>' in page  # existing feedback intact
    assert attempt_row(bank, attempt_id)["tutor_json"] is None
    # The button tries once more; with the API back, the explanation appears.
    tutor_api.fail = False
    r = client.post(f"/attempts/{attempt_id}/tutor", data={"scope": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(f"/attempts/{attempt_id}")
    page = client.get(location).text
    assert "Your tutor says" in page and "Try again" not in page


def test_an_unusable_reply_is_treated_as_a_failure(client, stored, tutor_api, bank):
    tutor_api.reply = {**GOOD, "annotated_student_answer": "", "coaching": ""}
    location = submit(client, POINTS_Q, stored("points_missing_term"))
    assert attempt_row(bank, location.rsplit("/", 1)[1])["tutor_json"] is None
    assert "Try again" in client.get(location).text


def test_only_a_parent_can_regenerate_a_stored_explanation(client, stored, tutor_api):
    location = submit(client, POINTS_Q, stored("points_missing_term"))
    attempt_id = location.rsplit("/", 1)[1]
    client.post(f"/attempts/{attempt_id}/tutor", data={"regenerate": "1"}, follow_redirects=False)
    client.post(f"/attempts/{attempt_id}/tutor", data={}, follow_redirects=False)
    assert len(tutor_api) == 1  # a student's request never re-calls the API
    assert "Regenerate the tutor explanation" not in client.get(location).text


def test_regenerate_replaces_the_stored_explanation(bank, client, stored, tutor_api):
    from app import practice
    location = submit(client, POINTS_Q, stored("points_missing_term"))
    attempt_id = int(location.rsplit("/", 1)[1])
    attempt = practice.get_attempt(bank, attempt_id)
    question = practice.question_for_attempt(bank, attempt)
    tutor_api.reply = {**GOOD, "coaching": "A new paragraph."}
    assert tutor.generate(bank, attempt, question)["coaching"] == GOOD["coaching"]  # stored one is reused
    assert tutor.generate(bank, attempt, question, force=True)["coaching"] == "A new paragraph."
    assert json.loads(attempt_row(bank, attempt_id)["tutor_json"])["coaching"] == "A new paragraph."
    tutor_api.fail = True  # a failed regenerate keeps what was stored
    assert tutor.generate(bank, attempt, question, force=True) is None
    assert json.loads(attempt_row(bank, attempt_id)["tutor_json"])["coaching"] == "A new paragraph."


def test_mcqs_have_no_tutor_explanation(client, bank, tutor_api):
    from app.importer import import_questions
    import_questions(bank, [MCQ])
    r = client.post(f"/q/{MCQ['id']}/answer", data={"answer": "B"}, follow_redirects=False)
    page = client.get(r.headers["location"]).text
    assert tutor_api == [] and "Your tutor says" not in page and "tutor's explanation" not in page


# ---- The request itself -----------------------------------------------------------------------------------

def test_the_request_uses_the_marking_model_and_caches_the_stable_parts(bank, stored, monkeypatch):
    from app import practice
    from app.revision_cards import import_cards
    monkeypatch.setenv("MARKING_MODEL", "claude-sonnet-5-5")
    import_cards(bank, [{
        "id": "sci-s1-test-card-1", "subject": "science", "level": "sec1", "topic": "sci-07-particulate-nature",
        "prompt": "State what happens to gas particles when heated.", "recall": "typed_or_paper",
        "answer": {"format": "bullets", "points": [{"text": "They move faster and further apart.", "key_terms": []}]},
    }])
    answer = stored("points_missing_term")  # the marker is replaced; the tutor call below is the real code, fake client
    attempt = practice.get_attempt(bank, practice.submit_first_attempt(bank, POINTS_Q, answer))
    question = practice.question_for_attempt(bank, attempt)
    fake = FakeClient([GOOD])
    assert tutor.generate(bank, attempt, question, client=fake)["coaching"] == GOOD["coaching"]

    request = fake.requests[0]
    assert request["model"] == "claude-sonnet-5-5"
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    first, last = request["messages"][0]["content"][0], request["messages"][0]["content"][-1]
    assert first["cache_control"] == {"type": "ephemeral"} and "cache_control" not in last
    assert "A balloon is left in a hot car." in first["text"] and "<mark_scheme>" in first["text"]
    assert "State what happens to gas particles when heated." in first["text"]  # the topic's approved card
    assert "<student_answer>\nThe balloon expands because it gets hot.\n</student_answer>" in last["text"]
    assert '"marks_awarded": 1' in last["text"]  # the marking JSON
    assert "full_marks_answer" not in first["text"] and "MP2" in first["text"]
    row = bank.execute("SELECT * FROM api_calls WHERE purpose = 'tutor_explanation'").fetchone()
    assert row["question_id"] == POINTS_Q and row["output_tokens"] > 0


def test_coaching_longer_than_100_words_is_trimmed():
    long = " ".join(["word"] * 150)
    cleaned = tutor._clean({**GOOD, "coaching": long})
    assert len(cleaned["coaching"].rstrip("…").split()) == 100 and cleaned["coaching"].endswith("…")


def test_schema_has_the_tutor_columns(conn):
    columns = [r["name"] for r in conn.execute("PRAGMA table_info(attempts)")]
    assert {"tutor_json", "tutor_model", "tutor_at"} <= set(columns)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 17
