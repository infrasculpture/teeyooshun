"""Student pages end to end, with the fake marker (no API calls)."""

from app import practice
from app.importer import import_questions
from conftest import CAP_QUESTION, attempt_count

POINTS_Q = "sci-test-points-q1"
PART = "The balloon expands."
FULL = "The balloon expands as particles move further apart."


def submit(client, answer, question_id=POINTS_Q, **extra):
    return client.post(f"/q/{question_id}/answer", data={"answer": answer, "shown_at": "", **extra},
                       follow_redirects=False)


def test_start_page_lists_subjects(client):
    page = client.get("/").text
    assert "Science" in page and "History" in page


def test_topics_are_listed_with_readable_names(client):
    page = client.get("/topics", params={"subject": "science"}).text
    assert "Particulate Nature of Matter" in page
    assert "Any topic" in page


def test_practice_redirects_to_a_question(client):
    r = client.get("/practice", params={"subject": "science"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/q/{POINTS_Q}"


def test_question_page_never_shows_the_mark_scheme(client):
    page = client.get(f"/q/{POINTS_Q}").text
    assert "State what happens to the balloon and explain why." in page
    for secret in ("Particles gain energy", "further apart", "the balloon grows because it is hot",
                   "MP1", "key_terms", "mark_points", "expands"):
        assert secret not in page


def test_submit_marks_saves_and_shows_feedback(client, bank):
    r = submit(client, PART)
    assert r.status_code == 303 and r.headers["location"].startswith("/attempts/")
    page = client.get(r.headers["location"]).text
    assert "1 / 2" in page
    assert "<mark>expands</mark>" in page  # her quote highlighted
    assert "Particles gain energy and move further apart" in page  # the point she missed
    assert "FULL MARKS MODEL ANSWER" in page and "TECHNIQUE NOTE" in page
    assert "Have another go" in page and "Skip" in page
    assert attempt_count(bank) == 1


def test_answer_text_is_escaped(client):
    r = submit(client, "<script>alert(1)</script> expands")
    page = client.get(r.headers["location"]).text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_failed_marking_keeps_the_answer_and_saves_nothing(client, bank):
    r = submit(client, "FAIL my careful answer")
    assert r.status_code == 503
    assert "Marking didn&#39;t work" in r.text or "Marking didn't work" in r.text
    assert "FAIL my careful answer" in r.text
    assert attempt_count(bank) == 0


def test_empty_answer_is_refused(client, bank):
    r = submit(client, "   ")
    assert r.status_code == 422
    assert attempt_count(bank) == 0


def test_cap_message_shown(client, bank):
    import_questions(bank, [CAP_QUESTION])
    r = submit(client, "alpha, beta and gamma", question_id="sci-test-cap")
    page = client.get(r.headers["location"]).text
    assert "You made 3 valid points but only 2 count" in page


def test_rewrite_flow(client, bank):
    first = submit(client, PART).headers["location"]
    first_id = int(first.rsplit("/", 1)[1])
    r = client.post(f"/attempts/{first_id}/rewrite", data={"answer": FULL, "shown_at": ""}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get(r.headers["location"]).text
    assert "+1 compared with your first answer" in page
    assert "Have another go" not in page  # one rewrite only
    # A second rewrite is refused and saves nothing.
    again = client.post(f"/attempts/{first_id}/rewrite", data={"answer": FULL}, follow_redirects=False)
    assert again.status_code == 303 and again.headers["location"] == f"/attempts/{first_id}"
    assert attempt_count(bank) == 2


def test_skip_flow(client, bank):
    first_id = int(submit(client, PART).headers["location"].rsplit("/", 1)[1])
    r = client.post(f"/attempts/{first_id}/skip", data={"scope": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/practice?subject=science"
    assert practice.rewrite_status(bank, practice.get_attempt(bank, first_id)) == "skipped"
    assert "Have another go" not in client.get(f"/attempts/{first_id}").text


def test_full_marks_goes_straight_to_next_question(client):
    page = client.get(submit(client, FULL).headers["location"]).text
    assert "Have another go" not in page and "Next question" in page


def test_student_can_flag_marking_as_wrong(client, bank):
    first_id = int(submit(client, PART).headers["location"].rsplit("/", 1)[1])
    r = client.post(f"/attempts/{first_id}/looks-wrong", data={"note": "I did say particles"},
                    headers={"HX-Request": "true"})
    assert "A parent will check it" in r.text
    events = practice.events_for(bank, first_id)
    assert [(e["event"], e["actor"], e["note"]) for e in events] == [
        ("marking_looks_wrong", "student", "I did say particles")]


def test_student_sees_no_parent_panel(client):
    page = client.get(submit(client, PART).headers["location"]).text
    assert "Parent review" not in page
    assert "Agree with the marking" not in page
