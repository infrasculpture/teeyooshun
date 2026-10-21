"""Page images of questions: display renders, what the student may see, practice
exclusions, the figure in the marking call, and the review page. Made-up PDFs, no API calls."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import practice
from app.db import connect
from app.importer import get_question
from app.ingest import staging
from app.ingest.assemble import answer_format_of, build_question
from app.ingest.display import ensure_display_renders
from app.ingest.render import image_size, render_display, render_page
from app.config import get_settings
from app.marking.marker import FIGURE_NOTE, mark_answer
from conftest import PARENT_PIN
from ingest_helpers import SOURCE_YAML, FakeClient, a, all_responses, book, ingest, make_pdf, make_source, page, q

CFG = SimpleNamespace(subject="science", level="sec1", slug="s", title="T", key_reliability="assessment_book")
KNOWN = {"sci-01-scientific-endeavour"}


def _items():
    return connect().execute("SELECT * FROM staged_items WHERE status = 'pending' ORDER BY id").fetchall()


def _records():
    return {i["item_id"].rsplit("-", 1)[1]: json.loads(i["record_json"]) for i in _items()}


def _warnings():
    return {i["item_id"].rsplit("-", 1)[1]: json.loads(i["warnings_json"]) for i in _items()}


# ---- 1. Display renders ----------------------------------------------------------------

def test_display_render_is_sharper_than_the_model_copy(tmp_path):
    pdf = make_pdf(tmp_path / "x.pdf", ["one"])
    model = render_page(pdf, 1, tmp_path / "p001.png", 1568)
    display = render_display(pdf, 1, tmp_path / "display" / "p001.jpg", 2400)
    assert display.suffix == ".jpg" and max(image_size(display)) == 2400 > max(image_size(model))


def test_display_renders_are_made_for_question_pages_only(env):
    ingest(book(env), all_responses())
    conn = connect()
    rows = conn.execute("""SELECT f.filename, p.page_no, p.page_type, p.display_path FROM pages p
                           JOIN source_files f ON f.id = p.file_id ORDER BY f.filename, p.page_no""").fetchall()
    shown = {(r["filename"], r["page_no"]): r["display_path"] for r in rows}
    assert shown[("answers.pdf", 1)] is None and shown[("answers.pdf", 2)] is None  # answer pages
    assert shown[("qns_c01.pdf", 1)] is None  # cover
    assert shown[("qns_c01.pdf", 2)] == "renders/science/sec1/test-book/qns_c01-v1/display/p002.jpg"
    assert max(image_size(env / shown[("qns_c01.pdf", 3)])) == 2400


def test_missing_display_renders_can_be_made_later_from_the_pdf(env):
    folder = book(env)
    ingest(folder, all_responses())
    conn = connect()
    f = conn.execute("SELECT * FROM source_files WHERE filename = 'qns_c01.pdf'").fetchone()
    old = env / "renders" / "science" / "sec1" / "test-book" / "qns_c01-v1" / "display" / "p002.jpg"
    old.unlink()
    conn.execute("UPDATE pages SET display_path = NULL WHERE file_id = ?", (f["id"],))
    conn.commit()
    assert ensure_display_renders(conn, folder, "science/sec1/test-book", f, get_settings()) == 2
    assert old.exists()
    # A PDF that has changed since it was read is not used.
    conn.execute("UPDATE pages SET display_path = NULL WHERE file_id = ?", (f["id"],))
    conn.commit()
    make_pdf(folder / "qns_c01.pdf", ["different", "pages", "now"])
    lines = []
    assert ensure_display_renders(conn, folder, "science/sec1/test-book", f, get_settings(), lines.append) == 0
    assert "has changed since this version was read" in lines[0]


# ---- 2 and 3. Which pages a question shows -------------------------------------------------------

def test_question_lists_every_page_of_its_question_and_never_answer_pages(env):
    folder = make_source(env, "pages", SOURCE_YAML, {"qns_c01.pdf": ["Q1 stem, 1a", "1b 1c", "Solutions", "Q2"]})
    ingest(folder, [
        page(questions=[q("1", "", marks=0, question="Fig 1.1 shows a plant."), q("1", "a")]),
        page(questions=[q("", "b"), q("", "c")]),
        page("answer", answers=[a("1", "a"), a("1", "b"), a("1", "c"), a("2")]),
        page(questions=[q("2")]),
    ])
    records = _records()
    assert [i["page"] for i in records["q1a"]["page_images"]] == [1, 2]
    assert [i["page"] for i in records["q1c"]["page_images"]] == [1, 2]  # includes the stem's page
    assert [i["page"] for i in records["q2"]["page_images"]] == [4]
    image = records["q1a"]["page_images"][0]
    assert image["display"].endswith("display/p001.jpg") and image["model"].endswith("p001.png")
    assert records["q1c"]["question_label"] == "Q1(c)"
    assert all(i["page"] != 3 for r in records.values() for i in r["page_images"])


def test_question_page_that_also_has_solutions_is_not_shown(env):
    folder = make_source(env, "mixed", SOURCE_YAML, {"qns_c01.pdf": ["Q1 with its answer"]})
    ingest(folder, [page(questions=[q("1", figure="needed_to_answer")], answers=[a("1")])])
    assert "page_images" not in _records()["q1"]
    assert "Depends on a figure but has no page image to show" in _warnings()["q1"]


def test_worked_example_has_no_page_images_and_is_left_out_of_practice(env):
    folder = make_source(env, "we", SOURCE_YAML, {"qns_c01.pdf": ["Worked example"]})
    ingest(folder, [page(questions=[q("WE", "a")], answers=[])])
    record = _records()["qwea"]
    assert record["worked_example"] is True and "page_images" not in record
    assert record["question_label"] == "WE(a)"
    assert "Worked example: left out of practice (its page shows the solution)" in _warnings()["qwea"]
    record["mark_points"] = [{"id": "MP1", "text": "x"}, {"id": "MP2", "text": "y"}]
    conn = connect()
    from app.importer import import_questions
    import_questions(conn, [record])
    assert practice.next_question_id(conn, "science") is None
    assert practice.question_for_student(get_question(conn, record["id"]))["images"] == 0


# ---- 4. Answer format ---------------------------------------------------------------------

@pytest.mark.parametrize("item, expected", [
    (q("1", question="Draw a ray diagram to show the image.", answer_format=None), ("draw_or_label", True)),
    (q("1", question="Label the parts of the cell.", answer_format=None), ("draw_or_label", True)),
    (q("1", question="Complete the table below.", answer_format=None), ("table", True)),
    (q("1", question="Explain why the ice melts.", answer_format=None), ("text", False)),
    (q("1", question="Draw a conclusion from the results.", answer_format="text"), ("text", False)),  # model's word wins
    (q("1", question="Fill in the table.", answer_format="table"), ("table", False)),
])
def test_answer_format(item, expected):
    assert answer_format_of(item) == expected


def test_guessed_drawing_question_is_flagged_and_table_question_is_not_excluded():
    def build(item):
        item = dict(item, pages=[1], printed_page="1")
        return build_question(item, cfg=CFG, file_stem="qns_c01", file_topic="sci-01-scientific-endeavour",
                              scheme=a("1"), known_topics=KNOWN)
    record, warnings = build(q("1", question="Sketch the graph.", answer_format=None))
    assert record["answer_format"] == "draw_or_label"
    assert "Answer format guessed as 'draw_or_label' from the wording: check it" in warnings
    assert "Drawing or labelling question: left out of typed practice" in warnings
    record, warnings = build(q("1", question="Complete the table.", answer_format="table"))
    assert record["answer_format"] == "table" and warnings == []


# ---- 2 and 3 on screen ------------------------------------------------------------------------

@pytest.fixture
def approved(client, bank, tmp_path):
    """The made-up book ingested into the app's own database, with Q1(a) approved."""
    ingest(book(tmp_path / "data"), all_responses())
    item = bank.execute("SELECT * FROM staged_items WHERE item_id LIKE '%-q1a'").fetchone()
    staging.approve(bank, item["id"])
    return item["item_id"]


def test_question_screen_shows_text_first_and_the_page_as_an_expandable_thumbnail(client, approved):
    html = client.get(f"/q/{approved}").text
    text, image = html.index('<p class="qa-question">'), html.index(f"/q/{approved}/page/0")
    assert text < image  # the page image is never the first thing she sees
    assert html.count('class="thumb"') == 2  # Q1 runs over two pages of the book
    assert "openViewer" in html and "<dialog" in html  # a tap opens it full screen
    assert "Your answer to Q1(a)" in html and "<textarea" in html


def test_page_image_route_serves_question_pages_only(client, bank, approved):
    r = client.get(f"/q/{approved}/page/0")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert client.get(f"/q/{approved}/page/9").status_code == 404
    # Even if a record pointed at an answer page, it is refused: the page must be a question page.
    answer_render = bank.execute("""SELECT p.render_path FROM pages p JOIN source_files f ON f.id = p.file_id
                                    WHERE f.filename = 'answers.pdf' AND p.page_no = 1""").fetchone()["render_path"]
    record = json.loads(bank.execute("SELECT record_json FROM questions WHERE id = ?", (approved,)).fetchone()[0])
    record["page_images"] = [{"page": 1, "display": answer_render, "model": answer_render}]
    bank.execute("UPDATE questions SET record_json = ? WHERE id = ?", (json.dumps(record), approved))
    bank.commit()
    assert client.get(f"/q/{approved}/page/0").status_code == 404


def test_question_without_images_keeps_text_above_the_box(client):
    html = client.get("/q/sci-test-points-q1").text
    assert "/page/" not in html
    assert html.index("State what happens to the balloon") < html.index("<textarea")


# ---- 5. The figure in the marking call ----------------------------------------------------------

POINTS_OUTPUT = {"points": [{"id": "MP1", "awarded": True, "evidence_quote": "expands", "reason": "", "reversed": False},
                            {"id": "MP2", "awarded": False, "evidence_quote": "", "reason": "", "reversed": False}],
                 "missing_key_terms": [], "filler_quotes": [], "misread_question": False, "borderline": False,
                 "borderline_reason": "", "full_marks_answer": "", "technique_note": ""}


def _figure_question(points_question, tmp_path, figure):
    png = tmp_path / "data" / "renders" / "s" / "f-v1" / "p002.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    points_question.pop("answers", None)
    return dict(points_question, figure=figure,
                page_images=[{"page": 2, "display": "renders/s/f-v1/display/p002.jpg", "model": "renders/s/f-v1/p002.png"}])


def test_marking_call_includes_the_page_image_for_figure_questions(bank, points_question, tmp_path):
    client = FakeClient([POINTS_OUTPUT])
    result = mark_answer(bank, _figure_question(points_question, tmp_path, "needed_to_answer"),
                         "The balloon expands.", client=client)
    assert result["marks_awarded"] == 1
    content = client.requests[0]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": FIGURE_NOTE}
    assert content[1]["type"] == "image" and content[1]["source"]["media_type"] == "image/png"
    assert content[2]["text"].startswith("<mark_scheme>") and content[2]["cache_control"] == {"type": "ephemeral"}
    assert content[3]["text"].startswith("<student_answer>") and "cache_control" not in content[3]


def test_marking_call_is_unchanged_for_questions_without_a_needed_figure(bank, points_question, tmp_path):
    client = FakeClient([POINTS_OUTPUT])
    mark_answer(bank, _figure_question(points_question, tmp_path, "described"), "The balloon expands.", client=client)
    content = client.requests[0]["messages"][0]["content"]
    assert [c["type"] for c in content] == ["text", "text"] and content[0]["text"].startswith("<mark_scheme>")


# ---- 6. Review page ---------------------------------------------------------------------

def test_review_item_shows_the_sharper_page_images(client, bank, approved):
    client.post("/parent/login", data={"pin": PARENT_PIN, "next": "/"}, follow_redirects=False)
    item = bank.execute("SELECT * FROM staged_items WHERE item_id LIKE '%-q1b'").fetchone()
    html = client.get(f"/review/items/{item['id']}").text
    assert html.count("/review/render/") == 2 and "2 page image(s) shown to the student" in html
    page_id = bank.execute("""SELECT p.id FROM pages p JOIN source_files f ON f.id = p.file_id
                              WHERE f.filename = 'qns_c01.pdf' AND p.page_no = 2""").fetchone()["id"]
    r = client.get(f"/review/render/{page_id}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


# ---- 7. A page thumbnail on every question screen (made-up pages written straight to the database) ----------

def _book_page(conn, page_no, *, page_type="content", answers=False, stem="made-up"):
    """A page of a made-up book with its picture on disk. Returns the picture's path under data/."""
    rel = f"renders/science/sec1/{stem}/display/p{page_no:03d}.jpg"
    path = Path(get_settings().data_dir) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8fake")
    now = "2026-01-01T00:00:00Z"
    source = conn.execute("SELECT id FROM sources WHERE path = ?", (stem,)).fetchone()
    source_id = source["id"] if source else conn.execute(
        "INSERT INTO sources (path, slug, title, subject, level, config_json, created_at, updated_at) "
        "VALUES (?, ?, 'T', 'science', 'sec1', '{}', ?, ?)", (stem, stem, now, now)).lastrowid
    file = conn.execute("SELECT id FROM source_files WHERE source_id = ?", (source_id,)).fetchone()
    file_id = file["id"] if file else conn.execute(
        "INSERT INTO source_files (source_id, filename, sha256, version, material_type, status, created_at) "
        "VALUES (?, 'qns.pdf', '', 1, 'questions', 'done', ?)", (source_id, now)).lastrowid
    conn.execute("INSERT INTO pages (file_id, page_no, render_path, display_path, page_type, status, raw_json, created_at) "
                 "VALUES (?, ?, ?, ?, ?, 'done', ?, ?)",
                 (file_id, page_no, rel, rel, page_type, json.dumps({"answers": [{"qnum": "1"}]} if answers else {}), now))
    conn.commit()
    return rel, file_id


def _with_pages(base, *rels, **changes):
    record = dict(base, **changes)
    record["page_images"] = [{"page": n, "display": rel, "model": rel} for n, rel in enumerate(rels, 1)]
    return record


def _add(conn, *questions):
    from app.importer import import_questions
    import_questions(conn, list(questions))


@pytest.mark.parametrize("figure", ["none", "described", "needed_to_answer"])
def test_thumbnail_is_shown_whenever_the_record_has_page_references(client, bank, figure):
    from session_helpers import mk
    rel, _ = _book_page(bank, 2)
    _add(bank, _with_pages(mk("pg-q", "sci-01-scientific-endeavour"), rel, figure=figure))
    html = client.get("/q/pg-q").text
    assert html.count('class="thumb"') == 1 and "/q/pg-q/page/0" in html
    assert client.get("/q/pg-q/page/0").status_code == 200


def test_an_answer_page_is_never_shown(client, bank):
    from session_helpers import mk
    answer_page, _ = _book_page(bank, 3, page_type="answer")
    mixed_page, _ = _book_page(bank, 4, answers=True)  # a question page that also has solutions on it
    _add(bank, _with_pages(mk("pg-ans", "sci-01-scientific-endeavour"), answer_page, mixed_page))
    assert "/page/" not in client.get("/q/pg-ans").text
    assert client.get("/q/pg-ans/page/0").status_code == 404 and client.get("/q/pg-ans/page/1").status_code == 404


def test_a_question_approved_before_page_images_existed_finds_its_pages_from_the_source_references(client, bank):
    from session_helpers import mk
    page2, file_id = _book_page(bank, 2)
    _book_page(bank, 3, page_type="answer")
    old = mk("pg-old", "sci-01-scientific-endeavour")  # no page_images in its record
    _add(bank, old)
    bank.execute("INSERT INTO staged_items (file_id, kind, item_id, qnum, part, status, record_json, page_refs_json, "
                 "created_at, updated_at) VALUES (?, 'question', 'pg-old', '1', 'a', 'approved', '{}', '[2, 3]', 'x', 'x')",
                 (file_id,))
    bank.commit()
    html = client.get("/q/pg-old").text
    assert html.count('class="thumb"') == 1  # page 3 is an answer page and is left out
    assert client.get("/q/pg-old/page/0").status_code == 200 and client.get("/q/pg-old/page/1").status_code == 404


def test_a_generated_question_shows_the_page_of_its_origin_card(client, bank):
    from revise_helpers import add_cards, make_card, point
    from session_helpers import mk
    rel, _ = _book_page(bank, 5, stem="notes")
    card = make_card("card-pg", "Define diffusion.", {"format": "bullets", "points": [point("Movement of particles.")]},
                     topic="sci-01-scientific-endeavour", subject="science",
                     page_images=[{"page": 5, "display": rel, "model": rel}])
    add_cards(bank, [card])
    _add(bank, mk("gen-pg", "sci-01-scientific-endeavour", source="generated") | {"origin_card_id": "card-pg"},
         mk("gen-nopg", "sci-01-scientific-endeavour", source="generated") | {"origin_card_id": "card-missing"})
    assert client.get("/q/gen-pg").text.count('class="thumb"') == 1
    assert client.get("/q/gen-pg/page/0").status_code == 200
    html = client.get("/q/gen-nopg").text  # its card has no page image: nothing at all, no broken link
    assert "/page/" not in html and 'class="thumb"' not in html and "<dialog" not in html


def test_no_link_when_the_picture_file_is_missing(client, bank):
    from session_helpers import mk
    rel, _ = _book_page(bank, 6)
    (Path(get_settings().data_dir) / rel).unlink()
    _add(bank, _with_pages(mk("pg-gone", "sci-01-scientific-endeavour"), rel))
    assert "/page/" not in client.get("/q/pg-gone").text


def test_thumbnail_goes_above_the_context_when_the_question_mentions_a_figure(client, bank):
    from session_helpers import mk
    rel, _ = _book_page(bank, 7)
    plain = _with_pages(mk("pg-plain", "sci-01-scientific-endeavour"), rel, context="CONTEXT-MARKER")
    figure = _with_pages(mk("pg-fig", "sci-01-scientific-endeavour"), rel, context="CONTEXT-MARKER",
                         question="Look at the graph and state the trend.")
    _add(bank, plain, figure)
    html = client.get("/q/pg-plain").text
    assert html.index("CONTEXT-MARKER") < html.index("/q/pg-plain/page/0")
    html = client.get("/q/pg-fig").text
    assert html.index('<p class="qa-question">') < html.index("/q/pg-fig/page/0") < html.index("CONTEXT-MARKER")


@pytest.mark.parametrize("text, expected", [
    ("Fig. 1.1 shows a plant.", True), ("Look at the photograph.", True), ("Study the map.", True),
    ("Complete Table 2.", True), ("Use Source A.", True), ("In the diagram, label X.", True),
    ("Plot a graph.", True), ("Name the process.", False), ("It is unstable.", False),
])
def test_figure_words(text, expected):
    from app.page_images import mentions_figure
    assert mentions_figure({"question": text}) is expected


def test_schema_has_the_new_columns(conn):
    assert "display_path" in {r["name"] for r in conn.execute("PRAGMA table_info(pages)")}
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(questions)")}
    assert {"answer_format", "worked_example"} <= columns
