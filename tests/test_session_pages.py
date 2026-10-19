"""The Today home screen and the session flow, end to end. Fake marker, no API calls."""

from app import app_settings, revise, session, session_plan
from conftest import PARENT_PIN
from revise_helpers import add_cards, make_card, point, settings
from session_helpers import add_questions, add_topic, answer, mk

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


# ---- Subject choice ------------------------------------------------------------------------------------

def add_three_subjects(bank):
    add_topic(bank, "geo-one", subject="geography", title="Made-up geography topic")
    add_topic(bank, "his-one", subject="history", title="Made-up history topic")
    add_questions(bank, [mk("g1", "geo-one", subject="geography"), mk("h1", "his-one", subject="history"),
                         mk("h2", "his-one", subject="history")])


def suggested_today(bank):
    return session_plan.pick_subject(bank, session_plan.local_day(session_plan.utc_now_dt()))


def test_home_preselects_the_suggested_subject_and_labels_it(client, bank):
    add_three_subjects(bank)
    page = client.get("/").text
    suggested = suggested_today(bank)
    assert f'value="{suggested}" checked' in page and page.count("Suggested") == 1
    assert 'value="mix"' in page and "Mix" in page
    assert page.count('name="subject"') == 4 and " checked" in page


def test_the_chosen_subject_is_used_for_the_session_and_the_preview(client, bank):
    add_three_subjects(bank)
    preview = client.get("/session/preview", params={"subject": "history", "minutes": "30"}).text
    assert "Made-up history topic" in preview and "Made-up geography topic" not in preview
    assert "Start today's session" in preview
    assert "Made-up geography topic" in client.get("/session/preview", params={"subject": "geography"}).text
    r = client.post("/session/start", data={"minutes": "30", "subject": "geography"}, follow_redirects=False)
    assert r.status_code == 303
    assert session.get(bank, sid_of(r.headers["location"]))["subject"] == "geography"


def test_the_last_choice_of_the_day_carries_to_resume_and_keep_going(client, bank):
    add_three_subjects(bank)
    r = client.post("/session/start", data={"minutes": "30", "subject": "history"}, follow_redirects=False)
    sid = sid_of(r.headers["location"])
    assert 'value="history" checked' in client.get("/").text                    # remembered, though not the suggestion
    s = session.get(bank, sid)
    while session.position(s)["step"]["key"] != "wrapup":
        session.finish_step(bank, s, "skipped")
    client.post(f"/session/{sid}/more", data={"minutes": "30"})
    client.post(f"/session/{sid}/continue")
    s = session.get(bank, sid)
    assert [b["subject"] for b in s["plan"]["blocks"]] == ["history", "history"]


def test_mix_picks_the_subject_with_her_weakest_recent_topic(client, bank):
    add_three_subjects(bank)
    add_questions(bank, [mk("s1", TOPIC)])
    answer(bank, mk("s1", TOPIC), True)
    answer(bank, mk("h1", "his-one", subject="history"), False)
    answer(bank, mk("g1", "geo-one", subject="geography"), True)
    r = client.post("/session/start", data={"minutes": "30", "subject": "mix"}, follow_redirects=False)
    sid = sid_of(r.headers["location"])
    assert session.get(bank, sid)["subject"] == "history"
    assert 'value="mix" checked' in client.get("/").text


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
    card = make_card("card-sess", "Explain diffusion.", {"format": "bullets", "points": [
        point("Particles move from high to low concentration.", ["concentration"]), point("No energy is needed.", ["energy"])]},
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

    revise.record_attempt(bank, card, method="paper", covered_ids=["P1", "P2"], units_total=2, settings=settings(), set_id=set_id)
    summary = client.get(f"/revise/sets/{set_id}/summary").text
    assert f'href="/session/{sid}"' in summary and "Continue" in summary and "Another set" not in summary
    assert "Practise" in client.get(f"/session/{sid}").text    # on to the next step


def test_quick_fire_cards_are_ticked_one_after_another_with_no_typed_check(client, bank):
    cards = [make_card(f"quick-{i}", f"Define term {i}.", {"format": "bullets", "points": [point(f"Meaning {i}.", [f"meaning{i}"])]},
                       topic=TOPIC, subject="science") for i in (1, 2)]
    add_cards(bank, cards)
    sid = science_session(bank)
    client.post(f"/session/{sid}/done")                                   # focus -> quick-fire
    intro = client.get(f"/session/{sid}").text
    assert "Quick-fire" in intro and "2 quick cards" in intro and "Step 2 of 4" in intro   # one step for both cards
    client.post(f"/session/{sid}/start-step")
    r = client.get(f"/session/{sid}", follow_redirects=False)
    set_id = int(r.headers["location"].split("/")[3])
    assert bank.execute("SELECT self_tick FROM revise_sets WHERE id = ?", (set_id,)).fetchone()[0] == 1
    recall = client.get(f"/revise/sets/{set_id}/recall/0").text
    assert "Say it to yourself" in recall and 'name="answer"' not in recall        # tick only: no typing box
    r = client.post(f"/revise/sets/{set_id}/typed/0", data={"answer": "anything", "shown_at": ""}, follow_redirects=False)
    assert r.status_code == 303 and "/tick/0" in r.headers["location"]            # never reaches the typed check
    for i, card in enumerate(cards):
        client.post(f"/revise/sets/{set_id}/tick/{i}", data={"unit": "P1", "shown_at": ""})
    assert f'href="/session/{sid}"' in client.get(f"/revise/sets/{set_id}/summary").text
    assert "Practise" in client.get(f"/session/{sid}").text                        # on to the next step


def test_parent_sets_the_warm_up_and_learn_card_counts(client, bank):
    login(client)
    html = client.get("/parent/settings").text
    assert 'name="warmup_max" inputmode="numeric" value="8"' in html and 'name="learn_count" inputmode="numeric" value="4"' in html
    assert client.post("/parent/settings", data=settings_form(warmup_max="5", learn_count="6")).status_code == 200
    saved = app_settings.get_all(bank)
    assert (saved["warmup_max"], saved["learn_count"]) == (5, 6)
    assert client.post("/parent/settings", data=settings_form(learn_count="1")).status_code == 422
    assert app_settings.get_all(bank)["learn_count"] == 6


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
