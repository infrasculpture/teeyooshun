"""Stage C: school notes and slide handouts -> derived revision cards, reference passages, outcomes.
Made-up PDFs, fake API client, no real sources (INGESTION_STAGE_C_SPEC.md section 10)."""

import json

import pytest
from PIL import Image, ImageDraw

from app.db import connect, init_db
from app.ingest import cards, notes, staging
from app.ingest.slides import split_page
from app.revision_cards import get_card, import_cards
from app.sources import SourceError, file_options_for, load_source
from conftest import PARENT_PIN
from ingest_helpers import card, ingest, make_source, pt

NOTES_YAML = """title: Test Notes
subject: geography
level: sec1
files:
  "notes_*": school_notes
key_reliability: school
file_options:
  "notes_*":
    topic: geo-intro
"""

NO_TOPIC_YAML = """title: Test Notes
subject: geography
level: sec1
files:
  "notes_*": school_notes
key_reliability: school
"""

SLIDES_YAML = """title: Test Notes
subject: geography
level: sec1
files:
  "notes_*": school_notes
key_reliability: school
file_options:
  "notes_weather-climate.pdf":
    layout: slides_4up
    slide_order: {order}
    topic: geo-intro
"""


def np(page_type="content", cards_=(), glossary=(), outcomes=(), passages=(), blanks=False, divider=False,
       section="", printed="", reason="", cover_other=False):
    """One page or slide of school-notes model output."""
    return {"page_type": page_type, "page_type_reason": reason, "rotation_needed": 0,
            "cover_has_other_content": cover_other, "printed_page_number": printed, "has_blanks": blanks,
            "is_divider": divider, "section": section, "cards": list(cards_),
            "glossary": [{"term": t, "description": d, "topic": "", "topic_uncertain": False} for t, d in glossary],
            "outcomes": [{"text": t, "topic": ""} for t in outcomes],
            "passages": [{"text": t, "topic": ""} for t in passages],
            "uncertain": False, "uncertain_reason": ""}


def nc(prompt, topic="", topic_uncertain=False, **kwargs):
    """A card as the model returns it for notes: a revision-table card plus its topic."""
    return {**card(prompt, **kwargs), "topic": topic, "topic_uncertain": topic_uncertain}


def make_4up_pdf(path, pages):
    """A PDF whose pages are four-up handouts. pages: [(TL, TR, BL, BR)] of booleans: has something on it."""
    images = []
    for inked in pages:
        im = Image.new("RGB", (1240, 1754), "white")
        draw = ImageDraw.Draw(im)
        for quarter, on in enumerate(inked):
            if on:
                x, y = quarter % 2 * 620, quarter // 2 * 877
                draw.rectangle((x + 100, y + 100, x + 500, y + 700), fill="black")
        images.append(im)
    images[0].save(path, save_all=True, append_images=images[1:], resolution=150)


def pending(conn=None):
    return (conn or connect()).execute("SELECT * FROM staged_items WHERE status = 'pending' ORDER BY id").fetchall()


def records():
    return {json.loads(i["record_json"])["prompt"]: (i, json.loads(i["record_json"])) for i in pending()}


def warnings_of(prompt):
    return json.loads(records()[prompt][0]["warnings_json"])


# ---- Cropping 4-up pages -------------------------------------------------------------------

COLOURS = {"TL": (200, 0, 0), "TR": (0, 150, 0), "BL": (0, 0, 200), "BR": (120, 0, 160)}


def quarter_image(tmp_path, which=("TL", "TR", "BL", "BR"), size=(1000, 1400)):
    im = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(im)
    for name in which:
        x, y = size[0] // 2 * (name in ("TR", "BR")), size[1] // 2 * (name in ("BL", "BR"))
        draw.rectangle((x + 150, y + 200, x + 350, y + 500), fill=COLOURS[name])
    png = tmp_path / "page.png"
    im.save(png)
    return png


def centres(paths):
    out = []
    for p in paths:
        if p is None:
            out.append(None)
            continue
        with Image.open(p) as im:
            out.append(im.getpixel((im.width // 2, im.height // 2)))
    return out


@pytest.mark.parametrize("order, expected", [
    ("down_then_across", ["TL", "BL", "TR", "BR"]),
    ("across_then_down", ["TL", "TR", "BL", "BR"]),
])
def test_four_up_crop_reading_orders(tmp_path, order, expected):
    paths = split_page(quarter_image(tmp_path), order, lambda i: tmp_path / f"s{i}.png", 2000)
    assert centres(paths) == [COLOURS[name] for name in expected]


def test_blank_quarters_are_skipped(tmp_path):
    png = quarter_image(tmp_path, which=("TL", "BR"))
    across = split_page(png, "across_then_down", lambda i: tmp_path / f"a{i}.png", 2000)
    down = split_page(png, "down_then_across", lambda i: tmp_path / f"d{i}.png", 2000)
    assert [p is None for p in across] == [False, True, True, False]  # TL, TR, BL, BR
    assert [p is None for p in down] == [False, True, True, False]  # TL, BL, TR, BR
    only_tr = quarter_image(tmp_path, which=("TR",))
    assert [p is None for p in split_page(only_tr, "across_then_down", lambda i: tmp_path / f"b{i}.png", 2000)] == \
           [True, False, True, True]
    assert [p is None for p in split_page(only_tr, "down_then_across", lambda i: tmp_path / f"c{i}.png", 2000)] == \
           [True, True, False, True]


def test_slides_are_shrunk_to_the_model_size_never_enlarged(tmp_path):
    big = split_page(quarter_image(tmp_path), "across_then_down", lambda i: tmp_path / f"s{i}.png", 400)
    with Image.open(big[0]) as im:
        assert max(im.size) == 400
    small = split_page(quarter_image(tmp_path), "across_then_down", lambda i: tmp_path / f"t{i}.png", 5000)
    with Image.open(small[0]) as im:
        assert max(im.size) < 1400


# ---- source.yaml ---------------------------------------------------------------------------

def source_with(tmp_path, options_yaml):
    folder = tmp_path / "src"
    folder.mkdir(exist_ok=True)
    (folder / "source.yaml").write_text(
        "title: T\nsubject: geography\nlevel: sec1\nfiles:\n  \"notes_*\": school_notes\nkey_reliability: school\n"
        + options_yaml, encoding="utf-8")
    return folder


def test_exact_file_name_beats_a_pattern(tmp_path):
    for first, second in (("glob", "exact"), ("exact", "glob")):
        blocks = {"glob": "  \"notes_*\":\n    topic: geo-glob\n",
                  "exact": "  \"notes_weather-climate.pdf\":\n    layout: slides_4up\n"
                           "    slide_order: across_then_down\n    topic: geo-exact\n"}
        cfg = load_source(source_with(tmp_path, "file_options:\n" + blocks[first] + blocks[second]))
        assert file_options_for(cfg, "notes_weather-climate.pdf") == {
            "layout": "slides_4up", "slide_order": "across_then_down", "topic": "geo-exact"}
        assert file_options_for(cfg, "notes_other.pdf") == {"layout": "a4", "slide_order": "", "topic": "geo-glob"}
        assert file_options_for(cfg, "elsewhere.pdf") == {"layout": "a4", "slide_order": "", "topic": None}


def test_slides_4up_without_slide_order_fails_validation(tmp_path):
    folder = source_with(tmp_path, "file_options:\n  \"notes_weather-climate.pdf\":\n    layout: slides_4up\n")
    with pytest.raises(SourceError, match="slides_4up needs 'slide_order'"):
        load_source(folder)
    code, client, lines = ingest(folder, [])
    assert code == 1 and any("slide_order" in line for line in lines) and not client.requests


def test_bad_layout_and_order_are_rejected(tmp_path):
    folder = source_with(tmp_path, "file_options:\n  \"notes_*\":\n    layout: poster\n    slide_order: sideways\n")
    with pytest.raises(SourceError) as error:
        load_source(folder)
    assert "'layout' must be one of" in str(error.value) and "'slide_order' must be one of" in str(error.value)


# ---- Names ---------------------------------------------------------------------------------

def test_name_lines_are_dropped():
    assert notes.drop_name_lines("Name: Alex Tan\nWater is a liquid.") == "Water is a liquid."
    assert notes.drop_name_lines("Class: 1A") == "" and notes.drop_name_lines("Index No. 12") == ""
    assert notes.drop_name_lines("Name ______") == ""
    for kept in ("Name the three states of matter.", "Class 1 lever", "Classification of matter", "Class"):
        assert notes.drop_name_lines(kept) == kept


def test_scrub_removes_emptied_entries_but_keeps_table_columns():
    cleaned = notes.scrub({"points": [{"text": "Class: 2A"}, {"text": "A real point"}],
                           "table": {"headers": ["Class", "Name: x"], "rows": [["a", "Name: b"], ["c", "d"]]}})
    assert cleaned["points"] == [{"text": "A real point"}]
    assert cleaned["table"] == {"headers": ["Class", ""], "rows": [["a", ""], ["c", "d"]]}


# ---- Page classes through the ingest command -----------------------------------------------

def notes_folder(env, yaml_text=NOTES_YAML, names=("notes_weather.pdf",), pages=7):
    return make_source(env, "test-notes", yaml_text, {n: [f"Page {i}" for i in range(1, pages + 1)] for n in names})


def class_responses():
    return [
        np("cover", reason="name box"),
        np("contents", reason="contents page"),
        np("learning_outcomes", outcomes=["Describe the weather.", "Name: Alex Tan"], printed="3"),
        np("glossary", glossary=[("Evaporation", "Water changing to vapour."), ("Condensation", "Vapour changing to water."),
                                 ("Humidity", "")], printed="4"),
        np("content", [nc("Define weather.", points=[pt("The day-to-day state of the atmosphere.", ["atmosphere"]),
                                                     pt("Name: Alex Tan"), pt("Class: 2A")]),
                       nc("State the formula for speed.", command="state",
                          points=[pt("speed = distance / time", ["speed"])])],
           passages=["Weather is the day-to-day state of the atmosphere.", "Name: ______"], blanks=True, printed="5"),
        np("activity", [nc("Should not appear.", points=[pt("Nothing.")])], reason="quick check"),
        np("questions", reason="exercise"),
    ]


def test_page_classes_route_to_the_right_place(env):
    code, client, lines = ingest(notes_folder(env), class_responses())
    assert code == 0 and len(client.requests) == 7
    system = client.requests[0]["system"][0]["text"]
    assert "school notes" in system and "Topic list" not in system  # the file's topic is set: no list
    by_prompt = records()
    assert set(by_prompt) == {"Define Evaporation.", "Define Condensation.", "Define weather.",
                              "State the formula for speed."}  # nothing from activity, questions or the blank Humidity

    _, evaporation = by_prompt["Define Evaporation."]  # the term is kept as printed
    assert evaporation["answer"] == {"format": "bullets", "points": [
        {"text": "Water changing to vapour.", "key_terms": ["Evaporation"]}]}
    assert evaporation["command_word"] == "define" and evaporation["derived"] is True
    assert evaporation["origin"] == "school_notes" and evaporation["topic"] == "geo-intro"
    assert evaporation["source_ref"] == "Test Notes, p.4"

    _, weather = by_prompt["Define weather."]
    assert [p["text"] for p in weather["answer"]["points"]] == ["The day-to-day state of the atmosphere."]
    assert "Alex" not in json.dumps(weather) and "2A" not in json.dumps(weather)

    # Every card of the first file from a new source needs a check; a page with blanks says so.
    for prompt in by_prompt:
        assert any(w.startswith("Derived card from a new source") for w in warnings_of(prompt))
    assert any(w.startswith("Has blanks:") for w in warnings_of("Define weather."))
    assert any(w.startswith("Has blanks:") for w in warnings_of("Define Evaporation."))  # Humidity had no description

    conn = connect()
    assert conn.execute("SELECT COUNT(*) FROM revision_cards").fetchone()[0] == 0  # nothing live before approval
    types = {r["page_no"]: (r["page_type"], r["status"], r["render_path"]) for r in conn.execute(
        "SELECT page_no, page_type, status, render_path FROM pages")}
    assert types[1][0] == "cover" and types[1][2] is None  # the render of a cover is deleted
    assert [types[n][:2] for n in (2, 6, 7)] == [("contents", "skipped"), ("activity", "skipped"), ("questions", "skipped")]
    assert "p6 activity: quick check" in "\n".join(_summary_lines(conn))


def _summary_lines(conn):
    from app.ingest.summary import file_summary, format_summary
    file_id = conn.execute("SELECT id FROM source_files WHERE status != 'superseded'").fetchone()["id"]
    return format_summary(file_summary(conn, file_id))


def test_outcomes_and_reference_passages_are_stored_with_the_cards(env):
    ingest(notes_folder(env), class_responses())
    conn = connect()
    outcomes = conn.execute("SELECT * FROM topic_outcomes").fetchall()
    assert [(o["text"], o["topic"], o["source_ref"], o["page_no"]) for o in outcomes] == [
        ("Describe the weather.", "geo-intro", "Test Notes, p.3", 3)]  # the name line is gone
    passages = conn.execute("SELECT * FROM reference_passages").fetchall()
    assert [(p["text"], p["subject"], p["level"], p["topic"], p["source_ref"], p["page_no"]) for p in passages] == [
        ("Weather is the day-to-day state of the atmosphere.", "geography", "sec1", "geo-intro", "Test Notes, p.5", 5)]
    assert passages[0]["page_render"].endswith("display/p005.jpg")
    assert len(pending(conn)) == 4
    everything = "\n".join(r["raw_json"] or "" for r in conn.execute("SELECT raw_json FROM pages"))
    assert "Alex" not in everything and "2A" not in everything  # not even in the stored model output


def test_rebuilding_replaces_passages_and_a_changed_file_drops_the_old_ones(env):
    folder = notes_folder(env)
    ingest(folder, class_responses())
    from app.ingest.reassemble import main as reassemble
    reassemble([str(folder), "--apply"], out=lambda _line: None)
    assert connect().execute("SELECT COUNT(*) FROM reference_passages").fetchone()[0] == 1
    ingest(folder, class_responses(), force=True)  # read again as version 2
    conn = connect()
    assert conn.execute("SELECT COUNT(*) FROM reference_passages").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM topic_outcomes").fetchone()[0] == 1


def test_model_picks_the_topic_when_the_file_has_none(env):
    pages = [np("content", [nc("Define weather.", topic="geo-intro", points=[pt("A.")]),
                            nc("Define climate.", topic="made-up-topic", points=[pt("B.")]),
                            nc("Define season.", topic="geo-intro", topic_uncertain=True, points=[pt("C.")])])]
    ingest(notes_folder(env, NO_TOPIC_YAML, pages=1), pages)
    by_prompt = records()
    assert by_prompt["Define weather."][1]["topic"] == "geo-intro"
    assert by_prompt["Define climate."][1]["topic"] == "unassigned"
    assert "The model suggested topic 'made-up-topic', which is not in topics.yaml" in warnings_of("Define climate.")
    assert any(w.startswith("Topic is uncertain") for w in warnings_of("Define season."))


def test_file_topic_is_set_on_every_card_and_raises_no_topic_warning(env):
    pages = [np("content", [nc("Define weather.", topic="made-up-topic", topic_uncertain=True, points=[pt("A.")]),
                            nc("Define climate.", topic="", topic_uncertain=True, points=[pt("B.")])])]
    _code, client, _lines = ingest(notes_folder(env, pages=1), pages)  # NOTES_YAML sets topic geo-intro
    by_prompt = records()
    assert {r["topic"] for _item, r in by_prompt.values()} == {"geo-intro"}
    for prompt in by_prompt:
        assert not any("opic" in w for w in warnings_of(prompt))  # no topic_uncertain, unassigned or unknown-topic warning
    assert "Topic list (id: title):" not in client.requests[0]["system"][0]["text"]


def test_topic_list_is_sent_only_when_the_model_chooses(env):
    _code, client, _lines = ingest(notes_folder(env, NO_TOPIC_YAML, pages=1), [np("content")])
    assert "Topic list (id: title):" in client.requests[0]["system"][0]["text"]


def test_derived_check_is_only_for_the_first_file_of_a_source(env):
    folder = notes_folder(env, names=("notes_a.pdf", "notes_b.pdf"), pages=1)
    page = [np("content", [nc("Define weather.", points=[pt("A.")])])]
    other = [np("content", [nc("Define climate.", points=[pt("B.")])])]
    ingest(folder, page + other)
    assert any(w.startswith("Derived card from a new source") for w in warnings_of("Define weather."))
    assert warnings_of("Define climate.") == []


# ---- Duplicates ----------------------------------------------------------------------------

def approved_card(card_id="geography-sec1-other-p1-abcd1234", prompt="Define weather", topic="geo-intro",
                  points=("The day to day state of the air.",)):
    return {"id": card_id, "subject": "geography", "level": "sec1", "source": "revision_guide", "topic": topic,
            "section": "", "source_ref": "Other, p.1", "command_word": "define", "prompt": prompt,
            "answer": {"format": "bullets", "points": [{"text": t, "key_terms": []} for t in points]},
            "recall": "typed_or_paper"}


def test_find_duplicate_by_prompt_or_by_points():
    approved = [("a", approved_card("a")), ("b", approved_card("b", prompt="Explain rain", points=("one", "two", "three")))]
    same_prompt = approved_card("new", prompt="Define weather.", points=("Something else entirely.",))
    assert cards.find_duplicate(same_prompt, approved) == "a"
    same_points = approved_card("new", prompt="Why does it rain?", points=("one", "two", "three", "four"))
    assert cards.find_duplicate(same_points, approved) == "b"
    different = approved_card("new", prompt="State the formula for speed.", points=("distance over time",))
    assert cards.find_duplicate(different, approved) == ""
    assert cards.find_duplicate(approved_card("a"), approved) == ""  # never itself


def test_likely_duplicates_are_flagged_and_not_stored_on_approval(env):
    init_db()
    conn = connect()
    import_cards(conn, [approved_card()])
    pages = [np("content", [nc("Define weather.", points=[pt("The day-to-day state of the atmosphere.")]),
                            nc("State the formula for speed.", points=[pt("speed = distance / time")])])]
    ingest(notes_folder(env, pages=1), pages)
    by_prompt = records()
    item, record = by_prompt["Define weather."]
    assert record["possible_duplicate"] == "geography-sec1-other-p1-abcd1234"
    assert any(w.startswith("Possible duplicate of approved card geography-sec1-other-p1-abcd1234")
               for w in warnings_of("Define weather."))
    assert "possible_duplicate" not in by_prompt["State the formula for speed."][1]
    staging.approve(connect(), item["id"])
    assert "possible_duplicate" not in get_card(connect(), record["id"])
    assert get_card(connect(), record["id"])["derived"] is True


# ---- Slide handouts ------------------------------------------------------------------------

def slides_folder(env, order="down_then_across", inked=((True, True, True, True), (True, False, False, False))):
    folder = make_source(env, "test-notes", SLIDES_YAML.format(order=order), {})
    make_4up_pdf(folder / "notes_weather-climate.pdf", inked)
    return folder


def slide_responses():
    return [
        np("content", divider=True, section="Element of weather: Temperature"),
        np("content", [nc("Define temperature.", section="", points=[pt("How hot or cold the air is.")], more=True)],
           passages=["Temperature is how hot or cold the air is."]),
        np("content", [nc("", section="", cont=True, command="", points=[pt("It is measured in degrees Celsius.")])]),
        np("content", [nc("Describe how temperature is measured.", command="describe", section="",
                          points=[pt("A thermometer is kept in a shaded screen.")])]),
        np("content", [nc("State the unit of rainfall.", command="state", section="Rainfall", points=[pt("mm")])]),
    ]


def test_slide_handout_is_read_slide_by_slide_and_blank_quarters_cost_nothing(env):
    folder = slides_folder(env)
    code, client, lines = ingest(folder, slide_responses())
    assert code == 0 and len(client.requests) == 5  # eight quarters, three blank
    assert "Slide s01 (file page 1) of 8 slides." in client.messages_text(0)
    assert "Section heading before this one: Element of weather: Temperature." in client.messages_text(1)
    conn = connect()
    pages = {r["page_no"]: r for r in conn.execute("SELECT * FROM pages ORDER BY page_no")}
    assert sorted(pages) == list(range(1, 9))
    assert [pages[n]["page_type"] for n in (6, 7, 8)] == ["blank", "blank", "blank"]
    assert all(pages[n]["render_path"] is None for n in (6, 7, 8)) and pages[2]["render_path"].endswith("s002.png")
    assert not any(p.name.startswith("page") for p in (env / "renders").rglob("*.png"))  # the full page is not kept
    file_row = conn.execute("SELECT * FROM source_files").fetchone()
    assert (file_row["layout"], file_row["slide_order"]) == ("slides_4up", "down_then_across")

    by_prompt = records()
    assert set(by_prompt) == {"Define temperature.", "Describe how temperature is measured.", "State the unit of rainfall."}
    item, temperature = by_prompt["Define temperature."]
    assert temperature["section"] == "Element of weather: Temperature"  # the divider slide sets it, and has no card
    assert [p["text"] for p in temperature["answer"]["points"]] == [
        "How hot or cold the air is.", "It is measured in degrees Celsius."]  # slides 2 and 3 joined
    assert json.loads(item["page_refs_json"]) == [2, 3]
    assert temperature["source_ref"] == "Test Notes, p.1, slide s02"
    assert by_prompt["State the unit of rainfall."][1]["section"] == "Rainfall"
    assert by_prompt["Describe how temperature is measured."][1]["section"] == "Element of weather: Temperature"
    assert conn.execute("SELECT page_no, source_ref FROM reference_passages").fetchone()["source_ref"] == \
           "Test Notes, p.1, slide s02"
    assert "display" not in pages[2]["render_path"]  # slide crops are shown as they are


def test_summary_labels_slides_by_sheet_and_lists_blank_quarters_as_blank(env):
    from app.ingest.summary import file_summary, format_summary
    ingest(slides_folder(env), slide_responses())
    conn = connect()
    text = "\n".join(format_summary(file_summary(conn, conn.execute("SELECT id FROM source_files").fetchone()["id"])))
    assert "Slides: 8" in text and "Slides not read:" in text
    assert "file pages" not in text and "Page numbers here are file pages" not in text
    for sheet, slide in ((2, "s06"), (2, "s07"), (2, "s08")):
        assert f"sheet {sheet}, slide {slide} blank: blank quarter" in text


def test_a_card_never_spans_more_than_three_slides():
    def page(prompt, cont):
        return {"cards": [dict(card(prompt, cont=cont, command="" if cont else "define", points=[pt(prompt or "more")]),
                               derived=True)]}
    pages = [(n, "", page("Define it." if n == 1 else "", n > 1)) for n in range(1, 6)]
    assert [c["pages"] for c in cards.stitch_cards(pages, max_pages=3)] == [[1, 2, 3], [4, 5]]
    assert [c["pages"] for c in cards.stitch_cards(pages)] == [[1, 2, 3, 4, 5]]


def test_changing_slide_order_reads_the_file_again(env):
    folder = slides_folder(env, "down_then_across")
    ingest(folder, slide_responses())
    code, client, lines = ingest(folder, slide_responses())
    assert any("unchanged since the last run" in line for line in lines) and not client.requests
    (folder / "source.yaml").write_text(SLIDES_YAML.format(order="across_then_down"), encoding="utf-8")
    code, client, lines = ingest(folder, slide_responses())
    assert any("(changed, version 2)" in line for line in lines) and len(client.requests) == 5
    conn = connect()
    assert conn.execute("SELECT slide_order FROM source_files WHERE version = 2").fetchone()[0] == "across_then_down"
    assert conn.execute("SELECT COUNT(*) FROM staged_items WHERE status = 'pending'").fetchone()[0] == 3


def test_dry_run_counts_slides_not_pages(env):
    folder = slides_folder(env)
    code, client, lines = ingest(folder, [], dry=True)
    assert code == 0 and not client.requests
    assert any("notes_weather-climate.pdf: school_notes, 8 pages" in line for line in lines)


# ---- Review pages --------------------------------------------------------------------------

def test_review_pages_show_slide_crops_and_the_duplicate(client, bank, tmp_path, monkeypatch):
    monkeypatch.setenv("INGEST_MODEL", "claude-sonnet-5-5")
    import_cards(bank, [approved_card(prompt="Define temperature", points=("How hot or cold the air is.",))])
    folder = slides_folder(tmp_path / "data")
    ingest(folder, slide_responses())
    client.post("/parent/login", data={"pin": PARENT_PIN, "next": "/"}, follow_redirects=False)
    file_id = connect().execute("SELECT id FROM source_files").fetchone()["id"]
    page = client.get(f"/review/files/{file_id}")
    assert page.status_code == 200
    assert "Slides in the order read (down then across)" in page.text
    assert "s05" in page.text and "Sheet 2, slide s06: blank" in page.text
    item = records()["Define temperature."][0]
    detail = client.get(f"/review/items/{item['id']}")
    assert detail.status_code == 200
    assert "Overlaps this approved card" in detail.text and "sheet 1, slide s02" in detail.text
    assert "Possible duplicate of approved card" in detail.text
