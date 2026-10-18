"""The Today home screen and the session flow, end to end. Fake marker, no API calls."""

from app import app_settings, revise, session
from conftest import PARENT_PIN
from revise_helpers import add_cards, make_card, point, settings
from session_helpers import add_questions, mk

POINTS_Q = "sci-test-points-q1"          # in the fixture bank: science, particulate nature, 2 marks
TOPIC = "sci-07-particulate-nature"
FULL = "The balloon expands as particles move further apart."


def science_session(bank, minutes=30):
    """focus -> practise (the one fixture question) -> wrap-up."""
    return session.create_daily(bank, minutes, subject="science")


def sid_of(location: str) -> int:
    return int(location.split("?")[0].rsplit("/", 1)[1])


# ---- Home ----------------------------------------------------------------------------------

def test_home_shows_the_plan_and_the_choices(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Start today's session" in page.text and "Choose my own" in page.text
    assert "30 minutes:" in page.text                       # the plan in plain words
    assert "Year-end review" not in page.text               # nothing approved to review yet
    assert "Resume session" not in page.text
    assert 'href="/choose"' in page.text and 'href="/revise"' in page.text


def test_year_end_review_appears_once_there_are_approved_generated_questions(client, bank):
    add_questions(bank, [mk("gen-1", TOPIC, source="generated")])
    assert "Year-end review" in client.get("/").text


def test_an_unfinished_session_is_offered_for_resume(client, bank):
    sid = science_session(bank)
    page = client.get("/").text
    assert "Resume session" in page and f'href="/session/{sid}"' in page


def test_start_creates_a_session_and_unknown_lengths_fall_back_to_30(client, bank):
    r = client.post("/session/start", data={"minutes": "999"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/session/")
    assert bank.execute("SELECT length_planned FROM sessions").fetchone()[0] == 30
    r = client.post("/session/start", data={"minutes": "60"}, follow_redirects=False)
    assert bank.execute("SELECT length_planned FROM sessions ORDER BY id DESC").fetchone()[0] == 60


def test_unknown_session_is_404(client):
    assert client.get("/session/9999").status_code == 404


# ---- Walking through a session -------------------------------------------------------------------

def test_a_session_walks_through_its_steps_and_ends(client, bank):
    sid = science_session(bank)
    page = client.get(f"/session/{sid}").text
    assert "Today's focus" in page and "Step 1 of 3" in page    # silent skips leave 3 steps, not 6

    assert client.post(f"/session/{sid}/done", follow_redirects=False).status_code == 303
    page = client.get(f"/session/{sid}").text
    assert "Step 2 of 3" in page and "Practise" in page and "Not now" in page

    client.post(f"/session/{sid}/start-step")
    r = client.get(f"/session/{sid}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(f"/q/{POINTS_Q}?scope=")
    question = client.get(r.headers["location"]).text
    assert "Step 2 of 3" in question and "Not now" in question

    r = client.post(f"/q/{POINTS_Q}/answer", data={"answer": FULL, "shown_at": "", "scope": f"@s:{sid}"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/attempts/")
    feedback = client.get(r.headers["location"]).text
    assert f'href="/session/{sid}"' in feedback                 # "Next question" goes back to the session

    wrap = client.get(f"/session/{sid}").text
    assert "Nearly done" in wrap and "2 / 2 marks" in wrap
    client.post(f"/session/{sid}/done")
    assert "Session finished" in client.get(f"/session/{sid}").text
    assert "Resume session" not in client.get("/").text


def test_not_now_skips_the_step_and_logs_it(client, bank):
    sid = science_session(bank)
    client.post(f"/session/{sid}/done")
    client.post(f"/session/{sid}/start-step")
    client.post(f"/session/{sid}/skip")                         # the "Not now" button on the question page
    assert "Nearly done" in client.get(f"/session/{sid}").text
    log = session.get(bank, sid)["plan"]["log"]
    assert [(e["event"], e["step"]) for e in log] == [("skip", "practise")]


def test_questions_outside_a_session_have_no_step_bar(client):
    page = client.get(f"/q/{POINTS_Q}").text
    assert "Not now" not in page and "Step " not in page


def test_choose_my_own_still_works(client):
    r = client.get("/practice", params={"subject": "science"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/q/{POINTS_Q}"   # no session, no scope


def test_a_break_screen_follows_each_60_minute_block(client, bank):
    sid = science_session(bank, minutes=90)
    s = session.get(bank, sid)
    while session.position(s)["state"] == "step":
        session.finish_step(bank, s, "skipped")
    page = client.get(f"/session/{sid}").text
    assert "Take 10 minutes" in page and "Continue" in page
    r = client.post(f"/session/{sid}/continue", follow_redirects=False)
    assert r.status_code == 303
    after = client.get(f"/session/{sid}").text
    assert "Take 10 minutes" not in after and "Nearly done" in after


def test_wrap_up_offers_a_break_then_keep_going(client, bank):
    add_questions(bank, [mk("second-q", "sci-01-scientific-endeavour")])
    sid = science_session(bank)
    s = session.get(bank, sid)
    while session.position(s)["step"]["key"] != "wrapup":
        session.finish_step(bank, s, "skipped")
    wrap = client.get(f"/session/{sid}").text
    assert "Take a break, then keep going" in wrap and "30 more minutes" in wrap and "60 more minutes" in wrap
    r = client.post(f"/session/{sid}/more", data={"minutes": "30"}, follow_redirects=False)
    assert r.status_code == 303
    assert "Take 10 minutes" in client.get(f"/session/{sid}").text      # the break screen first
    client.post(f"/session/{sid}/continue")
    page = client.get(f"/session/{sid}").text
    assert "Today's focus" in page and "Take 10 minutes" not in page    # then a new plan, same session and day
    assert session.get(bank, sid)["plan"]["blocks"][1]["minutes"] == 30
    assert client.post(f"/session/{sid}/more", data={"minutes": "30"}).status_code == 200
    assert session.get(bank, sid)["block_count"] == 2                   # not offered mid-block: nothing changed


def test_no_keep_going_offer_in_a_year_end_review(client, bank):
    add_questions(bank, [mk("gen-1", TOPIC, source="generated")])
    sid = session.create_year_end(bank)
    s = session.get(bank, sid)
    session.finish_step(bank, s, "skipped")
    assert "Nearly done" in client.get(f"/session/{sid}").text
    assert "keep going" not in client.get(f"/session/{sid}").text


def test_card_steps_use_revise_and_come_back_to_the_session(client, bank):
    card = make_card("card-sess", "Define diffusion.", {"format": "bullets", "points": [point("Spreading out.", ["spreading"])]},
                     topic=TOPIC, subject="science")
    add_cards(bank, [card])
    sid = science_session(bank)
    client.post(f"/session/{sid}/done")                         # focus -> learn
    assert "Learn" in client.get(f"/session/{sid}").text
    client.post(f"/session/{sid}/start-step")
    r = client.get(f"/session/{sid}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/study/0")
    set_id = int(r.headers["location"].split("/")[3])
    assert client.get(r.headers["location"]).status_code == 200
    assert revise.get_set(bank, set_id)["session_id"] == sid

    revise.record_attempt(bank, card, method="paper", covered_ids=["P1"], units_total=1, settings=settings(), set_id=set_id)
    summary = client.get(f"/revise/sets/{set_id}/summary").text
    assert f'href="/session/{sid}"' in summary and "Continue" in summary and "Another set" not in summary
    assert "Practise" in client.get(f"/session/{sid}").text    # on to the next step


# ---- Year-end review ----------------------------------------------------------------------------------

def test_year_end_review_runs_through_the_question_screen(client, bank):
    add_questions(bank, [mk("gen-1", TOPIC, source="generated"), mk("gen-2", "sci-01-scientific-endeavour", source="generated")])
    r = client.post("/session/year-end", follow_redirects=False)
    assert r.status_code == 303
    sid = sid_of(r.headers["location"])
    page = client.get(f"/session/{sid}").text
    assert "Year-end review" in page and "2 questions" in page
    client.post(f"/session/{sid}/start-step")
    r = client.get(f"/session/{sid}", follow_redirects=False)
    assert r.headers["location"].startswith("/q/gen-")
    assert "Step 1 of 2" in client.get(r.headers["location"]).text


def test_year_end_without_questions_says_so(client):
    r = client.post("/session/year-end")
    assert r.status_code == 200 and "no approved practice questions" in r.text


# ---- Parent setting ------------------------------------------------------------------------------------

def login(client):
    client.post("/parent/login", data={"pin": PARENT_PIN, "next": "/"}, follow_redirects=False)


def settings_form(**changes):
    form = {"cards_per_set": "4", "recall_method": "type", "recall_timing": "after_set", "known_threshold": "80",
            **{f"interval_{i}": d for i, d in enumerate(("1", "2", "4", "8", "16"), 1)}}
    return {**form, **changes}


def test_parent_can_set_her_current_level(client, bank):
    login(client)
    assert 'name="current_level"' in client.get("/parent/settings").text
    assert client.post("/parent/settings", data=settings_form(current_level="sec2")).status_code == 200
    assert app_settings.get_all(bank)["current_level"] == "sec2"
    assert client.post("/parent/settings", data=settings_form(current_level="sec9")).status_code == 422
    assert app_settings.get_all(bank)["current_level"] == "sec2"
