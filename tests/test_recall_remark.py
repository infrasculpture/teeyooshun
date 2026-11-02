"""Mark again: after an "Ask about this" chat on a typed Revise recall, the attempt can be marked once more with her
own chat messages as context. The second result replaces the score and box move; the first is kept. Made-up cards,
a fake chat model and a fake marker: no API calls."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app import app_settings, revise
from conftest import fake_mark
from ingest_helpers import FakeClient
from revise_helpers import RAIN, add_cards, settings

KEY = "abcdef123456"
SAID = "Water vapour condenses into clouds and droplets fall."
TUTOR = "Think about what happens when the vapour cools."


@pytest.fixture
def chat(client, monkeypatch):
    """The fake chat model: replies with TUTOR. Returns the list of calls."""
    made = []

    def fake(conn, **kwargs):
        made.append(kwargs)
        return {"reply": TUTOR}, {"model": "fake-light", "api_call_id": None,
                                  "usage": {"input_tokens": 10, "output_tokens": 5}}

    monkeypatch.setattr("app.ask.structured_call", fake)
    return made


@pytest.fixture
def marks(client, monkeypatch):
    """A fake marker that, like the real one, reads her clarification too. Returns the list of calls."""
    calls = []

    def wrapper(conn, question, answer, **kwargs):
        calls.append({"question": question, "answer": answer, **kwargs})
        extra = "\n" + kwargs["clarification"] if kwargs.get("clarification") else ""
        return fake_mark(conn, question, answer + extra, **{k: v for k, v in kwargs.items() if k != "clarification"})

    monkeypatch.setattr("app.marking.marker.mark_answer", wrapper)
    return calls


def typed_attempt(client, bank, answer="Water evaporates."):
    """RAIN in its own set, answered by typing. Returns the set id."""
    add_cards(bank, [RAIN])
    app_settings.save(bank, {**app_settings.get_all(bank), "ask_enabled": True})
    set_id = revise.create_set(bank, card_ids=["card-rain"], subject="geography")
    client.post(f"/revise/sets/{set_id}/typed/0", data={"answer": answer, "shown_at": ""})
    return set_id


def talk(client, set_id, text=SAID):
    return client.post("/ask/card/card-rain", data={"key": KEY, "text": text, "set_id": set_id})


def attempt(conn):
    return conn.execute("SELECT * FROM recall_attempts ORDER BY id").fetchone()


# ---- When the button shows ----------------------------------------------------------------------------

def test_the_button_appears_only_after_she_has_talked_to_the_tutor(client, bank, chat, marks):
    set_id = typed_attempt(client, bank)
    assert "Mark again" not in client.get(f"/revise/sets/{set_id}/result/0").text
    panel = talk(client, set_id).text
    assert "Mark again" in panel and f'action="/revise/sets/{set_id}/remark/0"' in panel
    # The conversation is saved, so the button is still there when she reloads the result.
    assert "Mark again" in client.get(f"/revise/sets/{set_id}/result/0").text


def test_a_paper_attempt_cannot_be_marked_again(client, bank, chat, marks):
    add_cards(bank, [RAIN])
    app_settings.save(bank, {**app_settings.get_all(bank), "ask_enabled": True})
    set_id = revise.create_set(bank, card_ids=["card-rain"], subject="geography")
    client.post(f"/revise/sets/{set_id}/tick/0", data={"unit": ["P1"]})
    assert "Mark again" not in talk(client, set_id).text
    client.post(f"/revise/sets/{set_id}/remark/0")
    assert marks == [] and attempt(bank)["remark_count"] == 0


def test_without_a_chat_the_route_does_nothing(client, bank, chat, marks):
    set_id = typed_attempt(client, bank)
    r = client.post(f"/revise/sets/{set_id}/remark/0", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/revise/sets/{set_id}/result/0"
    assert len(marks) == 1 and attempt(bank)["remark_count"] == 0   # only the first marking was ever made


# ---- What it does ---------------------------------------------------------------------------------------

def test_marking_again_replaces_the_score_and_box_move_and_keeps_the_first_result(client, bank, chat, marks):
    set_id = typed_attempt(client, bank)
    first = attempt(bank)
    assert (first["points_covered"], first["box_before"], first["box_after"]) == (1, 1, 1)   # 33%: stays in box 1
    talk(client, set_id)
    r = client.post(f"/revise/sets/{set_id}/remark/0", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/revise/sets/{set_id}/result/0"

    again = attempt(bank)
    assert (again["points_covered"], again["box_before"], again["box_after"]) == (3, 1, 2)   # 100%: up one box
    assert again["remark_count"] == 1 and json.loads(again["covered_ids_json"]) == ["P1", "P2", "P3"]
    assert json.loads(again["first_result_json"])["detail"]["score"] == 1   # the first marking is kept
    assert json.loads(again["result_json"])["detail"]["score"] == 3
    progress = revise.get_progress(bank, "card-rain")
    assert progress["box"] == 2
    created = datetime.strptime(again["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert progress["due_at"] == (created + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")   # box 2: 2 days

    # The marker was called twice: the check, then the re-mark with her words (never the tutor's) as context.
    assert [c["purpose"] for c in marks] == ["recall", "recall_remark"]
    assert SAID in marks[1]["clarification"] and TUTOR not in marks[1]["clarification"]
    assert marks[1]["answer"] == "Water evaporates."   # her answer is unchanged
    html = client.get(f"/revise/sets/{set_id}/result/0").text
    assert "3 / 3" in html and "Marked again with what you told the tutor." in html and "Known." in html


def test_it_can_only_be_done_once(client, bank, chat, marks):
    set_id = typed_attempt(client, bank)
    talk(client, set_id)
    client.post(f"/revise/sets/{set_id}/remark/0")
    client.post(f"/revise/sets/{set_id}/remark/0")
    assert len(marks) == 2                                   # check + one re-mark, not three
    assert attempt(bank)["remark_count"] == 1 and revise.get_progress(bank, "card-rain")["box"] == 2
    assert "Mark again" not in talk(client, set_id, "One more thing about rain.").text
    assert "Mark again" not in client.get(f"/revise/sets/{set_id}/result/0").text


def test_a_second_chat_after_marking_again_does_not_open_another_go(client, bank, chat, marks):
    set_id = typed_attempt(client, bank)
    talk(client, set_id)
    client.post(f"/revise/sets/{set_id}/remark/0")
    talk(client, set_id, "And rain is water.")
    client.post(f"/revise/sets/{set_id}/remark/0")
    assert len(marks) == 2 and attempt(bank)["remark_count"] == 1


def test_a_failed_re_mark_changes_nothing_and_can_be_tried_again(client, bank, chat, marks):
    set_id = typed_attempt(client, bank)
    talk(client, set_id, "FAIL this one")                      # the fake marker fails when it sees FAIL
    r = client.post(f"/revise/sets/{set_id}/remark/0", follow_redirects=False)
    assert r.headers["location"].endswith("/result/0?remark=failed")
    row = attempt(bank)
    assert (row["points_covered"], row["box_after"], row["remark_count"], row["first_result_json"]) == (1, 1, 0, None)
    assert "didn’t work just now" in client.get(r.headers["location"]).text
    assert "Mark again" in client.get(f"/revise/sets/{set_id}/result/0").text   # the one go is still there
    talk(client, set_id, SAID)
    assert revise.remark_url(bank, set_id, "card-rain")


def test_a_later_attempt_keeps_the_cards_progress(client, bank, chat, marks):
    set_id = typed_attempt(client, bank)
    talk(client, set_id)
    other = revise.create_set(bank, card_ids=["card-rain"], subject="geography")
    revise.record_attempt(bank, dict(RAIN, version=1), method="paper", covered_ids=[], units_total=3,
                          settings=settings(), set_id=other)   # nothing covered: the card stays in box 1
    assert revise.get_progress(bank, "card-rain")["box"] == 1
    client.post(f"/revise/sets/{set_id}/remark/0")            # her earlier attempt is marked again: 100%, box 1 -> 2
    first = bank.execute("SELECT * FROM recall_attempts WHERE set_id = ?", (set_id,)).fetchone()
    assert first["box_after"] == 2 and first["remark_count"] == 1
    assert revise.get_progress(bank, "card-rain")["box"] == 1   # untouched: the later attempt set it


def test_the_button_is_hidden_when_ask_is_off(client, bank, chat, marks):
    set_id = typed_attempt(client, bank)
    talk(client, set_id)
    app_settings.save(bank, {**app_settings.get_all(bank), "ask_enabled": False})
    assert "Mark again" not in client.get(f"/revise/sets/{set_id}/result/0").text


def test_a_failed_second_call_reaches_typed_check_as_check_failed(bank):
    """The real marker's non-fatal failure becomes CheckFailed (the route turns it into a retry message)."""
    add_cards(bank, [RAIN])
    with pytest.raises(revise.CheckFailed):
        revise.typed_check(bank, RAIN, "Water evaporates.", client=FakeClient(["TRUNCATED"]),
                           clarification="- more", purpose="recall_remark")


def test_the_second_marking_sends_her_words_as_a_separate_uncached_block_and_logs_its_own_purpose(bank):
    add_cards(bank, [RAIN])
    out = {"points": [{"id": i, "status": "covered", "evidence_quote": q, "reason": "", "reversed": False}
                      for i, q in (("P1", "evaporates"), ("P2", "condenses"), ("P3", "droplets"))],
           "missing_key_terms": [], "filler_quotes": [], "misread_question": False, "borderline": False,
           "borderline_reason": "", "one_to_add": "", "full_marks_answer": "", "technique_note": "", "other_points": []}
    client = FakeClient([out])
    check = revise.typed_check(bank, RAIN, "Water evaporates.", client=client,
                               clarification=f"- {SAID}", purpose="recall_remark")
    blocks = client.requests[0]["messages"][0]["content"]
    assert blocks[-1]["text"].startswith("<student_clarification>") and "cache_control" not in blocks[-1]
    assert SAID in blocks[-1]["text"] and blocks[-2]["text"].startswith("<student_answer>")
    assert check["covered_count"] == 3          # quotes from the clarification pass the code-side quote check
    assert bank.execute("SELECT purpose FROM api_calls").fetchone()[0] == "recall_remark"
