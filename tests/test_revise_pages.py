"""Revise mode pages, end to end, with made-up cards and the fake marker (no API calls)."""

from app import app_settings, revise
from revise_helpers import CLIMATE, EVENTS, MAP, RAIN, add_cards, make_card, point


def recalls(conn) -> list:
    return conn.execute("SELECT * FROM recall_attempts ORDER BY id").fetchall()


def start(client, **form) -> str:
    """Start a set and return its base URL, e.g. /revise/sets/1."""
    r = client.post("/revise/start", data={"subject": "geography", **form}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/study/0")
    return r.headers["location"][:-len("/study/0")]


# ---- Start page ---------------------------------------------------------------------

def test_start_page_offers_subjects_then_topics(client, bank):
    assert "There are no revision cards yet" in client.get("/revise").text
    add_cards(bank, [CLIMATE, RAIN, EVENTS])
    html = client.get("/revise").text
    assert "Geography" in html and "2 cards · 0 due · 2 new" in html and "History" in html
    html = client.get("/revise?subject=geography").text
    assert 'name="topic" value="geo-intro"' in html and "Introduction to Geography" in html
    assert "Exam mode" in html and "Start 4 cards" in html
    assert 'href="/revise"' in client.get("/").text  # the link from the Practise page


def test_start_with_nothing_to_revise_says_so(client, bank):
    add_cards(bank, [CLIMATE])
    r = client.post("/revise/start", data={"subject": "history"})
    assert r.status_code == 200 and "There are no cards for that choice yet." in r.text
    base = start(client)
    client.post(f"{base}/tick/0", data={"unit": "P1"})
    r = client.post("/revise/start", data={"subject": "geography"})  # known today, so not due
    assert "Nothing is due right now" in r.text and "Tick exam mode" in r.text
    r = client.post("/revise/start", data={"subject": "geography", "exam": "on"}, follow_redirects=False)
    assert r.status_code == 303  # exam mode ignores due dates


# ---- The whole flow: study the set, then recall it -----------------------------------------

def test_study_the_set_then_recall_each_card(client, bank, fake_marker):
    add_cards(bank, [CLIMATE, RAIN, MAP])
    base = start(client)

    # Study: prompt and answer together, key terms marked, examples shown.
    html = client.get(f"{base}/study/0").text
    assert "Study · card 1 of 3" in html and "Define climate." in html
    assert "<mark>average weather</mark>" in html and "Singapore is hot and wet all year." in html
    assert f'href="{base}/study/1"' in html
    html = client.get(f"{base}/study/1").text
    assert "<ol>" in html and "This happens as the air rises." in html  # a list is numbered; sub-points shown
    html = client.get(f"{base}/study/2").text
    assert "Picture card." in html and f'href="{base}/recall/0"' in html  # after the last card, recall starts

    # Recall: the prompt only. Nothing from the answer is on the page.
    html = client.get(f"{base}/recall/0").text
    assert "Define climate." in html and f'action="{base}/typed/0"' in html
    assert "average weather" not in html and "Singapore is hot" not in html and "long period" not in html

    # Typed: checked by the points engine with the light model.
    r = client.post(f"{base}/typed/0", data={"answer": "Climate is the average weather over many years.",
                                             "shown_at": "1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"{base}/result/0"
    assert fake_marker[0]["light"] is True and fake_marker[0]["purpose"] == "recall"
    assert fake_marker[0]["question"]["id"] == "card-climate"
    html = client.get(f"{base}/result/0").text
    assert "1 / 1" in html and "Known. This card moves to box 2 and comes back in 2 days." in html
    assert "Climate is the <mark>average weather</mark> over many years." in html  # her words that earned it
    assert "✓ covered" in html and f'href="{base}/recall/1"' in html
    assert "Define:" not in html  # one sentence: no command-word note

    # One attempt per card per set: going back shows the result, and a second answer isn't taken.
    assert client.get(f"{base}/recall/0", follow_redirects=False).headers["location"] == f"{base}/result/0"
    client.post(f"{base}/typed/0", data={"answer": "Something else."})
    client.post(f"{base}/tick/0", data={})
    assert len(recalls(bank)) == 1

    # Paper: the answer appears with one tick box per point.
    html = client.get(f"{base}/recall/1").text
    assert f'href="{base}/tick/1?t=' in html and "evaporates" not in html
    html = client.get(f"{base}/tick/1?t=1").text
    assert html.count('name="unit"') == 3 and "Water <mark>evaporates</mark> from the sea." in html
    r = client.post(f"{base}/tick/1", data={"unit": ["P1"], "shown_at": "1"}, follow_redirects=False)
    assert r.headers["location"] == f"{base}/result/1"
    html = client.get(f"{base}/result/1").text
    assert "1 / 3" in html and "To revisit. This card goes back to box 1 and comes back in 1 day." in html
    assert html.count("✗ missed") == 2 and "What you wrote" not in html

    # Image card: self-tick only. There is no typing box, and a typed post is sent to the tick page.
    html = client.get(f"{base}/recall/2").text
    assert "Picture it, or sketch it on paper" in html and "/typed/2" not in html and "three rivers" in html
    assert "A made-up map" not in html  # the picture's description is part of the answer
    r = client.post(f"{base}/typed/2", data={"answer": "Three rivers."}, follow_redirects=False)
    assert r.headers["location"].startswith(f"{base}/tick/2")
    html = client.get(f"{base}/tick/2").text
    assert "A made-up map of three rivers." in html and "I recalled this" in html
    client.post(f"{base}/tick/2", data={"unit": "P1"})
    assert f'href="{base}/summary"' in client.get(f"{base}/result/2").text

    html = client.get(f"{base}/summary").text
    assert "Set finished" in html and "2 known · 1 to revisit" in html and "Another set" in html
    rows = recalls(bank)
    assert [(r["card_id"], r["method"], r["points_covered"], r["box_after"]) for r in rows] == [
        ("card-climate", "typed", 1, 2), ("card-rain", "paper", 1, 1), ("card-map", "paper", 1, 2)]
    assert rows[0]["typed_text"] == "Climate is the average weather over many years." and rows[0]["set_id"] == 1
    assert rows[1]["seconds_taken"] is None  # shown_at of "1" is far too long ago to be time spent


def test_recall_after_each_card_when_the_setting_says_so(client, bank):
    add_cards(bank, [CLIMATE, RAIN])
    assert app_settings.save(bank, {"recall_timing": "after_each"}) == []
    base = start(client)
    assert f'href="{base}/recall/0"' in client.get(f"{base}/study/0").text
    client.post(f"{base}/tick/0", data={"unit": "P1"})
    assert f'href="{base}/study/1"' in client.get(f"{base}/result/0").text
    client.post(f"{base}/tick/1", data={})
    assert f'href="{base}/summary"' in client.get(f"{base}/result/1").text


def test_paper_is_offered_first_when_the_setting_says_so(client, bank):
    add_cards(bank, [CLIMATE])
    base = start(client)
    html = client.get(f"{base}/recall/0").text
    assert html.index("Type it from memory") < html.index("Write it on paper")
    app_settings.save(bank, {"recall_method": "paper"})
    html = client.get(f"{base}/recall/0").text
    assert html.index("Write it on paper") < html.index("Type it from memory")


# ---- Typed checks that go wrong, and command-word notes ----------------------------------------

def test_failed_or_empty_typed_check_saves_nothing_and_keeps_her_text(client, bank):
    add_cards(bank, [CLIMATE])
    base = start(client)
    r = client.post(f"{base}/typed/0", data={"answer": "Climate FAIL"})
    assert r.status_code == 503 and "The check didn&#39;t work just now" in r.text and "Climate FAIL</textarea>" in r.text
    assert "average weather" not in r.text  # still the recall page: no answer
    r = client.post(f"{base}/typed/0", data={"answer": "   "})
    assert r.status_code == 422 and "Write an answer before submitting." in r.text
    assert recalls(bank) == [] and revise.get_progress(bank, "card-climate") is None


def test_command_word_note_is_shown_only_when_relevant(client, bank):
    add_cards(bank, [RAIN])
    base = start(client)
    client.post(f"{base}/typed/0", data={"answer": "Water evaporates and droplets fall."})
    html = client.get(f"{base}/result/0").text
    assert "2 / 3" in html and "To revisit" in html  # 67% is under the 80% threshold
    assert "Explain: give the reason, not just what happens." in html
    assert recalls(bank)[0]["command_note"].startswith("Explain:")

    base = start(client, exam="on")
    client.post(f"{base}/typed/0", data={"answer": "Water evaporates because the sun heats it, so it condenses "
                                                   "and droplets fall."})
    html = client.get(f"{base}/result/0").text
    assert "3 / 3" in html and "Explain:" not in html and recalls(bank)[1]["command_note"] == ""


# ---- Underline step ------------------------------------------------------------------

def test_underline_step_hides_bold_until_she_has_tapped(client, bank):
    add_cards(bank, [CLIMATE, MAP])
    app_settings.save(bank, {"underline_step": True})
    base = start(client)
    html = client.get(f"{base}/study/0").text
    assert 'name="w" value="0-3"' in html and "<mark>" not in html and "Check my underlining" in html
    html = client.post(f"{base}/underline/0", data={"w": ["0-4", "0-0"]}).text  # "weather" and "Climate"
    assert "You spotted: <mark>average weather</mark>" in html and "Not key terms: Climate" in html
    assert "Climate is the <mark>average weather</mark> of a place" in html  # now the bold version
    assert recalls(bank) == []  # nothing is stored or scored
    html = client.post(f"{base}/underline/0", data={}).text
    assert "Key terms you missed: <strong>average weather</strong>" in html
    assert "Picture card." in client.get(f"{base}/study/1").text  # nothing to tap on a picture card


# ---- Split cards, single cards, and things that must not work -----------------------------------

def test_split_cards_link_to_their_other_parts(client, bank):
    parts = [make_card(f"card-causes{letter}", f"Explain the causes. ({name})",
                       {"format": "bullets", "points": [point(f"{name} cause.")]}, split_from="Explain the causes.")
             for letter, name in (("a", "Natural"), ("b", "Human"))]
    add_cards(bank, parts)
    html = client.get("/revise/cards/card-causesa").text
    assert "Part 1 of 2" in html and 'href="/revise/cards/card-causesb"' in html and "Natural cause." in html
    base = start(client)
    assert "Part 2 of 2" in client.get(f"{base}/study/1").text


def test_unknown_cards_sets_and_pages_are_404(client, bank):
    add_cards(bank, [CLIMATE])
    base = start(client)
    assert client.get("/revise/cards/no-such-card").status_code == 404
    assert client.get("/revise/cards/card-climate/page/0").status_code == 404  # this card has no page image
    assert client.get(f"{base}/study/5").status_code == 404
    assert client.get("/revise/sets/99/recall/0").status_code == 404
    assert client.get(f"{base}/result/0", follow_redirects=False).headers["location"] == f"{base}/recall/0"
    quiz = revise.create_set(bank, card_ids=["card-climate"], kind="parent_quiz")
    assert client.get(f"/revise/sets/{quiz}/study/0").status_code == 404  # a parent's quiz isn't hers to answer
    assert client.post(f"/revise/sets/{quiz}/tick/0", data={"unit": "P1"}).status_code == 404
    assert recalls(bank) == []
