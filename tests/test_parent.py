"""Parent-only actions behind the PIN (fake marker, no API calls)."""

import pytest

from app import practice
from app.parent import COOKIE_NAME, MAX_WRONG, make_cookie
from conftest import PARENT_PIN

POINTS_Q = "sci-test-points-q1"


@pytest.fixture
def attempt_id(client):
    r = client.post(f"/q/{POINTS_Q}/answer", data={"answer": "The balloon expands."}, follow_redirects=False)
    return int(r.headers["location"].rsplit("/", 1)[1])


def login(client, pin=PARENT_PIN, next_url="/"):
    return client.post("/parent/login", data={"pin": pin, "next": next_url}, follow_redirects=False)


def test_review_refused_without_pin(client, bank, attempt_id):
    r = client.post(f"/attempts/{attempt_id}/review", data={"verdict": "agree"})
    assert r.status_code == 403
    assert practice.events_for(bank, attempt_id) == []


def test_wrong_pin_sets_no_cookie(client):
    r = login(client, "000111")
    assert r.status_code == 401
    assert COOKIE_NAME not in r.cookies


def test_parent_can_agree_then_disagree(client, bank, attempt_id):
    r = login(client, next_url=f"/attempts/{attempt_id}")
    assert r.status_code == 303 and r.headers["location"] == f"/attempts/{attempt_id}"
    client.post(f"/attempts/{attempt_id}/review", data={"verdict": "agree", "note": "fair"})
    r = client.post(f"/attempts/{attempt_id}/review", data={"verdict": "disagree", "note": "too harsh"},
                    headers={"HX-Request": "true"})
    assert "Your verdict" in r.text and "Disagree" in r.text
    verdict = practice.parent_verdict(bank, attempt_id)
    assert (verdict["event"], verdict["actor"], verdict["note"]) == ("parent_disagree", "parent", "too harsh")
    assert len(practice.events_for(bank, attempt_id)) == 2  # both kept; the latest counts


def test_parent_panel_only_with_pin(client, attempt_id):
    assert "Parent review" not in client.get(f"/attempts/{attempt_id}").text
    login(client)
    page = client.get(f"/attempts/{attempt_id}").text
    assert "Parent review" in page and "base (assessment book key)" in page


def test_parent_flag_is_stored_as_parent(client, bank, attempt_id):
    login(client)
    client.post(f"/attempts/{attempt_id}/looks-wrong", data={"note": "check MP2"})
    events = practice.events_for(bank, attempt_id)
    assert [(e["event"], e["actor"]) for e in events] == [("marking_looks_wrong", "parent")]


def test_lockout_after_repeated_wrong_pins(client):
    for _ in range(MAX_WRONG):
        assert login(client, "999999").status_code == 401
    r = login(client)  # even the right PIN is refused while locked
    assert r.status_code == 429
    assert COOKIE_NAME not in r.cookies


def test_parent_features_off_with_placeholder_pin(client, attempt_id, monkeypatch):
    monkeypatch.setenv("PARENT_PIN", "0000")
    assert login(client, "0000").status_code == 503
    client.cookies.set(COOKIE_NAME, make_cookie())
    assert client.post(f"/attempts/{attempt_id}/review", data={"verdict": "agree"}).status_code == 403


def test_forged_cookie_refused(client, attempt_id):
    client.cookies.set(COOKIE_NAME, "9999999999.not-a-real-signature")
    assert client.post(f"/attempts/{attempt_id}/review", data={"verdict": "agree"}).status_code == 403


def test_changing_the_pin_logs_parents_out(client, attempt_id, monkeypatch):
    login(client)
    monkeypatch.setenv("PARENT_PIN", "731902")
    assert client.post(f"/attempts/{attempt_id}/review", data={"verdict": "agree"}).status_code == 403


def test_expired_cookie_refused(client, attempt_id):
    client.cookies.set(COOKIE_NAME, make_cookie(now=0))
    assert client.post(f"/attempts/{attempt_id}/review", data={"verdict": "agree"}).status_code == 403


def test_login_only_redirects_within_the_app(client):
    r = login(client, next_url="//evil.example/")
    assert r.headers["location"] == "/"
