""""Help me" on questions (docs/STUDENT_UI_SPEC.md section 6): tiers, caching, answer-leak guard, "with help",
parent settings. Made-up questions and cards and a fake API only."""

import pytest

from app import app_settings, practice, question_help
from app.importer import import_questions
from app.llm import LLMError
from conftest import PARENT_PIN
from revise_helpers import add_cards, make_card, point
from test_mcq import MCQ

POINTS_Q = "sci-test-points-q1"
TOPIC = "sci-07-particulate-nature"
EXPLAIN = {"rephrase": "You are asked what the balloon does and why.",
           "command_word": "Explain means say why or how it happens.",
           "marks": "Two marks: make two linked points.",
           "topic_part": "How heat changes a gas."}
NUDGE = {"nudge": "Think about what the heat does to the gas inside."}

GAS_CARD = make_card("card-gas", "Describe what happens to a gas when it is heated.",
                     {"format": "bullets", "points": [point("The balloon expands as particles gain energy.", ["expands"])]},
                     topic=TOPIC, subject="science", command="describe")
OTHER_CARD = make_card("card-other", "Name the parts of a plant cell.",
                       {"format": "bullets", "points": [point("Cell wall, vacuole and chloroplast.", ["vacuole"])]},
                       topic=TOPIC, subject="science", command="name")
FORM = {"cards_per_set": "4", "recall_method": "type", "recall_timing": "after_set",
        "interval_1": "1", "interval_2": "2", "interval_3": "4", "interval_4": "8", "interval_5": "16",
        "known_threshold": "80"}


@pytest.fixture
def api(client, monkeypatch):
    """Replace the model call (after the client fixture's own no-API stand-in). Returns the list of calls;
    set api.fail to fail every call, api.replies to change a tier's reply."""
    class Calls(list):
        fail = False
        replies = {1: EXPLAIN, 2: NUDGE}

    made = Calls()

    def fake(conn, **kwargs):
        made.append(kwargs)
        if made.fail:
            raise LLMError("api_error", "503: overloaded")
        tier = int(kwargs["purpose"][-1])
        return dict(made.replies[tier]), {"model": "fake-light", "api_call_id": None}

    monkeypatch.setattr("app.question_help.structured_call", fake)
    return made


def open_help(client, question_id=POINTS_Q):
    return client.post(f"/q/{question_id}/help")


def submit(client, answer="The balloon expands.", question_id=POINTS_Q):
    return client.post(f"/q/{question_id}/answer", data={"answer": answer, "shown_at": ""}, follow_redirects=False)


def prompt_text(call) -> str:
    return " ".join(b["text"] for b in call["content"] if b["type"] == "text")


def parent_login(client):
    client.post("/parent/login", data={"pin": PARENT_PIN, "next": "/"}, follow_redirects=False)


def with_help(conn) -> list:
    return [r["with_help"] for r in conn.execute("SELECT with_help FROM attempts ORDER BY id")]


# ---- Tiers, one at a time -----------------------------------------------------------------

def test_tier_one_explains_the_question_and_offers_the_nudge(client, bank, api):
    page = open_help(client).text
    for part in EXPLAIN.values():
        assert part in page
    assert "Give me a nudge" in page and NUDGE["nudge"] not in page
    assert [c["purpose"] for c in api] == ["question_help_1"]


def test_the_tiers_come_in_order_ending_with_the_related_card(client, bank, api):
    add_cards(bank, [GAS_CARD])
    assert "Give me a nudge" in open_help(client).text
    second = open_help(client).text
    assert NUDGE["nudge"] in second and EXPLAIN["rephrase"] in second and "Show me the card" in second
    third = open_help(client).text
    assert "The related card" in third and "Describe what happens to a gas when it is heated." in third
    assert "Show me the card" not in third and "Give me a nudge" not in third  # nothing after the card
    assert practice.unclaimed_tiers(bank, POINTS_Q) == [1, 2, 3]


def test_without_a_card_in_the_topic_there_is_no_tier_three(client, bank, api):
    open_help(client)
    page = open_help(client).text
    assert NUDGE["nudge"] in page and "Show me the card" not in page


def test_tier_two_off_goes_from_the_explanation_to_the_card(client, bank, api):
    add_cards(bank, [GAS_CARD])
    app_settings.save(bank, {**app_settings.get_all(bank), "help_tier2": False})
    first = open_help(client).text
    assert "Show me the card" in first and "Give me a nudge" not in first
    second = open_help(client).text
    assert "The related card" in second and NUDGE["nudge"] not in second
    assert [c["purpose"] for c in api] == ["question_help_1"]  # the nudge was never written


def test_an_mcq_gets_no_nudge(client, bank, api):
    import_questions(bank, [MCQ])
    page = open_help(client, MCQ["id"]).text
    assert "Give me a nudge" not in page and EXPLAIN["rephrase"] in page


def test_the_related_card_is_the_best_text_match_in_the_topic(bank):
    add_cards(bank, [OTHER_CARD, GAS_CARD])  # the better match is second in book order
    question = import_and_get(bank)
    assert question_help.related_card(bank, question)["id"] == "card-gas"
    assert question_help.related_card(bank, {**question, "topic": "sci-01-scientific-endeavour"}) is None


def import_and_get(conn):
    from app.importer import get_question
    return get_question(conn, POINTS_Q)


# ---- Written once, never from the mark scheme (tier 1) --------------------------------------------

def test_help_is_written_once_per_question_and_reused(client, bank, api):
    open_help(client)
    open_help(client)
    assert [c["purpose"] for c in api] == ["question_help_1", "question_help_2"]
    submit(client)  # her attempt takes the help she used, so the next open starts at tier 1 again, from the database
    open_help(client)
    open_help(client)
    assert len(api) == 2
    assert bank.execute("SELECT COUNT(*) FROM question_help").fetchone()[0] == 2


def test_tier_one_never_sees_the_mark_scheme_but_tier_two_does(client, bank, api):
    open_help(client)
    open_help(client)
    one, two = prompt_text(api[0]), prompt_text(api[1])
    assert "mark_scheme" not in one and "Particles gain energy" not in one and "expands" not in one
    assert "State what happens to the balloon" in one and "A balloon is left in a hot car." in one
    assert "Particles gain energy and move further apart" in two
    assert "confidential" in api[1]["system"][0]["text"]


def test_both_tiers_use_the_light_model(client, bank, api):
    from app.config import get_settings
    open_help(client)
    open_help(client)
    assert [c["model"] for c in api] == [get_settings().light_model] * 2


# ---- The answer must not be given away ---------------------------------------------------------

def test_a_nudge_that_uses_a_hidden_key_term_is_thrown_away(client, bank, api):
    api.replies = {1: EXPLAIN, 2: {"nudge": "The particles move further apart."}}
    open_help(client)
    page = open_help(client).text
    assert "try again" in page and "further apart" not in page
    assert bank.execute("SELECT COUNT(*) FROM question_help WHERE tier = 2").fetchone()[0] == 0
    assert practice.unclaimed_tiers(bank, POINTS_Q) == [1]  # nothing recorded for the failed tier


def test_a_key_term_the_question_already_uses_is_fine(client, bank, api):
    api.replies = {1: EXPLAIN, 2: {"nudge": "Think about the balloon and what the hot car does to the gas."}}
    open_help(client)
    assert "what the hot car does to the gas" in open_help(client).text


def test_an_explanation_that_states_a_mark_point_is_thrown_away(client, bank, api):
    api.replies = {1: {**EXPLAIN, "rephrase": "Say that particles gain energy and move further apart."}}
    page = open_help(client).text
    assert "try again" in page and "gain energy" not in page
    assert bank.execute("SELECT COUNT(*) FROM question_help").fetchone()[0] == 0
    assert practice.unclaimed_tiers(bank, POINTS_Q) == []


def test_a_failed_call_stores_nothing_and_can_be_retried(client, bank, api):
    api.fail = True
    page = open_help(client).text
    assert "try again" in page and "Try again" in page
    assert bank.execute("SELECT COUNT(*) FROM question_help").fetchone()[0] == 0
    assert practice.unclaimed_tiers(bank, POINTS_Q) == []
    api.fail = False
    assert EXPLAIN["rephrase"] in open_help(client).text


# ---- "With help" -----------------------------------------------------------------------------------

def test_an_attempt_after_help_is_marked_with_help_and_the_score_stands(client, bank, api):
    open_help(client)
    submit(client, "The balloon expands because the particles gain energy and move further apart.")
    row = bank.execute("SELECT marks_awarded, max_marks, with_help FROM attempts").fetchone()
    assert (row["marks_awarded"], row["max_marks"], row["with_help"]) == (2, 2, 1)
    assert practice.hint_tiers(bank, 1) == [1]
    assert practice.unclaimed_tiers(bank, POINTS_Q) == []


def test_an_attempt_without_help_is_not_marked(client, bank, api):
    submit(client)
    assert with_help(bank) == [0]


def test_help_is_claimed_once_so_the_next_attempt_is_clean(client, bank, api):
    open_help(client)
    submit(client)
    submit(client)
    assert with_help(bank) == [1, 0]


def test_help_left_from_a_long_time_ago_does_not_count(client, bank, api):
    bank.execute("INSERT INTO hint_uses (question_id, tier, created_at) VALUES (?, 1, '2020-01-01T00:00:00Z')",
                 (POINTS_Q,))
    bank.commit()
    submit(client)
    assert with_help(bank) == [0]


def test_help_on_another_question_does_not_count(client, bank, api):
    import_questions(bank, [MCQ])
    open_help(client, MCQ["id"])
    submit(client)
    assert with_help(bank) == [0]


def test_a_failed_marking_keeps_the_help_for_the_next_try(client, bank, api):
    open_help(client)
    assert submit(client, "FAIL").status_code == 503
    submit(client)
    assert with_help(bank) == [1]


def test_the_parent_sees_with_help_on_the_feedback_page_and_she_does_not(client, bank, api):
    open_help(client)
    open_help(client)
    location = submit(client).headers["location"]
    assert "with help" not in client.get(location).text
    parent_login(client)
    page = client.get(location).text
    assert "with help" in page and "steps 1, 2" in page
    plain = submit(client).headers["location"]
    assert "with help" not in client.get(plain).text


# ---- Parent settings ---------------------------------------------------------------------------

def test_help_is_on_by_default_with_the_button_and_a_place_for_the_panel(client, bank):
    assert app_settings.get_all(bank)["help_enabled"] is True and app_settings.get_all(bank)["help_tier2"] is True
    page = client.get(f"/q/{POINTS_Q}").text
    assert f'hx-post="/q/{POINTS_Q}/help"' in page and 'id="help-area"' in page and ">Help me<" in page


def test_the_parent_can_switch_help_off_and_the_tiers_off(client, bank, api):
    parent_login(client)
    assert 'name="help_enabled"' in client.get("/parent/settings").text
    assert 'name="help_tier2"' in client.get("/parent/settings").text
    assert client.post("/parent/settings", data={**FORM, "help_enabled": "on"}).status_code == 200
    s = app_settings.get_all(bank)
    assert s["help_enabled"] is True and s["help_tier2"] is False
    assert client.post("/parent/settings", data=FORM).status_code == 200
    assert app_settings.get_all(bank)["help_enabled"] is False
    page = client.get(f"/q/{POINTS_Q}").text
    assert "help-area" not in page and ">Help me<" not in page


def test_with_help_switched_off_the_route_refuses_and_calls_nothing(client, bank, api):
    app_settings.save(bank, {**app_settings.get_all(bank), "help_enabled": False})
    assert open_help(client).status_code == 403
    assert len(api) == 0 and practice.unclaimed_tiers(bank, POINTS_Q) == []


def test_help_for_an_unknown_question_is_a_404(client, bank, api):
    assert open_help(client, "no-such-question").status_code == 404


def test_the_question_page_still_never_shows_the_mark_scheme_with_help_on(client, bank, api):
    open_help(client)
    page = client.get(f"/q/{POINTS_Q}").text
    for secret in ("Particles gain energy", "further apart", "MP1", "key_terms", "mark_points"):
        assert secret not in page
