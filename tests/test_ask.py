""""Ask about this" (docs/STUDENT_UI_SPEC.md section 7): the scope payload, the code guards, "with help", the
Singapore-midnight daily cap, saved conversations and the parent page. Made-up questions and cards and a fake model
only: no API calls."""

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from app import app_settings, ask, db, practice
from app.config import get_settings
from app.importer import get_question
from app.llm import LLMError
from conftest import PARENT_PIN
from revise_helpers import CLIMATE, RAIN, add_cards, make_card, point

POINTS_Q = "sci-test-points-q1"
MCQ_TOPIC = "sci-07-particulate-nature"
GOOD_ANSWER = "The balloon expands because the particles gain energy and move further apart."
KEY = "abcdef123456"

GAS_CARD = make_card("card-gas", "Describe what happens to a gas when it is heated.",
                     {"format": "bullets", "points": [point("The balloon expands as particles gain energy.", ["expands"])]},
                     topic=MCQ_TOPIC, subject="science", command="describe")
FORM = {"cards_per_set": "4", "recall_method": "type", "recall_timing": "after_set",
        "interval_1": "1", "interval_2": "2", "interval_3": "4", "interval_4": "8", "interval_5": "16",
        "known_threshold": "80"}


@pytest.fixture
def api(client, monkeypatch):
    """The fake model (after the client fixture's own no-API stand-in). Returns the list of calls;
    set api.fail to fail every call, api.reply to change what it says."""
    class Calls(list):
        fail = False
        reply = "Think about what heat does to the particles."

    made = Calls()

    def fake(conn, **kwargs):
        made.append(kwargs)
        if made.fail:
            raise LLMError("api_error", "503: overloaded")
        return {"reply": made.reply}, {"model": "fake-light", "api_call_id": None,
                                       "usage": {"input_tokens": 10, "output_tokens": 5}}

    monkeypatch.setattr("app.ask.structured_call", fake)
    return made


def turn_on(conn, **changes):
    app_settings.save(conn, {**app_settings.get_all(conn), "ask_enabled": True, **changes})


def say(client, text="Why does it get bigger?", url=f"/ask/question/{POINTS_Q}", key=KEY, **extra):
    return client.post(url, data={"key": key, "text": text, **extra})


def stage_of(call) -> str:
    """The stage the model was told (the system prompt also names both, so look at the scope block itself)."""
    return call["content"][0]["text"].split("</stage>")[0].removeprefix("<stage>")


def payload(call) -> str:
    """Everything the model was sent: the system prompt and every content block."""
    return " ".join(b["text"] for b in call["system"] + call["content"] if b["type"] == "text")


def submit(client, answer=GOOD_ANSWER):
    r = client.post(f"/q/{POINTS_Q}/answer", data={"answer": answer, "shown_at": ""}, follow_redirects=False)
    return r.headers["location"]


def attempt_id_of(location: str) -> int:
    return int(location.split("/attempts/")[1].split("?")[0])


def parent_login(client):
    client.post("/parent/login", data={"pin": PARENT_PIN, "next": "/"}, follow_redirects=False)


def utc(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


def with_help(conn) -> list:
    return [r["with_help"] for r in conn.execute("SELECT with_help FROM attempts ORDER BY id")]


# ---- Off by default, hidden, refused --------------------------------------------------------------------

def test_off_by_default_and_the_panel_is_hidden_everywhere(client, bank, api):
    assert app_settings.get_all(bank)["ask_enabled"] is False
    assert app_settings.get_all(bank)["ask_daily_cap"] == 40
    add_cards(bank, [CLIMATE])
    # (the name "ask-panel" is also in the page's CSS, so look for the panel's own id)
    assert 'id="ask-panel"' not in client.get(f"/q/{POINTS_Q}").text
    assert 'id="ask-panel"' not in client.get(submit(client)).text  # the feedback page
    assert 'id="ask-panel"' not in client.get("/revise/cards/card-climate").text


def test_off_the_routes_refuse_and_call_nothing(client, bank, api):
    assert say(client).status_code == 403
    location = submit(client)
    assert say(client, url=f"/ask/attempt/{attempt_id_of(location)}").status_code == 403
    add_cards(bank, [CLIMATE])
    assert say(client, url="/ask/card/card-climate").status_code == 403
    assert len(api) == 0 and bank.execute("SELECT COUNT(*) FROM discussions").fetchone()[0] == 0
    assert practice.unclaimed_tiers(bank, POINTS_Q) == []


def test_on_the_panel_shows_on_the_question_feedback_and_revise_pages(client, bank, api):
    turn_on(bank)
    add_cards(bank, [CLIMATE, RAIN])
    assert 'id="ask-panel"' in client.get(f"/q/{POINTS_Q}").text
    location = submit(client)
    assert f'hx-post="/ask/attempt/{attempt_id_of(location)}"' in client.get(location).text
    assert 'hx-post="/ask/card/card-climate"' in client.get("/revise/cards/card-climate").text  # standalone card page
    base = start_set(client)
    assert "ask-panel" in client.get(f"{base}/study/0").text
    client.post(f"{base}/tick/0", data={"unit": "P1"})
    assert "ask-panel" in client.get(f"{base}/result/0").text


def test_each_page_load_starts_a_new_empty_conversation(client, bank, api):
    turn_on(bank)
    first = client.get(f"/q/{POINTS_Q}").text
    second = client.get(f"/q/{POINTS_Q}").text
    key = lambda page: page.split('name="key" value="')[1].split('"')[0]  # noqa: E731
    assert key(first) != key(second)
    assert "Tutor:" not in second


def test_an_unknown_question_attempt_or_card_is_a_404_and_bad_keys_a_400(client, bank, api):
    turn_on(bank)
    assert say(client, url="/ask/question/no-such").status_code == 404
    assert say(client, url="/ask/attempt/999").status_code == 404
    assert say(client, url="/ask/card/no-such").status_code == 404
    assert say(client, key="!!").status_code == 400
    assert len(api) == 0


def test_a_conversation_key_cannot_be_reused_on_another_question(client, bank, api):
    turn_on(bank)
    assert say(client).status_code == 200
    assert say(client, url="/ask/question/his-test-lorms-q1").status_code == 400
    assert len(api) == 1


# ---- The scope payload ---------------------------------------------------------------------------------

def test_before_submitting_the_payload_has_no_mark_scheme_and_only_card_prompts_and_key_terms(client, bank, api):
    turn_on(bank)
    add_cards(bank, [GAS_CARD])
    say(client)
    text = payload(api[0])
    assert stage_of(api[0]) == "before_submit" and "State what happens to the balloon and explain why." in text
    assert "A balloon is left in a hot car." in text
    # the topic's card: its prompt and key terms, but not its answer points
    assert "Describe what happens to a gas when it is heated." in text and "expands" in text
    assert "The balloon expands as particles gain energy." not in text and '"answer"' not in text
    # never the mark scheme, nor anything from the full-marks answer
    for secret in ("Particles gain energy and move further apart", "MP1", "MP2", "mark_points", "rejects", "requires"):
        assert secret not in text
    assert len(api[0]["content"]) == 2  # the scope, then the conversation: nothing else


def test_after_submitting_the_payload_adds_her_answer_the_marking_the_explanation_and_the_full_cards(client, bank, api):
    turn_on(bank)
    add_cards(bank, [GAS_CARD])
    location = submit(client, GOOD_ANSWER)
    attempt_id = attempt_id_of(location)
    bank.execute("UPDATE attempts SET tutor_json = ? WHERE id = ?",
                 (json.dumps({"coaching": "COACHING-TEXT-123"}), attempt_id))
    bank.commit()
    say(client, url=f"/ask/attempt/{attempt_id}")
    text = payload(api[0])
    assert stage_of(api[0]) == "after_submit" and GOOD_ANSWER in text and "marks_awarded" in text
    assert "COACHING-TEXT-123" in text
    assert "The balloon expands as particles gain energy." in text  # the card in full now
    assert "Particles gain energy and move further apart" in text   # it is part of the marking she has been shown


def test_a_card_before_recall_sends_this_card_in_full_and_the_other_cards_light(client, bank, api):
    turn_on(bank)
    add_cards(bank, [CLIMATE, RAIN])
    base = start_set(client)
    here, other = ordered_cards(bank, base)
    say(client, url=f"/ask/card/{here['id']}", set_id=set_id_of(base))
    text = payload(api[0])
    assert stage_of(api[0]) == "before_submit" and here["prompt"] in text and ANSWER_TEXT[here["id"]] in text
    assert other["prompt"] in text and KEY_TERM[other["id"]] in text and ANSWER_TEXT[other["id"]] not in text


def test_a_card_after_recall_sends_the_other_cards_in_full(client, bank, api):
    turn_on(bank)
    add_cards(bank, [CLIMATE, RAIN])
    base = start_set(client)
    here, other = ordered_cards(bank, base)
    client.post(f"{base}/tick/0", data={"unit": "P1"})
    say(client, url=f"/ask/card/{here['id']}", set_id=set_id_of(base))
    text = payload(api[0])
    assert stage_of(api[0]) == "after_submit" and ANSWER_TEXT[other["id"]] in text


def test_the_system_prompt_is_cached_and_the_light_model_is_used(client, bank, api):
    turn_on(bank)
    say(client)
    call = api[0]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"} and "80 words" in call["system"][0]["text"]
    assert call["model"] == get_settings().light_model  # LIGHT_MODEL comes from config, never hard-coded
    assert call["purpose"] == "ask_about_this"


ANSWER_TEXT = {"card-climate": "Climate is the average weather of a place over a long period.",
               "card-rain": "Water evaporates from the sea."}
KEY_TERM = {"card-climate": "average weather", "card-rain": "evaporates"}


def start_set(client) -> str:
    r = client.post("/revise/start", data={"subject": "geography"}, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"].rsplit("/study/0", 1)[0]


def set_id_of(base: str) -> int:
    return int(base.rsplit("/", 1)[1])


def ordered_cards(conn, base):
    from app import revise
    from app.revision_cards import get_card
    ids = revise.get_set(conn, set_id_of(base))["card_ids"]
    return get_card(conn, ids[0]), get_card(conn, ids[1])


# ---- Code guards -------------------------------------------------------------------------------------

def test_at_most_six_of_her_messages_per_conversation(client, bank, api):
    turn_on(bank)
    for _ in range(6):
        assert "Tutor:" in say(client).text
    seventh = say(client)
    assert "all the questions for this one" in seventh.text and 'name="text"' not in seventh.text  # (the ' is escaped)
    assert len(api) == 6
    assert "all the questions for this one" not in say(client, key="anotherkey12").text  # a new conversation


def test_a_message_over_300_characters_is_refused_without_a_call_and_kept_in_the_box(client, bank, api):
    turn_on(bank)
    long = "x" * 301
    r = say(client, long)
    assert "300 characters" in r.text and long in r.text and len(api) == 0
    assert "Tutor:" in say(client, "y" * 300).text and len(api) == 1


def test_an_empty_message_is_refused_without_a_call(client, bank, api):
    turn_on(bank)
    assert "Type your question first." in say(client, "   ").text and len(api) == 0


def test_the_daily_cap_applies_across_conversations_and_is_a_parent_setting(client, bank, api):
    turn_on(bank, ask_daily_cap=3)
    for n in range(3):
        assert "Tutor:" in say(client, key=f"conversation{n}").text
    refused = say(client, key="conversation9")
    assert "all the chat for today" in refused.text and len(api) == 3


def test_a_failed_model_call_saves_nothing_uses_no_allowance_and_keeps_her_text(client, bank, api):
    turn_on(bank)
    api.fail = True
    r = say(client, "Please help")
    assert "get an answer, try again" in r.text and "Please help" in r.text  # (the ' in Couldn't is escaped)
    assert bank.execute("SELECT COUNT(*) FROM discussions").fetchone()[0] == 0
    assert practice.unclaimed_tiers(bank, POINTS_Q) == [] and ask.sent_today(bank) == 0
    api.fail = False
    assert "Tutor:" in say(client, "Please help").text


def test_a_long_reply_is_trimmed_to_80_words(client, bank, api):
    turn_on(bank)
    api.reply = " ".join(["word"] * 120)
    reply = say(client).text
    stored = json.loads(bank.execute("SELECT messages_json FROM discussions").fetchone()[0])[1]["text"]
    assert len(stored.rstrip("…").split()) == 80 and stored.endswith("…") and "word…" in reply


# ---- The daily cap counts from midnight in Singapore ----------------------------------------------------

def test_the_day_starts_at_midnight_singapore_time():
    assert ask.day_start(utc(20, 15, 59)) == "2026-10-19T16:00:00Z"   # 23:59 on the 20th in Singapore
    assert ask.day_start(utc(20, 16, 1)) == "2026-10-20T16:00:00Z"    # 00:01 on the 21st in Singapore


def test_the_cap_resets_at_singapore_midnight_not_utc_midnight(bank, api):
    turn_on(bank, ask_daily_cap=2)
    scope = ask.question_scope(bank, get_question(bank, POINTS_Q))
    assert ask.send(bank, scope, "kk1", "one", now=utc(20, 15, 58))["notice"] == ""   # 23:58 SGT, 20 Oct
    assert ask.send(bank, scope, "kk2", "two", now=utc(20, 15, 59))["notice"] == ""   # 23:59 SGT, 20 Oct
    assert "all the chat for today" in ask.send(bank, scope, "kk3", "three", now=utc(20, 15, 59))["notice"]
    # Two minutes later it is 21 Oct in Singapore (still 20 Oct in UTC): a fresh day.
    assert ask.send(bank, scope, "kk3", "three", now=utc(20, 16, 1))["notice"] == ""
    assert ask.send(bank, scope, "kk4", "four", now=utc(20, 16, 2))["notice"] == ""
    assert "all the chat for today" in ask.send(bank, scope, "kk5", "five", now=utc(20, 16, 3))["notice"]
    assert len(api) == 4


def test_messages_on_two_utc_dates_but_one_singapore_day_count_together(bank, api):
    turn_on(bank, ask_daily_cap=2)
    scope = ask.question_scope(bank, get_question(bank, POINTS_Q))
    assert ask.send(bank, scope, "kk1", "one", now=utc(19, 17, 0))["notice"] == ""    # 01:00 SGT, 20 Oct (19 Oct UTC)
    assert ask.send(bank, scope, "kk2", "two", now=utc(20, 14, 0))["notice"] == ""    # 22:00 SGT, 20 Oct
    assert "all the chat for today" in ask.send(bank, scope, "kk3", "three", now=utc(20, 15, 0))["notice"]
    assert ask.sent_today(bank, utc(20, 15, 0)) == 2 and len(api) == 2


# ---- "With help" ---------------------------------------------------------------------------------------

def test_asking_before_submitting_marks_the_attempt_with_help_and_the_score_stands(client, bank, api):
    turn_on(bank)
    say(client)
    say(client, "And why?")
    rows = bank.execute("SELECT tier FROM hint_uses").fetchall()
    assert [r["tier"] for r in rows] == [4]  # once per conversation
    attempt_id = attempt_id_of(submit(client))
    row = bank.execute("SELECT marks_awarded, max_marks, with_help FROM attempts").fetchone()
    assert (row["marks_awarded"], row["max_marks"], row["with_help"]) == (2, 2, 1)
    assert practice.hint_tiers(bank, attempt_id) == [4]


def test_asking_after_submitting_does_not_mark_help(client, bank, api):
    turn_on(bank)
    attempt_id = attempt_id_of(submit(client))
    say(client, url=f"/ask/attempt/{attempt_id}")
    assert with_help(bank) == [0] and bank.execute("SELECT COUNT(*) FROM hint_uses").fetchone()[0] == 0
    assert bank.execute("SELECT stage FROM discussions").fetchone()["stage"] == "after"


def test_a_question_chat_does_not_mark_another_question_with_help(client, bank, api):
    turn_on(bank)
    say(client, url="/ask/question/his-test-lorms-q1")
    submit(client)
    assert with_help(bank) == [0]


def test_asking_about_a_card_before_recalling_marks_the_recall_with_help(client, bank, api):
    turn_on(bank)
    add_cards(bank, [CLIMATE, RAIN])
    base = start_set(client)
    here, _ = ordered_cards(bank, base)
    say(client, url=f"/ask/card/{here['id']}", set_id=set_id_of(base))
    client.post(f"{base}/tick/0", data={"unit": "P1"})
    assert [r["with_help"] for r in bank.execute("SELECT with_help FROM recall_attempts")] == [1]


def test_a_card_recall_without_asking_is_not_marked_and_asking_after_recall_adds_no_help(client, bank, api):
    turn_on(bank)
    add_cards(bank, [CLIMATE, RAIN])
    base = start_set(client)
    here, _ = ordered_cards(bank, base)
    client.post(f"{base}/tick/0", data={"unit": "P1"})
    say(client, url=f"/ask/card/{here['id']}", set_id=set_id_of(base))
    assert [r["with_help"] for r in bank.execute("SELECT with_help FROM recall_attempts")] == [0]
    assert bank.execute("SELECT COUNT(*) FROM help_views").fetchone()[0] == 0


# ---- Saved conversations and the parent page --------------------------------------------------------------

def test_every_conversation_is_saved_with_its_messages_and_tokens(client, bank, api):
    turn_on(bank)
    say(client, "First")
    say(client, "Second")
    rows = bank.execute("SELECT * FROM discussions").fetchall()
    assert len(rows) == 1
    row = rows[0]
    messages = json.loads(row["messages_json"])
    assert [m["role"] for m in messages] == ["student", "tutor", "student", "tutor"]
    assert messages[0]["text"] == "First" and messages[1]["text"] == api.reply
    assert (row["kind"], row["scope_id"], row["stage"], row["student_messages"], row["tokens"]) == \
        ("question", POINTS_Q, "before", 2, 30)
    # the second call sees the first exchange
    assert "Student: First" in payload(api[1]) and "Tutor: " + api.reply in payload(api[1])


def test_the_parent_page_needs_the_pin_and_lists_newest_first_in_singapore_time(client, bank, api):
    turn_on(bank)
    scope = ask.question_scope(bank, get_question(bank, POINTS_Q))
    ask.send(bank, scope, "kk1", "OLDER QUESTION", now=utc(20, 15, 59))
    ask.send(bank, scope, "kk2", "NEWER QUESTION", now=utc(20, 16, 1))
    r = client.get("/parent/discussions", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/parent/login")
    parent_login(client)
    page = client.get("/parent/discussions").text
    assert page.index("NEWER QUESTION") < page.index("OLDER QUESTION")
    assert "21 Oct 2026, 00:01" in page and "20 Oct 2026, 23:59" in page
    assert "before submitting: with help" in page and "State what happens to the balloon" in page


def test_the_student_cannot_reach_the_parent_page_without_the_pin(client, bank, api):
    turn_on(bank)
    say(client, "SECRET CHAT")
    r = client.get("/parent/discussions", follow_redirects=True)
    assert "SECRET CHAT" not in r.text


# ---- Parent settings -----------------------------------------------------------------------------------

def test_the_parent_can_switch_it_on_and_set_the_daily_cap(client, bank, api):
    parent_login(client)
    page = client.get("/parent/settings").text
    assert 'name="ask_enabled"' in page and 'name="ask_daily_cap"' in page
    assert client.post("/parent/settings", data={**FORM, "ask_enabled": "on", "ask_daily_cap": "25"}).status_code == 200
    s = app_settings.get_all(bank)
    assert s["ask_enabled"] is True and s["ask_daily_cap"] == 25
    assert 'id="ask-panel"' in client.get(f"/q/{POINTS_Q}").text
    assert client.post("/parent/settings", data=FORM).status_code == 200
    assert app_settings.get_all(bank)["ask_enabled"] is False


@pytest.mark.parametrize("bad", ["0", "201", "lots", "-3"])
def test_a_bad_daily_cap_is_refused(client, bank, bad):
    parent_login(client)
    r = client.post("/parent/settings", data={**FORM, "ask_enabled": "on", "ask_daily_cap": bad})
    assert r.status_code == 422 and "daily limit" in r.text
    assert app_settings.get_all(bank)["ask_daily_cap"] == 40


# ---- Database ----------------------------------------------------------------------------------------

def test_hint_uses_accepts_tier_4_and_nothing_higher(conn):
    conn.execute("INSERT INTO hint_uses (question_id, tier, created_at) VALUES ('q', 4, 'now')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO hint_uses (question_id, tier, created_at) VALUES ('q', 5, 'now')")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'discussions'").fetchone()


def test_upgrading_a_version_13_database_keeps_its_hint_rows(tmp_path):
    path = tmp_path / "old.db"
    conn = db.connect(path)
    conn.executescript(db.SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 1")
    for n in range(2, 14):
        conn.executescript(getattr(db, f"SCHEMA_V{n}_FILE").read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("INSERT INTO hint_uses (question_id, tier, created_at) VALUES ('q-old', 2, '2026-10-01T00:00:00Z')")
    conn.commit()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
    conn.close()
    db.init_db(path)
    conn = db.connect(path)
    rows = conn.execute("SELECT question_id, tier FROM hint_uses").fetchall()
    assert [(r["question_id"], r["tier"]) for r in rows] == [("q-old", 2)]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
    conn.close()
