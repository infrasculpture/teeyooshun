"""Parent pages for Revise mode: the quiz view, the settings page and switching topics off.
Made-up cards, no API calls."""

from app import app_settings, practice, revise
from app.topics import topic_statuses, topics_in_use
from conftest import PARENT_PIN
from revise_helpers import CLIMATE, EVENTS, MAP, RAIN, add_cards

SETTINGS_FORM = {"cards_per_set": "2", "recall_method": "paper", "recall_timing": "after_each", "underline_step": "on",
                 "interval_1": "1", "interval_2": "3", "interval_3": "7", "interval_4": "14", "interval_5": "30",
                 "known_threshold": "60"}


def login(client):
    client.post("/parent/login", data={"pin": PARENT_PIN, "next": "/"}, follow_redirects=False)


def recalls(conn) -> list:
    return conn.execute("SELECT * FROM recall_attempts ORDER BY id").fetchall()


# ---- The PIN ----------------------------------------------------------------------

def test_parent_pages_need_the_pin(client, bank):
    add_cards(bank, [CLIMATE])
    for url in ("/parent/quiz", "/parent/settings", "/parent/quiz/1/0", "/parent/quiz/1/done"):
        r = client.get(url, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == f"/parent/login?next={url}"
    for url, form in (("/parent/quiz/start", {"subject": "geography"}), ("/parent/quiz/1/0", {"unit": "P1"}),
                      ("/parent/settings", SETTINGS_FORM), ("/parent/topics", {"status:geo-intro": "not_taught"})):
        assert client.post(url, data=form).status_code == 403
    assert recalls(bank) == [] and topic_statuses(bank) == {}
    assert app_settings.get_all(bank) == app_settings.DEFAULTS
    assert "Quiz" not in client.get("/revise").text  # no parent links for her


# ---- Quiz ------------------------------------------------------------------------

def test_quiz_attempts_are_saved_as_parent_quiz_and_move_the_box(client, bank):
    add_cards(bank, [CLIMATE, RAIN, MAP])
    login(client)
    html = client.get("/parent/quiz").text
    assert "Quiz her" in html and "Her due cards (0)" in html and "Introduction to Geography" in html
    r = client.post("/parent/quiz/start", data={"subject": "geography"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/parent/quiz/1/0"

    html = client.get("/parent/quiz/1/0").text  # the prompt in large type, the points as tick boxes
    assert 'class="quiz-prompt"' in html and "Define climate." in html and "Tick each point she gives." in html
    assert "Climate is the <mark>average weather</mark> of a place" in html and "Singapore is hot and wet" in html
    r = client.post("/parent/quiz/1/0", data={"unit": "P1"}, follow_redirects=False)
    assert r.headers["location"] == "/parent/quiz/1/1"
    assert client.get("/parent/quiz/1/1").text.count('name="unit"') == 3
    client.post("/parent/quiz/1/1", data={"unit": ["P1", "P2", "P3"]})
    assert "She recalled this" in client.get("/parent/quiz/1/2").text  # picture card: one tick
    r = client.post("/parent/quiz/1/2", data={}, follow_redirects=False)
    assert r.headers["location"] == "/parent/quiz/1/done"

    rows = recalls(bank)
    assert [(r["card_id"], r["method"], r["points_covered"], r["box_before"], r["box_after"]) for r in rows] == [
        ("card-climate", "parent_quiz", 1, 1, 2), ("card-rain", "parent_quiz", 3, 1, 2),
        ("card-map", "parent_quiz", 0, 1, 1)]
    assert revise.get_progress(bank, "card-rain")["box"] == 2
    html = client.get("/parent/quiz/1/done").text
    assert "Quiz finished" in html and "2 known · 1 to revisit" in html and "Another quiz" in html

    # A card already marked in this quiz can't be marked twice.
    assert "already been marked" in client.get("/parent/quiz/1/0").text
    client.post("/parent/quiz/1/0", data={})
    assert len(recalls(bank)) == 3


def test_quiz_on_her_due_cards_and_by_topic(client, bank):
    add_cards(bank, [CLIMATE, RAIN, EVENTS])
    login(client)
    r = client.post("/parent/quiz/start", data={"choice": "due"})
    assert r.status_code == 200 and "There are no cards to ask" in r.text  # nothing has been seen yet
    revise.record_attempt(bank, RAIN, method="paper", covered_ids=[], units_total=3, settings=app_settings.DEFAULTS)
    bank.execute("UPDATE card_progress SET due_at = '2020-01-01T00:00:00Z'")
    bank.commit()
    assert "Her due cards (1)" in client.get("/parent/quiz").text
    r = client.post("/parent/quiz/start", data={"choice": "due"}, follow_redirects=False)
    quiz = revise.get_set(bank, int(r.headers["location"].split("/")[3]))
    assert quiz["card_ids"] == ["card-rain"] and quiz["kind"] == "parent_quiz"

    r = client.post("/parent/quiz/start", data={"subject": "history", "topic": "his-u1-temasek-to-singapore"},
                    follow_redirects=False)
    quiz = revise.get_set(bank, int(r.headers["location"].split("/")[3]))
    assert quiz["card_ids"] == ["card-events"]
    assert client.get(f"/parent/quiz/{quiz['id']}/0").text.count('name="unit"') == 2  # one per event

    # A student set isn't a quiz.
    student = revise.create_set(bank, card_ids=["card-climate"])
    assert client.get(f"/parent/quiz/{student}/0").status_code == 404


# ---- Settings ---------------------------------------------------------------------

def test_settings_change_behaviour_without_code(client, bank):
    add_cards(bank, [CLIMATE, RAIN, MAP])
    login(client)
    html = client.get("/parent/settings").text
    assert 'name="cards_per_set" inputmode="numeric" value="4"' in html
    assert '<option value="after_set" selected>' in html and 'name="interval_5" inputmode="numeric" value="16"' in html

    r = client.post("/parent/settings", data=SETTINGS_FORM)
    assert r.status_code == 200 and "Settings saved." in r.text and '<option value="after_each" selected>' in r.text
    assert app_settings.get_all(bank) == {
        "cards_per_set": 2, "recall_method": "paper", "recall_timing": "after_each", "underline_step": True,
        "box_intervals": [1, 3, 7, 14, 30], "known_threshold": 60, "exam_mode": False,  # unticked box = off
        "generated_approved_confidence": "medium", "current_level": app_settings.DEFAULTS["current_level"],  # this form has no level field: the default stays
        "warmup_max": 8, "learn_count": 4,  # a form without these fields keeps the defaults
        "practise_max_30": 8, "practise_max_60": 15, "check_max": 5,
        "help_enabled": False, "help_tier2": False,  # unticked boxes = off (this form has no Help me fields)
        "read_enabled": False, "read_before_submit": False, "read_on_recall": False,  # unticked = off (this form has no Read about this fields)
        "ask_enabled": False, "ask_daily_cap": 40}

    # The next set follows the new settings: 2 cards, recall after each card, a 60% threshold, 3 days for box 2.
    assert "Start 2 cards" in client.get("/revise?subject=geography").text
    client.post("/revise/start", data={"subject": "geography"})
    assert revise.get_set(bank, 1)["card_ids"] == ["card-climate", "card-rain"]
    assert "Check my underlining" in client.get("/revise/sets/1/study/0").text
    client.post("/revise/sets/1/tick/1", data={"unit": ["P1", "P2"]})  # 2 of 3 = 67%
    html = client.get("/revise/sets/1/result/1").text
    assert "Known. This card moves to box 2 and comes back in 3 days." in html
    assert 'href="/revise/sets/1/summary"' in html


def test_bad_settings_are_refused_and_nothing_changes(client, bank):
    login(client)
    r = client.post("/parent/settings", data={**SETTINGS_FORM, "cards_per_set": "0", "interval_3": "soon"})
    assert r.status_code == 422
    assert "Cards per set must be a whole number from 1 to 20." in r.text
    assert "Box intervals must be five whole numbers of days" in r.text
    assert 'name="interval_3" inputmode="numeric" value="soon"' in r.text  # what was typed is kept on the page
    assert app_settings.get_all(bank) == app_settings.DEFAULTS


# ---- Topics switched off ---------------------------------------------------------------

def test_a_topic_set_to_not_taught_is_hidden_in_practise_and_revise(client, bank):
    add_cards(bank, [CLIMATE, EVENTS])
    login(client)
    topics = topics_in_use(bank)
    assert {"geo-intro", "his-u1-temasek-to-singapore"} <= {t["topic"] for t in topics}
    html = client.get("/parent/settings").text
    assert 'name="status:geo-intro"' in html and "Introduction to Geography" in html and "1 card" in html

    form = {f"status:{t['topic']}": "not_taught" for t in topics if t["topic"] != "his-u1-temasek-to-singapore"}
    r = client.post("/parent/topics", data={**form, "status:his-u1-temasek-to-singapore": "taught"})
    assert r.status_code == 200 and "Topics saved." in r.text
    statuses = topic_statuses(bank)
    assert statuses["geo-intro"] == "not_taught" and statuses["his-u1-temasek-to-singapore"] == "taught"
    assert [s["subject"] for s in revise.list_subjects(bank)] == ["history"]
    assert practice.list_subjects(bank) == []  # every topic with questions is off
    html = client.get("/revise").text
    assert "History" in html and "Geography" not in html

    # Back to "not set": offered again.
    client.post("/parent/topics", data={name: "" for name in form})
    assert topic_statuses(bank) == {"his-u1-temasek-to-singapore": "taught"}
    assert [s["subject"] for s in revise.list_subjects(bank)] == ["geography", "history"]
    assert practice.list_subjects(bank) != []
