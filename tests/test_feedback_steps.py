"""Question layout and stepped feedback (STUDENT_UI_SPEC.md sections 4 and 5).

Presentation only: marking is replaced by stored marking outputs (fixtures/marking_outputs.json),
so no test here calls the API.
"""

import json
from pathlib import Path

import pytest
from markupsafe import Markup

from app import feedback
from app.importer import import_questions
from app.marking import rules
from conftest import ONE_MARK_QUESTION
from test_mcq import MCQ

OUTPUTS = json.loads((Path(__file__).parent / "fixtures" / "marking_outputs.json").read_text(encoding="utf-8"))
POINTS_Q = "sci-test-points-q1"
LORMS_Q = "his-test-lorms-q1"


# ---- Helpers ---------------------------------------------------------------------------------

def scored(question, name):
    """A stored marking output run through the real rules, as the marker would hand it back."""
    entry = OUTPUTS[name]
    result = rules.score(question, entry["output"], entry["answer"])
    return result, entry["answer"]


@pytest.fixture
def stored(monkeypatch):
    """Make the marker return a stored output; returns the answer text that goes with it."""
    def use(name):
        entry = OUTPUTS[name]

        def mark(conn, question, answer, **kwargs):
            result = rules.score(question, entry["output"], answer)
            result.update({"question_id": question["id"], "model": "fake", "api_call_id": None, "usage": None,
                           "error": None, "model_output": entry["output"]})
            return result

        monkeypatch.setattr("app.marking.marker.mark_answer", mark)
        return entry["answer"]
    return use


def submit(client, question_id, answer, **extra):
    r = client.post(f"/q/{question_id}/answer", data={"answer": answer, "shown_at": "", **extra},
                    follow_redirects=False)
    return r.headers["location"]


def order(html, *markers):
    """Positions of the markers in the page, to check the order things appear in."""
    return [html.index(m) for m in markers]


# ---- "The one thing to fix": chosen in code ---------------------------------------------------

def test_a_missed_key_term_beats_a_missed_point_and_the_technique_note(points_question):
    result, _ = scored(points_question, "points_missing_term")
    view = feedback._points_view(result)
    assert view["not_earned"] and view["missing_key_terms"]  # all three candidates exist
    one = feedback.one_thing_to_fix("points", result["marks_awarded"], 2, view, "Name the particles.")
    assert one == {"kind": "key_term", "text": "Use the key term “particles”."}


def test_a_missed_point_beats_the_technique_note(points_question):
    result, _ = scored(points_question, "points_missing_point")
    view = feedback._points_view(result)
    one = feedback.one_thing_to_fix("points", result["marks_awarded"], 2, view, "Two marks means two linked points.")
    assert one == {"kind": "point", "text": "Particles gain energy and move further apart"}


def test_the_technique_note_is_the_last_resort(points_question):
    view = {"missing_key_terms": [], "not_earned": []}
    assert feedback.one_thing_to_fix("points", 1, 2, view, "Be shorter.") == {"kind": "technique", "text": "Be shorter."}
    assert feedback.one_thing_to_fix("points", 1, 2, view, "") is None


def test_nothing_to_fix_on_full_marks_or_for_mcqs(points_question):
    result, _ = scored(points_question, "points_technique_only")
    view = feedback._points_view(result)
    assert result["marks_awarded"] == 2
    assert feedback.one_thing_to_fix("points", 2, 2, view, "Nice and short.") is None
    assert feedback.one_thing_to_fix("mcq", 0, 1, {}, "x") is None


def test_lorms_names_a_missing_peel_part_before_why_not_higher(lorms_question):
    result, _ = scored(lorms_question, "lorms_missing_peel")
    view = feedback._lorms_view(result)
    assert view["peel_missing"] == ["Evidence", "Explain", "Link"]
    one = feedback.one_thing_to_fix("lorms", result["marks_awarded"], 5, view, "Quote the source.")
    assert one == {"kind": "point", "text": "Your answer is missing the Evidence part of PEEL."}


def test_lorms_falls_back_to_why_not_higher_then_technique(lorms_question):
    result, _ = scored(lorms_question, "lorms_nothing_missing_from_peel")
    view = feedback._lorms_view(result)
    assert view["peel_missing"] == []
    one = feedback.one_thing_to_fix("lorms", result["marks_awarded"], 5, view, "Quote the source.")
    assert one == {"kind": "point", "text": "The inference has no support from the source."}
    view["why_not_higher"] = ""
    assert feedback.one_thing_to_fix("lorms", 2, 5, view, "Quote the source.")["kind"] == "technique"


# ---- Highlights and the command-word note -------------------------------------------------------

def test_highlight_labels_the_mark_point_and_greys_filler():
    html = feedback.highlight("The balloon expands because it gets hot.", ["expands"], ["1"], ["because it gets hot"])
    assert html == Markup('The balloon <mark>expands</mark><sup class="mp">1</sup> '
                          '<span class="filler">because it gets hot</span>.')


def test_filler_that_overlaps_a_highlight_is_not_greyed_and_unlabelled_output_is_unchanged():
    text = "The balloon expands because it gets hot."
    assert "filler" not in feedback.highlight(text, ["expands because"], ["1"], ["because it gets hot"])
    assert feedback.highlight(text, ["expands"]) == Markup("The balloon <mark>expands</mark> because it gets hot.")


def test_html_in_the_answer_is_still_escaped_with_labels():
    html = feedback.highlight("<b>expands</b>", ["expands"], ["1"])
    assert "&lt;b&gt;" in html and "<b>" not in html


def test_command_note_comes_from_the_command_word_table():
    assert feedback.command_note("explain", 4) == (
        "Explain [4]: give reasons or make some reference to theory, depending on the context.")
    assert feedback.command_note("Explain", 2).startswith("Explain [2]:")
    assert feedback.command_note("infer", 5) == "" and feedback.command_note("", 2) == ""  # not in the table


# ---- The question screen ----------------------------------------------------------------------------

def test_question_screen_has_two_columns_and_scales_up_for_a_big_screen(client):
    page = client.get(f"/q/{POINTS_Q}").text
    assert '<body class="qa">' in page and 'class="qa-grid"' in page
    assert page.index('class="qa-left"') < page.index('class="qa-right"')
    assert "@media (min-width: 1000px)" in page and "@media (min-width: 2560px)" in page
    assert "max-width: 80em" in page  # about 1600px at 20px type


def test_answer_box_is_sized_to_the_marks_with_the_command_word_note_underneath(client, bank):
    page = client.get(f"/q/{POINTS_Q}").text  # 2 marks
    assert 'rows="6"' in page
    note = "Explain [2]: give reasons or make some reference to theory, depending on the context."
    assert page.index("<textarea") < page.index(note)
    assert '<span class="chip cmd">explain</span>' in page
    import_questions(bank, [ONE_MARK_QUESTION])  # 1 mark, no command word
    one = client.get("/q/sci-test-one-mark").text
    assert 'rows="3"' in one and "qa-note" not in one.split("</style>")[1]


def test_mcq_question_has_options_and_no_answer_box(client, bank):
    import_questions(bank, [MCQ])
    page = client.get(f"/q/{MCQ['id']}").text
    assert page.count('type="radio"') == 4 and "<textarea" not in page


# ---- Stepped feedback: points ------------------------------------------------------------------------

STEP_IDS = ['id="step-score"', 'id="step-answer"', 'id="step-missing"', 'id="step-full"', 'id="step-rewrite"']


def test_feedback_steps_come_in_order_with_only_the_first_open(client, stored):
    answer = stored("points_missing_term")
    page = client.get(submit(client, POINTS_Q, answer)).text
    positions = order(page, *STEP_IDS)  # raises if one is missing
    assert positions == sorted(positions)
    assert '<section class="step" id="step-score">' in page
    for key in ("answer", "missing", "full", "rewrite"):
        assert f'<section class="step hidden" id="step-{key}">' in page
    assert page.count("Got it →") == 4  # the last step has the rewrite form instead
    assert "Next question" not in page
    assert 'class="qa-left"' in page and "State what happens to the balloon" in page  # question stays on the left


def test_score_card_has_the_one_thing_to_fix(client, stored):
    page = client.get(submit(client, POINTS_Q, stored("points_missing_term"))).text
    score = page[page.index('id="step-score"'):page.index('id="step-answer"')]
    assert "1 / 2" in score and "Marking confidence" in score
    assert "The one thing to fix" in score and "Use the key term “particles”." in score


def test_her_answer_step_shows_highlights_the_mark_point_and_greyed_filler(client, stored):
    page = client.get(submit(client, POINTS_Q, stored("points_missing_term"))).text
    step = page[page.index('id="step-answer"'):page.index('id="step-missing"')]
    assert '<mark>expands</mark><sup class="mp">1</sup>' in step
    assert '<span class="filler">because it gets hot</span>' in step
    assert "The balloon expands</li>" in step  # the mark point the highlight earned


def test_missing_step_lists_missed_points_key_terms_and_technique(client, stored):
    page = client.get(submit(client, POINTS_Q, stored("points_missing_term"))).text
    step = page[page.index('id="step-missing"'):page.index('id="step-full"')]
    assert "Particles gain energy and move further apart" in step
    assert "Key terms to use:</strong> particles, further apart" in step
    assert "Name the particles in your explanation." in step
    assert "particles gain energy and move further apart." in page[page.index('id="step-full"'):].lower()


def test_rewrite_is_inline_and_uses_the_existing_route(client, stored, bank):
    answer = stored("points_missing_point")
    location = submit(client, POINTS_Q, answer)
    attempt_id = location.rsplit("/", 1)[1]
    page = client.get(location).text
    step = page[page.index('id="step-rewrite"'):]
    assert f'action="/attempts/{attempt_id}/rewrite"' in step and f'action="/attempts/{attempt_id}/skip"' in step
    assert 'name="scope"' in step and "Submit rewrite" in step
    # Submitting it marks and stores a second attempt exactly as before; the first stays the one that counts.
    r = client.post(f"/attempts/{attempt_id}/rewrite", data={"answer": "The balloon expands.", "shown_at": ""},
                    follow_redirects=False)
    assert r.status_code == 303
    rows = bank.execute("SELECT id, parent_attempt_id FROM attempts ORDER BY id").fetchall()
    assert [(row["id"], row["parent_attempt_id"]) for row in rows] == [(int(attempt_id), None), (int(attempt_id) + 1, int(attempt_id))]


def test_a_failed_rewrite_shows_every_step_so_the_form_and_error_are_visible(client, stored):
    answer = stored("points_missing_point")
    attempt_id = submit(client, POINTS_Q, answer).rsplit("/", 1)[1]
    r = client.post(f"/attempts/{attempt_id}/rewrite", data={"answer": "   ", "shown_at": ""})
    assert r.status_code == 422 and 'class="error"' in r.text
    assert "step hidden" not in r.text and "Got it" not in r.text


def test_coming_back_to_an_attempt_shows_every_step(client, stored):
    location = submit(client, POINTS_Q, stored("points_missing_point"))
    page = client.get(location + "?all=1").text
    assert "step hidden" not in page and "Got it" not in page and "classList.add('js')" not in page


def test_full_marks_ends_on_next_question_with_no_rewrite(client, stored):
    answer = stored("points_technique_only")
    page = client.get(submit(client, POINTS_Q, answer)).text
    assert "Full marks. Nothing to fix." in page and "The one thing to fix" not in page
    assert 'id="step-rewrite"' not in page and "Have another go" not in page
    assert page.count("Got it →") == 3  # score, answer, technique
    assert page.index("Next question") > page.index('id="step-full"')
    assert "Technique" in page and "Nice and short." in page


def test_the_session_scope_is_kept_for_next_question_and_rewrite(client, bank, stored):
    from app import session
    sid = session.create_daily(bank, 30, subject="science")
    scope = f"@s:{sid}"
    answer = stored("points_missing_point")
    page = client.get(submit(client, POINTS_Q, answer, scope=scope)).text
    assert f'name="scope" value="{scope}"' in page


def test_feedback_page_has_the_enter_key_handler_and_still_shows_looks_wrong(client, stored):
    page = client.get(submit(client, POINTS_Q, stored("points_missing_point"))).text
    assert "e.key !== 'Enter'" in page and "document.documentElement.classList.add('js')" in page
    assert "This marking looks wrong" in page and 'id="parent-panel"' in page


# ---- Stepped feedback: LORMS and MCQ ---------------------------------------------------------------------

def test_lorms_steps_show_the_level_and_the_peel_map_in_step_two(client, stored):
    answer = stored("lorms_missing_peel")
    page = client.get(submit(client, LORMS_Q, answer)).text
    answer_step = page[page.index('id="step-answer"'):page.index('id="step-missing"')]
    assert "Level 2" in answer_step and "PEEL check" in answer_step and "MISSING" in answer_step
    assert "Your answer, marked" in answer_step
    score = page[page.index('id="step-score"'):page.index('id="step-answer"')]
    assert "Your answer is missing the Evidence part of PEEL." in score
    missing = page[page.index('id="step-missing"'):page.index('id="step-full"')]
    assert "Why not higher:</strong> No evidence from the source." in missing
    assert "Missing from PEEL:</strong> Evidence, Explain, Link" in missing


@pytest.fixture
def mcq_page(client, bank, monkeypatch):
    import_questions(bank, [MCQ])
    explanation = {"why_correct": "A plane mirror gives regular reflection.",
                   "why_chosen_wrong": "Brown paper scatters light.", "misconception": "Rough is not shiny."}
    monkeypatch.setattr("app.marking.mcq_explain.structured_call",
                        lambda conn, **kw: (explanation, {"model": "fake", "api_call_id": None, "usage": {}}))

    def go(choice):
        location = client.post(f"/q/{MCQ['id']}/answer", data={"answer": choice}, follow_redirects=False).headers["location"]
        return client.get(location).text
    return go


def test_mcq_is_two_steps_score_then_the_explanation(mcq_page):
    page = mcq_page("B")
    assert page.count('<section class="step') == 2
    positions = order(page, 'id="step-score"', "Not this time", 'id="step-explain"', "Why C is right")
    assert positions == sorted(positions)
    assert "0 / 1" in page and "The correct answer is" in page
    assert page.count("Got it →") == 1 and "Next question" in page
    assert "Have another go" not in page and "The one thing to fix" not in page


def test_a_right_mcq_answer_shows_only_why_it_is_right(mcq_page):
    page = mcq_page("C")
    assert "Correct" in page and "Why C is right" in page and "Why C is not" not in page
