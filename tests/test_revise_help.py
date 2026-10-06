"""'Help me remember' (docs/REVISE_SPEC.md section 11): cached help, follow-ups, with_help, parent edits.
Made-up cards and a fake API only."""

from datetime import datetime, timedelta, timezone

import pytest

from app import revise, revise_help
from app.llm import LLMError
from app.revision_cards import get_card, import_cards
from conftest import PARENT_PIN
from ingest_helpers import FakeClient
from revise_helpers import CLIMATE, RAIN, add_cards, settings

HELP = {"why": "Climate tells you what a place is usually like.",
        "marks_in": "The examiner wants average weather over a long period.",
        "common_slip": "Writing today's weather instead of the average weather.",
        "hook": "Climate = long-term pattern, weather = today."}
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def api(monkeypatch):
    """Replace the API call. Returns the list of calls; set api.fail to make it fail, api.output to change the reply."""
    class Calls(list):
        fail = False
        output = HELP

    made = Calls()

    def fake(conn, **kwargs):
        made.append(kwargs)
        if made.fail:
            raise LLMError("api_error", "503: overloaded")
        if kwargs["purpose"] == "card_help_followup":
            return {"reply": "Think of it as the usual pattern."}, {"model": "fake-light", "api_call_id": None}
        return dict(made.output), {"model": "fake-light", "api_call_id": None}

    monkeypatch.setattr("app.revise_help.structured_call", fake)
    return made


def parent_login(client):
    client.post("/parent/login", data={"pin": PARENT_PIN, "next": "/"}, follow_redirects=False)


def open_help(client, card_id="card-climate", set_id=""):
    return client.post(f"/revise/cards/{card_id}/help", data={"set_id": set_id})


def start(client, **form) -> str:
    r = client.post("/revise/start", data={"subject": "geography", **form}, follow_redirects=False)
    return r.headers["location"][:-len("/study/0")]


def with_help_flags(conn) -> list:
    return [r["with_help"] for r in conn.execute("SELECT with_help FROM recall_attempts ORDER BY id")]


# ---- Caching ---------------------------------------------------------------------------

def test_first_open_writes_the_help_and_later_opens_reuse_it(client, bank, api):
    add_cards(bank, [CLIMATE])
    html = open_help(client).text
    assert "Why this matters" in html and HELP["why"] in html and HELP["hook"] in html
    assert "The marks are in" in html and "Common slip" in html and "Memory hook" in html
    assert api[0]["purpose"] == "card_help" and api[0]["question_id"] == "card-climate"
    open_help(client)
    open_help(client)
    assert len(api) == 1  # the second and third opens come from the database
    assert bank.execute("SELECT COUNT(*) FROM card_help").fetchone()[0] == 1
    assert bank.execute("SELECT COUNT(*) FROM help_views").fetchone()[0] == 3


def test_new_card_version_gets_fresh_help(client, bank, api):
    add_cards(bank, [CLIMATE])
    open_help(client)
    changed = dict(CLIMATE, prompt="Define climate in one sentence.")
    import_cards(bank, [changed])
    assert get_card(bank, "card-climate")["version"] == 2
    open_help(client)
    assert len(api) == 2
    versions = [r["card_version"] for r in bank.execute("SELECT card_version FROM card_help ORDER BY 1")]
    assert versions == [1, 2]


def test_api_failure_shows_the_retry_message_and_stores_nothing(client, bank, api):
    add_cards(bank, [CLIMATE])
    api.fail = True
    html = open_help(client).text
    assert "Couldn&#39;t load help, try again" in html or "Couldn't load help, try again" in html
    assert "Try again" in html
    assert bank.execute("SELECT COUNT(*) FROM card_help").fetchone()[0] == 0
    assert bank.execute("SELECT COUNT(*) FROM help_views").fetchone()[0] == 0  # nothing was shown
    api.fail = False
    assert HELP["why"] in open_help(client).text


def test_unusable_reply_is_treated_as_a_failure(client, bank, api):
    add_cards(bank, [CLIMATE])
    api.output = dict(HELP, marks_in="  ")
    assert "Try again" in open_help(client).text
    assert bank.execute("SELECT COUNT(*) FROM card_help").fetchone()[0] == 0


def test_one_point_card_is_accepted_with_an_empty_hook(client, bank, api):
    add_cards(bank, [CLIMATE])
    api.output = dict(HELP, hook="")
    html = open_help(client).text
    assert HELP["why"] in html and "Memory hook" not in html
    # And a one-point card gets no hook even if the model offers one.
    add_cards(bank, [dict(CLIMATE, id="card-climate-2", prompt="Define weather.")])
    api.output = HELP
    assert "Memory hook" not in open_help(client, "card-climate-2").text
    # A multi-point card keeps its hook.
    add_cards(bank, [RAIN])
    assert "Memory hook" in open_help(client, "card-rain").text


def test_study_page_has_the_button_and_the_recall_page_does_not(client, bank, api):
    add_cards(bank, [CLIMATE])
    base = start(client)
    study = client.get(f"{base}/study/0").text
    assert "Help me remember" in study and f'name="set_id" value="{base.rsplit("/", 1)[1]}"' in study
    assert "Help me remember" not in client.get(f"{base}/recall/0").text
    assert "Help me remember" in start_exam_study(client)


def start_exam_study(client) -> str:
    base = start(client, exam="on")
    return client.get(f"{base}/study/0").text


def test_real_call_uses_the_light_model_and_logs_tokens(bank):
    add_cards(bank, [RAIN])
    fake = FakeClient([HELP])
    card = get_card(bank, "card-rain")
    assert revise_help.generate(bank, card, client=fake)["why"] == HELP["why"]
    request = fake.requests[0]
    assert "<card>" in request["messages"][0]["content"][0]["text"]
    assert "Water evaporates from the sea." in request["messages"][0]["content"][0]["text"]
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    row = bank.execute("SELECT * FROM api_calls WHERE purpose = 'card_help'").fetchone()
    assert row["question_id"] == "card-rain" and row["input_tokens"] == 2000
    assert revise_help.generate(bank, card, client=FakeClient([])) is not None  # cached: no call


# ---- with_help ---------------------------------------------------------------------------

def attempt(conn, card_id, now):
    return revise.record_attempt(conn, get_card(conn, card_id), method="paper", covered_ids=["P1"], units_total=1,
                                 settings=settings(), now=now)


def test_with_help_is_set_when_help_was_opened_in_the_last_24_hours(bank):
    add_cards(bank, [CLIMATE, RAIN])
    card = get_card(bank, "card-climate")
    attempt(bank, "card-climate", NOW - timedelta(days=3))
    assert with_help_flags(bank) == [0]  # no help yet
    revise_help.record_view(bank, card, set_id=None, now=NOW - timedelta(hours=23))
    attempt(bank, "card-climate", NOW)
    assert with_help_flags(bank)[-1] == 1  # opened on the standalone page, 23 hours earlier
    attempt(bank, "card-climate", NOW + timedelta(hours=2))
    assert with_help_flags(bank)[-1] == 0  # 25 hours since it was opened
    attempt(bank, "card-rain", NOW)
    assert with_help_flags(bank)[-1] == 0  # help for another card doesn't count


def test_with_help_counts_help_from_an_earlier_card_version_and_from_a_set(bank):
    add_cards(bank, [CLIMATE])
    old = get_card(bank, "card-climate")
    set_id = revise.create_set(bank, card_ids=["card-climate"], subject="geography", now=NOW)
    revise_help.record_view(bank, old, set_id=set_id, now=NOW - timedelta(hours=1))
    import_cards(bank, [dict(CLIMATE, prompt="Define climate briefly.")])  # now version 2
    attempt(bank, "card-climate", NOW)
    assert with_help_flags(bank) == [1]


def test_with_help_does_not_change_box_movement(bank):
    add_cards(bank, [CLIMATE])
    card = get_card(bank, "card-climate")
    revise_help.record_view(bank, card, now=NOW - timedelta(minutes=5))
    attempt(bank, "card-climate", NOW)
    row = bank.execute("SELECT box_before, box_after, with_help FROM recall_attempts").fetchone()
    assert (row["box_before"], row["box_after"], row["with_help"]) == (1, 2, 1)


def test_attempt_made_through_the_pages_after_opening_help_is_marked(client, bank, api):
    add_cards(bank, [CLIMATE])
    base = start(client)
    open_help(client, set_id=base.rsplit("/", 1)[1])
    client.post(f"{base}/tick/0", data={"unit": "P1"})
    assert with_help_flags(bank) == [1]


# ---- Follow-ups ----------------------------------------------------------------------------

def test_followup_is_limited_to_three_per_card_per_set(client, bank, api):
    add_cards(bank, [CLIMATE, RAIN])
    set_id = str(revise.create_set(bank, card_ids=["card-climate", "card-rain"], subject="geography"))
    other_set = str(revise.create_set(bank, card_ids=["card-climate"], subject="geography"))
    open_help(client, set_id=set_id)
    open_help(client, set_id=other_set)
    ask = lambda s: client.post("/revise/cards/card-climate/help/followup", data={"set_id": s, "question": "why?"})
    for _ in range(3):
        assert "Think of it as the usual pattern." in ask(set_id).text
    html = ask(set_id).text
    assert "That&#39;s all the questions for this card." in html or "That's all the questions" in html
    assert "Think of it as" not in html
    assert len([c for c in api if c["purpose"] == "card_help_followup"]) == 3
    assert "Think of it as the usual pattern." in ask(other_set).text  # a different set has its own allowance


def test_followup_on_the_standalone_page_is_limited_to_three_per_card_per_day(bank, api):
    add_cards(bank, [CLIMATE, RAIN])
    card = get_card(bank, "card-climate")
    revise_help.generate(bank, card)
    revise_help.record_view(bank, card, now=NOW - timedelta(hours=30))
    for _ in range(3):
        assert revise_help.followup(bank, card, "why?", now=NOW - timedelta(hours=29))
    # Yesterday's questions don't count today.
    revise_help.record_view(bank, card, now=NOW)
    assert revise_help.followups_left(bank, "card-climate", None, NOW) == 3
    for _ in range(3):
        assert revise_help.followup(bank, card, "why?", now=NOW)
    with pytest.raises(revise_help.FollowupLimit):
        revise_help.followup(bank, card, "why?", now=NOW)
    assert revise_help.followups_left(bank, "card-rain", None, NOW) == 3  # another card has its own


def test_failed_followup_does_not_use_up_an_allowance_and_empty_question_is_ignored(client, bank, api):
    add_cards(bank, [CLIMATE])
    open_help(client)
    api.fail = True
    html = client.post("/revise/cards/card-climate/help/followup", data={"question": "why?"}).text
    assert "Couldn&#39;t get an answer" in html or "Couldn't get an answer" in html
    assert revise_help.followups_left(bank, "card-climate", None) == 3
    api.fail = False
    calls_before = len(api)
    assert "Type your question first." in client.post("/revise/cards/card-climate/help/followup",
                                                      data={"question": "  "}).text
    assert len(api) == calls_before


def test_followup_is_not_stored_and_uses_the_card_and_cached_help(bank, api):
    add_cards(bank, [CLIMATE])
    card = get_card(bank, "card-climate")
    revise_help.generate(bank, card)
    assert revise_help.followup(bank, card, "I don't get the average part") == "Think of it as the usual pattern."
    content = api[-1]["content"]
    assert "<help_shown>" in content[0]["text"] and HELP["why"] in content[0]["text"]
    assert content[1]["text"] == "Her question: I don't get the average part"
    assert bank.execute("SELECT COUNT(*) FROM card_help").fetchone()[0] == 1


# ---- Flags and the parent page -----------------------------------------------------------

def test_flag_goes_to_the_parent_page_with_the_card_and_help_text(client, bank, api):
    add_cards(bank, [CLIMATE])
    open_help(client)
    r = client.post("/revise/cards/card-climate/help/flag", data={"note": "The slip is not a slip"})
    assert r.status_code == 200 and "a parent will check it" in r.text
    assert client.get("/parent/card-help", follow_redirects=False).status_code == 303  # PIN needed
    parent_login(client)
    html = client.get("/parent/card-help").text
    assert "Define climate." in html and HELP["common_slip"] in html
    assert "Flagged by student: The slip is not a slip" in html
    client.post("/parent/card-help/card-climate/checked", data={})
    assert "Flagged by" not in client.get("/parent/card-help").text


def test_parent_edit_survives_regenerate_and_is_replaced_by_a_new_card_version(client, bank, api):
    add_cards(bank, [CLIMATE, RAIN])
    open_help(client)
    open_help(client, "card-rain")
    parent_login(client)
    edit = dict(HELP, why="Climate is the long-run pattern for a place.")
    r = client.post("/parent/card-help/card-climate/edit", data=edit, follow_redirects=False)
    assert r.status_code == 303
    row = bank.execute("SELECT * FROM card_help WHERE card_id = 'card-climate'").fetchone()
    assert row["edited_by_parent"] == 1
    # Regenerate one card: an edited card keeps its text, an unedited card is rewritten.
    api.output = dict(HELP, why="Written again.")
    calls = len(api)
    html = client.post("/parent/card-help/card-climate/regenerate", data={}).text
    assert "kept" in html and len(api) == calls
    client.post("/parent/card-help/card-rain/regenerate", data={})
    assert len(api) == calls + 1
    # Regenerate everything from the code too (the same rule): the edit stays.
    card = get_card(bank, "card-climate")
    assert revise_help.regenerate(bank, card)["why"] == "Climate is the long-run pattern for a place."
    assert revise_help.get(bank, "card-rain", 1)["why"] == "Written again."
    assert "Edited by a parent" in client.get("/parent/card-help").text
    # A new card version generates fresh help, not the edit.
    import_cards(bank, [dict(CLIMATE, prompt="Define climate in one line.")])
    open_help(client)
    assert revise_help.get(bank, "card-climate", 2)["why"] == "Written again."
    assert revise_help.get(bank, "card-climate", 1)["edited_by_parent"] is True


def test_parent_edit_needs_the_three_main_parts(client, bank, api):
    add_cards(bank, [CLIMATE])
    parent_login(client)
    r = client.post("/parent/card-help/card-climate/edit", data=dict(HELP, marks_in=""))
    assert r.status_code == 422 and "can't be empty" in r.text.replace("&#39;", "'")
    assert bank.execute("SELECT COUNT(*) FROM card_help").fetchone()[0] == 0
    r = client.post("/parent/card-help/card-climate/edit", data=dict(HELP, hook=""), follow_redirects=False)
    assert r.status_code == 303  # an empty hook is fine


def test_parent_actions_need_the_pin(client, bank, api):
    add_cards(bank, [CLIMATE])
    assert client.post("/parent/card-help/card-climate/edit", data=HELP).status_code == 403
    assert client.post("/parent/card-help/card-climate/regenerate", data={}).status_code == 403


# ---- Command line ----------------------------------------------------------------------------

def test_command_line_writes_only_the_missing_help(bank, api, monkeypatch, capsys):
    add_cards(bank, [CLIMATE, RAIN])
    revise_help.generate(bank, get_card(bank, "card-climate"))  # already has help
    api.clear()

    class Keep:  # the command closes its connection; the test still needs this one
        def __getattr__(self, name):
            return getattr(bank, name)

        def close(self):
            pass

    monkeypatch.setattr("app.revise_help.init_db", lambda: None)
    monkeypatch.setattr("app.revise_help.connect", lambda: Keep())
    assert revise_help.cards_for(bank, "geo-intro") == ["card-climate", "card-rain"]
    assert revise_help.main(["no-such-topic"]) == 1
    assert revise_help.main(["geo-intro"]) == 0
    assert "2 cards: 1 written, 1 already had help, 0 failed" in capsys.readouterr().out
    assert len(api) == 1 and api[0]["question_id"] == "card-rain"
    assert revise_help.main(["geo-intro"]) == 0 and len(api) == 1  # nothing left to write


def test_schema_has_the_help_tables(conn):
    for name in ("card_help", "help_views", "help_flags"):
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    assert "with_help" in [r["name"] for r in conn.execute("PRAGMA table_info(recall_attempts)")]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
