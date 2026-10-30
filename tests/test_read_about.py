""""Read about this": the relevance lookup, the reference text, "with help" only before submitting, the parent
settings, the chapter viewer's allow-list, the Ask payload and the parent's counts. Made-up notes files, pages and
cards only, drawn into a temporary data directory. No API calls."""

import json
from pathlib import Path

import pytest
from PIL import Image

from app import app_settings, ask, practice, read_about, session_track
from app.config import get_settings
from app.importer import get_question
from conftest import PARENT_PIN
from revise_helpers import add_cards, make_card, point

POINTS_Q = "sci-test-points-q1"
TOPIC = "sci-07-particulate-nature"   # the made-up question's topic
OTHER_TOPIC = "sci-01-scientific-endeavour"
GOOD_ANSWER = "The balloon expands because the particles gain energy and move further apart."
NOW = "2026-10-20T00:00:00Z"

GAS = make_card("card-gas", "Describe what happens to a gas when it is heated.",
                {"format": "bullets", "points": [point("The balloon expands as particles gain energy.", ["expands"])]},
                topic=TOPIC, subject="science", command="describe")
CELL = make_card("card-cell", "Name the parts of a plant cell.",
                 {"format": "bullets", "points": [point("Cell wall, vacuole and chloroplast.", ["vacuole"])]},
                 topic=TOPIC, subject="science", command="name")
OFF_TOPIC = make_card("card-off", "Describe the balloon and the particles.",
                      {"format": "bullets", "points": [point("The balloon expands as particles move.", ["particles"])]},
                      topic=OTHER_TOPIC, subject="science", command="describe")


# ---- Seeding: a source file, its pages (with image files) and the staged card items ---------------------------

def _image(rel: str) -> None:
    path = Path(get_settings().data_dir) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 60), "white").save(path)


def add_file(conn, filename="notes_c09.pdf", mtype="school_notes", slug="made-up-notes", title="Made-up Notes",
             unit="09", layout="a4", pages=((1, "content", "3"), (2, "content", "4"), (3, "content", "")),
             raw=None, images=True) -> int:
    """A source file with pages [(page_no, page_type, printed page)]. Returns the file id. raw: {page_no: raw JSON}."""
    source = conn.execute("SELECT id FROM sources WHERE slug = ?", (slug,)).fetchone()
    source_id = source["id"] if source else conn.execute(
        "INSERT INTO sources (path, slug, title, subject, level, config_json, created_at, updated_at) "
        "VALUES (?, ?, ?, 'science', 'sec1', '{}', ?, ?)", (f"data/sources/science/sec1/{slug}", slug, title, NOW, NOW)).lastrowid
    file_id = conn.execute(
        "INSERT INTO source_files (source_id, filename, sha256, version, material_type, unit, status, page_count, "
        "created_at, layout) VALUES (?, ?, 'x', 1, ?, ?, 'done', ?, ?, ?)",
        (source_id, filename, mtype, unit, len(pages), NOW, layout)).lastrowid
    for page_no, page_type, printed in pages:
        rel = f"renders/science/sec1/{slug}/{Path(filename).stem}-v1/p{page_no:03d}.png"
        if images:
            _image(rel)
        conn.execute("INSERT INTO pages (file_id, page_no, render_path, page_type, printed_page, status, raw_json, "
                     "created_at) VALUES (?, ?, ?, ?, ?, 'done', ?, ?)",
                     (file_id, page_no, rel if images else None, page_type, printed,
                      json.dumps((raw or {}).get(page_no, {})), NOW))
    conn.commit()
    return file_id


def stage(conn, card_id: str, file_id: int, pages=(1,), status="approved") -> None:
    conn.execute("INSERT INTO staged_items (file_id, kind, item_id, status, record_json, page_refs_json, created_at, "
                 "updated_at) VALUES (?, 'card', ?, ?, '{}', ?, ?, ?)",
                 (file_id, card_id, status, json.dumps(list(pages)), NOW, NOW))
    conn.commit()


def passage(conn, file_id: int, page_no: int, text: str, topic=TOPIC, level="sec1") -> None:
    conn.execute("INSERT INTO reference_passages (file_id, subject, level, topic, source_ref, text, page_no, created_at) "
                 "VALUES (?, 'science', ?, ?, '', ?, ?, ?)", (file_id, level, topic, text, page_no, NOW))
    conn.commit()


@pytest.fixture
def notes(bank):
    """A notes file with GAS on page 2 (printed page 4) and CELL on page 1 (printed page 3)."""
    add_cards(bank, [CELL, GAS])
    file_id = add_file(bank)
    stage(bank, "card-cell", file_id, (1,))
    stage(bank, "card-gas", file_id, (2,))
    return file_id


def question(conn):
    return get_question(conn, POINTS_Q)


def submit(client, answer=GOOD_ANSWER):
    r = client.post(f"/q/{POINTS_Q}/answer", data={"answer": answer, "shown_at": ""}, follow_redirects=False)
    return int(r.headers["location"].split("/attempts/")[1].split("?")[0])


def with_help(conn) -> list:
    return [r["with_help"] for r in conn.execute("SELECT with_help FROM attempts ORDER BY id")]


def uses(conn) -> list:
    return [(r["stage"], r["attempt_id"]) for r in conn.execute("SELECT stage, attempt_id FROM reference_uses ORDER BY id")]


def parent_login(client):
    client.post("/parent/login", data={"pin": PARENT_PIN, "next": "/"}, follow_redirects=False)


def turn(conn, **changes):
    app_settings.save(conn, {**app_settings.get_all(conn), **changes})


# ---- 1. The lookup ------------------------------------------------------------------------------------------

def test_the_best_match_in_the_topic_comes_first(bank, notes):
    refs = read_about.references_for_question(bank, question(bank))
    assert [(r["file_id"], r["page_no"]) for r in refs][0] == (notes, 2)   # GAS shares the question's words


def test_only_notes_type_sources_are_used(bank):
    add_cards(bank, [GAS])
    notes_file = add_file(bank)
    stage(bank, "card-gas", notes_file, (2,))
    # The best match, but staged from a questions file, an answer key and an exam paper: never offered.
    for mtype, filename in (("questions", "qns_c09.pdf"), ("answer_key", "ans.pdf"), ("exam_paper_with_key", "exam.pdf")):
        other = add_file(bank, filename=filename, mtype=mtype, slug=f"slug-{mtype}")
        stage(bank, "card-gas", other, (1,))
        passage(bank, other, 1, "The balloon expands as particles gain energy and move further apart.")
    refs = read_about.references_for_question(bank, question(bank))
    assert {r["file_id"] for r in refs} == {notes_file}
    for mtype in ("revision_tables", "teaching_text"):   # these two are allowed
        ok = add_file(bank, filename=f"{mtype}.pdf", mtype=mtype, slug=f"slug-{mtype}")
        passage(bank, ok, 1, "The balloon expands as particles gain energy and move further apart.")
    refs = read_about.references_for_question(bank, question(bank), limit=10)
    assert {r["file_id"] for r in refs} >= {notes_file} and len({r["file_id"] for r in refs}) == 3


def test_a_card_that_never_came_from_a_file_gets_no_reference(bank):
    add_cards(bank, [GAS])   # imported, but with no staged item
    assert read_about.references_for_question(bank, question(bank)) == []


def test_only_the_questions_topic_is_searched(bank):
    add_cards(bank, [OFF_TOPIC])
    file_id = add_file(bank)
    stage(bank, "card-off", file_id, (1,))
    assert read_about.references_for_question(bank, question(bank)) == []


def test_the_study_level_filter_applies(bank):
    sec2 = {**GAS, "level": "sec2"}
    add_cards(bank, [sec2, CELL])
    file_id = add_file(bank)
    stage(bank, "card-gas", file_id, (2,))
    stage(bank, "card-cell", file_id, (1,))
    q = question(bank)
    assert [r["page_no"] for r in read_about.references_for_question(bank, q, "sec1")] == [1]   # CELL, by fallback
    assert [r["page_no"] for r in read_about.references_for_question(bank, q, "sec2")] == [2]
    assert [r["page_no"] for r in read_about.references_for_question(bank, q, "both")][0] == 2


def test_two_references_to_the_same_page_are_shown_once(bank):
    twin = {**GAS, "id": "card-gas-twin", "prompt": "Describe what a heated gas does."}
    add_cards(bank, [GAS, twin])
    file_id = add_file(bank)
    stage(bank, "card-gas", file_id, (2,))
    stage(bank, "card-gas-twin", file_id, (2,))
    passage(bank, file_id, 2, "The balloon expands as the particles gain energy.")
    refs = read_about.references_for_question(bank, question(bank))
    assert [(r["file_id"], r["page_no"]) for r in refs] == [(file_id, 2)]


def test_with_no_overlap_the_topics_first_card_is_the_fallback(bank):
    far = make_card("card-far", "Name a planet.", {"format": "bullets", "points": [point("Mars.", ["Mars"])]},
                    topic=TOPIC, subject="science", command="name")
    far2 = make_card("card-far2", "Name a moon.", {"format": "bullets", "points": [point("Titan.", ["Titan"])]},
                     topic=TOPIC, subject="science", command="name")
    add_cards(bank, [far, far2])
    file_id = add_file(bank)
    stage(bank, "card-far", file_id, (3,))
    stage(bank, "card-far2", file_id, (1,))
    refs = read_about.references_for_question(bank, question(bank))
    assert [r["page_no"] for r in refs] == [3]   # book order of the cards, not page order


def test_at_most_three_references(bank):
    cards = [make_card(f"card-{i}", f"Describe the balloon {i}.",
                       {"format": "bullets", "points": [point(f"The balloon expands {i}.", ["expands"])]},
                       topic=TOPIC, subject="science") for i in range(5)]
    add_cards(bank, cards)
    file_id = add_file(bank, pages=tuple((n, "content", str(n)) for n in range(1, 6)))
    for i, c in enumerate(cards, 1):
        stage(bank, c["id"], file_id, (i,))
    assert len(read_about.references_for_question(bank, question(bank))) == 3


def test_ties_go_to_page_order(bank):
    a = {**GAS, "id": "card-a", "prompt": "Balloon particles."}
    b = {**GAS, "id": "card-b", "prompt": "Balloon particles."}
    add_cards(bank, [b, a])
    file_id = add_file(bank)
    stage(bank, "card-a", file_id, (3,))
    stage(bank, "card-b", file_id, (1,))
    refs = read_about.references_for_question(bank, question(bank))
    assert [r["page_no"] for r in refs] == [1, 3]


def test_a_revise_card_lists_its_own_page_first(bank, notes):
    refs = read_about.references_for_card(bank, CELL)
    assert refs[0]["page_no"] == 1


# ---- 2. What a reference shows --------------------------------------------------------------------------------

def test_reference_text(bank, notes):
    ref = read_about.references_for_question(bank, question(bank))[0]
    assert ref["title"] == "Made-up Notes"
    assert ref["chapter"].startswith("Unit 9 · ")
    assert ref["printed_text"] == "p. 4" and ref["printed"] == "4"
    assert ref["detail"] == "notes_c09.pdf, file page 2" and ref["file_page"] == 2
    assert ref["image_url"] == f"/read/{notes}/page/2" and ref["viewer_url"] == f"/read/{notes}?p=2"


def test_a_missing_printed_page_says_so(bank, notes):
    other = make_card("card-third", "Describe the balloon and particles.",
                      {"format": "bullets", "points": [point("The balloon expands.", ["balloon"])]},
                      topic=TOPIC, subject="science")
    add_cards(bank, [other])
    stage(bank, "card-third", notes, (3,))   # page 3 has no printed number
    refs = read_about.references_for_question(bank, question(bank), limit=10)
    third = next(r for r in refs if r["page_no"] == 3)
    assert third["printed_text"] == "page not printed" == read_about.NO_PRINTED_PAGE


def test_a_slide_handout_reference_names_the_slide_and_the_file_page(bank):
    add_cards(bank, [GAS])
    file_id = add_file(bank, filename="slides_c02.pdf", mtype="teaching_text", layout="slides_4up",
                       pages=tuple((n, "content", "") for n in range(1, 7)))
    stage(bank, "card-gas", file_id, (6,))   # slide 6 is on file page 2
    ref = read_about.references_for_question(bank, question(bank))[0]
    assert ref["printed_text"] == "slide s06" and ref["detail"] == "slides_c02.pdf, file page 2, slide s06"


def test_a_page_without_a_kept_render_still_gives_the_book_reference(bank):
    add_cards(bank, [GAS])
    file_id = add_file(bank, images=False)
    stage(bank, "card-gas", file_id, (2,))
    ref = read_about.references_for_question(bank, question(bank))[0]
    assert ref["printed_text"] == "p. 4" and ref["image_url"] == "" and ref["viewer_url"] == ""


# ---- 3. The button: help only before submitting ------------------------------------------------------------

def test_before_submit_it_is_help(client, bank, notes):
    page = client.get(f"/q/{POINTS_Q}").text
    assert "Read about this" in page and f"/q/{POINTS_Q}/read" in page
    panel = client.post(f"/q/{POINTS_Q}/read", data={"scope": ""})
    assert panel.status_code == 200 and "Made-up Notes" in panel.text and "p. 4" in panel.text
    assert "page not printed" not in panel.text
    assert [r["tier"] for r in bank.execute("SELECT tier FROM hint_uses")] == [read_about.HINT_TIER] == [5]
    assert uses(bank) == [("before", None)]
    attempt_id = submit(client)
    assert with_help(bank) == [1]
    assert practice.hint_tiers(bank, attempt_id) == [5]


def test_before_submit_with_nothing_to_show_is_not_help(client, bank):
    assert "No notes pages found" in client.post(f"/q/{POINTS_Q}/read", data={}).text
    assert bank.execute("SELECT COUNT(*) FROM hint_uses").fetchone()[0] == 0
    assert uses(bank) == []
    submit(client)
    assert with_help(bank) == [0]


def test_after_submit_it_is_not_help(client, bank, notes):
    attempt_id = submit(client)
    page = client.get(f"/attempts/{attempt_id}").text
    assert "Read about this" in page and f"/attempts/{attempt_id}/read" in page
    assert "Made-up Notes" in client.post(f"/attempts/{attempt_id}/read", data={}).text
    assert uses(bank) == [("after", attempt_id)]
    assert bank.execute("SELECT COUNT(*) FROM hint_uses").fetchone()[0] == 0
    assert with_help(bank) == [0]


def test_in_revise_it_is_not_help(client, bank, notes):
    for card in ("card-gas", "card-cell"):
        r = client.post(f"/revise/cards/{card}/read", data={"set_id": ""})
        assert "Made-up Notes" in r.text
    assert [u[0] for u in uses(bank)] == ["revise", "revise"]
    assert bank.execute("SELECT COUNT(*) FROM help_views").fetchone()[0] == 0
    page = client.get("/revise/cards/card-gas").text
    assert "Read about this" in page and 'id="viewer"' in page


def test_every_read_is_logged_with_its_session(client, bank, notes):
    client.post(f"/q/{POINTS_Q}/read", data={"scope": "@s:7"})
    client.post(f"/q/{POINTS_Q}/read", data={"scope": "@s:7"})
    assert bank.execute("SELECT COUNT(*) FROM reference_uses WHERE session_id = 7").fetchone()[0] == 2
    assert bank.execute("SELECT COUNT(*) FROM hint_uses WHERE tier = 5").fetchone()[0] == 2
    assert session_track.read_counts(bank, 7) == {"before": 2, "after": 0, "revise": 0}


def test_the_button_is_not_on_the_home_screen(client, bank, notes):
    assert "Read about this" not in client.get("/").text


# ---- 3b. Parent settings -------------------------------------------------------------------------------------

def test_switching_it_off_hides_the_button_and_blocks_the_routes(client, bank, notes):
    attempt_id = submit(client)
    turn(bank, read_enabled=False)
    assert "Read about this" not in client.get(f"/q/{POINTS_Q}").text
    assert "Read about this" not in client.get(f"/attempts/{attempt_id}").text
    assert "Read about this" not in client.get("/revise/cards/card-gas").text
    assert client.post(f"/q/{POINTS_Q}/read", data={}).status_code == 403
    assert client.post(f"/attempts/{attempt_id}/read", data={}).status_code == 403
    assert client.post("/revise/cards/card-gas/read", data={}).status_code == 403
    assert client.get(f"/read/{notes}?p=1").status_code == 403
    assert client.get(f"/read/{notes}/page/1").status_code == 403
    assert uses(bank) == []


def test_no_reading_before_submitting_when_that_is_off(client, bank, notes):
    turn(bank, read_before_submit=False)
    assert "Read about this" not in client.get(f"/q/{POINTS_Q}").text
    assert client.post(f"/q/{POINTS_Q}/read", data={}).status_code == 403
    assert bank.execute("SELECT COUNT(*) FROM hint_uses").fetchone()[0] == 0
    attempt_id = submit(client)   # after submitting and in Revise it still works
    assert "Read about this" in client.get(f"/attempts/{attempt_id}").text
    assert client.post(f"/attempts/{attempt_id}/read", data={}).status_code == 200
    assert client.post("/revise/cards/card-gas/read", data={}).status_code == 200
    assert with_help(bank) == [0]


def test_the_settings_page_has_both_switches_on_by_default(client, bank):
    parent_login(client)
    page = client.get("/parent/settings").text
    assert 'name="read_enabled" checked' in page and 'name="read_before_submit" checked' in page
    form = {"cards_per_set": "4", "recall_method": "type", "recall_timing": "after_set", "interval_1": "1",
            "interval_2": "2", "interval_3": "4", "interval_4": "8", "interval_5": "16", "known_threshold": "80",
            "help_enabled": "on", "read_enabled": "on"}
    assert client.post("/parent/settings", data=form).status_code == 200
    saved = app_settings.get_all(bank)
    assert saved["read_enabled"] is True and saved["read_before_submit"] is False


# ---- 4. The chapter viewer -----------------------------------------------------------------------------------

def test_the_viewer_starts_at_the_page_and_steps_through(client, bank, notes):
    page = client.get(f"/read/{notes}?p=2").text
    assert "Page 2 of 3" in page and "Made-up Notes" in page
    assert f"/read/{notes}?p=1" in page and f"/read/{notes}?p=3" in page
    assert f'src="/read/{notes}/page/2"' in page
    assert "Page 3 of 3" in client.get(f"/read/{notes}?p=99").text   # past the end: the last page
    r = client.get(f"/read/{notes}/page/2")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_the_viewer_skips_pages_that_are_not_notes_content(client, bank):
    kinds = ("cover", "contents", "learning_outcomes", "content", "activity", "questions", "blank", "other",
             "glossary", "image_only")
    file_id = add_file(bank, pages=tuple((n, k, str(n)) for n, k in enumerate(kinds, 1)))
    assert read_about.viewable_pages(bank, file_id) == [3, 4, 9, 10]   # outcomes, content, glossary, image_only
    for n in (1, 2, 5, 6, 7, 8):
        assert client.get(f"/read/{file_id}/page/{n}").status_code == 404
    assert "Page 1 of 4" in client.get(f"/read/{file_id}?p=1").text   # starts at the first page it can show
    assert client.get(f"/read/{file_id}/page/4").status_code == 200


def test_a_notes_page_that_also_has_answers_is_never_served(client, bank):
    file_id = add_file(bank, raw={2: {"answers": [{"qnum": "1"}]}})
    assert read_about.viewable_pages(bank, file_id) == [1, 3]
    assert client.get(f"/read/{file_id}/page/2").status_code == 404


@pytest.mark.parametrize("mtype", ["answer_key", "exam_paper_with_key", "questions", "worksheets"])
def test_question_answer_and_exam_files_can_never_be_served(client, bank, mtype):
    file_id = add_file(bank, filename="other.pdf", mtype=mtype, slug=f"s-{mtype}")
    assert read_about.viewable_pages(bank, file_id) == []
    assert client.get(f"/read/{file_id}/page/1").status_code == 404
    assert client.get(f"/read/{file_id}?p=1").status_code == 404


def test_a_missing_file_or_image_is_a_404_not_an_error(client, bank, notes):
    assert client.get("/read/9999/page/1").status_code == 404
    assert client.get("/read/9999").status_code == 404
    assert client.get(f"/read/{notes}/page/50").status_code == 404
    path = Path(get_settings().data_dir) / "renders/science/sec1/made-up-notes/notes_c09-v1/p001.png"
    path.unlink()
    assert read_about.viewable_pages(bank, notes) == [2, 3]


def test_a_render_path_outside_the_renders_folder_is_not_served(client, bank):
    file_id = add_file(bank, images=False)
    secret = Path(get_settings().data_dir) / "secret.png"
    secret.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4)).save(secret)
    bank.execute("UPDATE pages SET render_path = 'secret.png' WHERE file_id = ?", (file_id,))
    bank.commit()
    assert client.get(f"/read/{file_id}/page/1").status_code == 404


def test_the_parent_sees_the_file_name_and_file_page(client, bank, notes):
    assert "notes_c09.pdf" not in client.post(f"/q/{POINTS_Q}/read", data={}).text
    parent_login(client)
    assert "notes_c09.pdf, file page 2" in client.post(f"/q/{POINTS_Q}/read", data={}).text


# ---- 5. Ask about this ---------------------------------------------------------------------------------------

@pytest.fixture
def api(client, monkeypatch):
    calls = []

    def fake(conn, **kwargs):
        calls.append(kwargs)
        return {"reply": "Think about the particles."}, {"model": "fake", "api_call_id": None,
                                                         "usage": {"input_tokens": 1, "output_tokens": 1}}

    monkeypatch.setattr("app.ask.structured_call", fake)
    return calls


def payload(call) -> str:
    return " ".join(b["text"] for b in call["system"] + call["content"] if b["type"] == "text")


def block(call) -> str:
    """Just the scope block (the system prompt also mentions <references>)."""
    return call["content"][0]["text"]


def say(client, url=f"/ask/question/{POINTS_Q}", key="abcdef123456"):
    return client.post(url, data={"key": key, "text": "Where do I look?"})


def test_ask_is_told_the_references_and_must_cite_only_those(client, bank, notes, api):
    turn(bank, ask_enabled=True)
    say(client)
    text = block(api[0])
    assert "<references>" in text and '"book": "Made-up Notes"' in text and '"printed_page": "p. 4"' in text
    assert "never invent" in payload(api[0]) and "Read about this" in payload(api[0])
    assert "Page text" not in text and "notes_c09.pdf" not in text   # titles, chapters and pages only


def test_ask_gets_no_references_when_there_are_none_or_the_feature_is_off(client, bank, api):
    turn(bank, ask_enabled=True)
    say(client)
    assert "<references>" not in block(api[0])


def test_ask_references_follow_the_settings(client, bank, notes, api):
    turn(bank, ask_enabled=True, read_before_submit=False)
    say(client)   # before submitting and not allowed: no pointer to the pages
    assert "<references>" not in block(api[0])
    attempt_id = submit(client)
    say(client, f"/ask/attempt/{attempt_id}", "key-after-1")   # a new page view, a new conversation
    assert "<references>" in block(api[1])
    turn(bank, read_enabled=False)
    say(client, f"/ask/attempt/{attempt_id}", "key-after-2")
    assert "<references>" not in block(api[2])


def test_ask_payload_for_a_card_has_the_references(client, bank, notes, api):
    turn(bank, ask_enabled=True)
    say(client, "/ask/card/card-cell")
    assert '"printed_page": "p. 3"' in block(api[0])


def test_ask_rules_are_unchanged_without_references(bank):
    scope = ask.question_scope(bank, question(bank))
    assert "mark_scheme" not in scope.payload and "<references>" not in scope.payload


# ---- 6. The parent's counts ---------------------------------------------------------------------------------

def test_the_dashboard_counts_reads_by_subject_and_stage(client, bank, notes):
    client.post(f"/q/{POINTS_Q}/read", data={})
    attempt_id = submit(client)
    client.post(f"/attempts/{attempt_id}/read", data={})
    client.post(f"/attempts/{attempt_id}/read", data={})
    client.post("/revise/cards/card-gas/read", data={})
    parent_login(client)
    page = client.get("/parent/dashboard").text
    assert "Before submitting (help)" in page and "<td>Science</td><td>1</td><td>2</td><td>1</td></tr>" in page
    assert "Read about this (before submitting)" in page   # also a column of the "Help me" tiers table


def test_the_session_log_shows_reads(client, bank, notes):
    bank.execute("INSERT INTO sessions (day, started_at, length_planned, plan_json) VALUES ('2026-10-20', ?, 30, '{}')", (NOW,))
    bank.commit()
    sid = bank.execute("SELECT id FROM sessions").fetchone()["id"]
    client.post(f"/q/{POINTS_Q}/read", data={"scope": f"@s:{sid}"})
    client.post("/revise/cards/card-gas/read", data={})   # not in a session: not counted here
    parent_login(client)
    page = client.get("/parent/sessions").text
    assert "<th>Read about this</th>" in page and "1 before submitting" in page


def test_the_parent_panel_names_tier_5(client, bank, notes):
    client.post(f"/q/{POINTS_Q}/read", data={})
    attempt_id = submit(client)
    parent_login(client)
    page = client.get(f"/attempts/{attempt_id}").text
    assert "with help" in page and "5 Read about this" in page
