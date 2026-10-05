"""Revision cards: formats, stitching, splitting, records, checks and IDs. Plain functions, made-up data."""

from types import SimpleNamespace

import pytest

from app.ingest import cards, prompts
from app.ingest.assemble import warning_type
from ingest_helpers import card, card_page, pt

CFG = SimpleNamespace(subject="geography", level="sec1", slug="test-guide", title="Test Revision Guide")
KNOWN = {"geo-intro"}
IMAGES = [{"page": 2, "display": "renders/test-guide/rev_u01-v1/display/p002.jpg",
           "model": "renders/test-guide/rev_u01-v1/p002.png"}]


def build(raw, images=None, topic="geo-intro", card_id="card-1"):
    item = cards.stitch_cards([(2, "7", card_page([raw]))])[0]
    return cards.build_card(item, cfg=CFG, card_id=card_id, topic=topic, images=images, known_topics=KNOWN)


# ---- Formats ---------------------------------------------------------------------

def test_bullets_keep_bold_words_as_key_terms_and_indented_points_as_sub():
    record, warnings = build(card("Define climate.", points=[
        pt("Climate is the average weather over a long period.", ["average weather", "long period"],
           sub=[("Usually 30 years.", ["30 years"])])]))
    assert warnings == []
    assert record["answer"] == {"format": "bullets", "points": [
        {"text": "Climate is the average weather over a long period.", "key_terms": ["average weather", "long period"],
         "sub": [{"text": "Usually 30 years.", "key_terms": ["30 years"]}]}]}
    assert record["recall"] == "typed_or_paper" and record["source"] == "revision_guide"
    assert record["section"] == "Made-up section" and record["command_word"] == "define"
    assert record["source_ref"] == "Test Revision Guide, p.7" and record["topic"] == "geo-intro"


def test_list_format_keeps_its_points_in_order():
    record, _ = build(card("List the steps.", "list", points=[pt("First"), pt("Second")]))
    assert record["answer"]["format"] == "list" and [p["text"] for p in record["answer"]["points"]] == ["First", "Second"]


def test_grouped_format():
    record, warnings = build(card("Describe two effects of heavy rain.", "grouped", groups=[
        ("Flooding", [pt("Drains overflow.", ["drains overflow"])]), ("Transport delays", [pt("Traffic slows.")])]))
    assert warnings == []
    assert [g["heading"] for g in record["answer"]["groups"]] == ["Flooding", "Transport delays"]
    assert record["answer"]["groups"][0]["points"][0]["key_terms"] == ["drains overflow"]


def test_table_format_has_headers_rows_and_flat_key_terms():
    record, warnings = build(card("Compare weather and climate.", "table",
                                  table=(["", "Weather", "Climate"], [["Time", "short", "long"]]), table_terms=["long"]))
    assert warnings == []
    assert record["answer"] == {"format": "table", "key_terms": ["long"],
                                "table": {"headers": ["", "Weather", "Climate"], "rows": [["Time", "short", "long"]]}}


def test_timeline_format():
    record, warnings = build(card("Outline the events.", "timeline",
                                  events=[("1819", "A trading post is set up.", ["trading post"]), ("1824", "A treaty.", [])]))
    assert warnings == []
    assert record["answer"]["events"][0] == {"date": "1819", "text": "A trading post is set up.", "key_terms": ["trading post"]}


def test_image_card_points_at_the_page_and_is_self_tick_only():
    record, warnings = build(card("Locate the three rivers on the map.", "image", command="locate",
                                  alt="A made-up map with three rivers.", box=[120, 340, 880, 900]), images=IMAGES)
    assert warnings == []
    assert record["answer"] == {"format": "image", "alt": "A made-up map with three rivers.",
                                "image": {"page_render": IMAGES[0]["display"], "bbox": [120, 340, 880, 900]}}
    assert record["recall"] == "self_tick_only"


def test_image_box_outside_the_page_is_left_out_and_missing_page_image_warns():
    record, warnings = build(card("Locate X.", "image", alt="A map.", box=[0, 0, 1500, 900]))
    assert "bbox" not in record["answer"]["image"]
    assert "Image card has no page image" in warnings


def test_examples_and_remarks():
    record, _ = build(card("Define climate.", points=[pt("Average weather.")],
                           examples=["Singapore is hot and wet all year."], notes="Remark: see Unit 2."))
    assert record["examples"] == ["Singapore is hot and wet all year."] and record["notes"] == "Remark: see Unit 2."
    record, _ = build(card("Define weather.", points=[pt("Conditions now.")]))
    assert "examples" not in record and "notes" not in record


# ---- Stitching ---------------------------------------------------------------------

def test_row_continuing_onto_the_next_page_joins_its_card():
    items = cards.stitch_cards([
        (1, "1", card_page([card("Define climate.", points=[pt("One")]), card("Explain rain.", points=[pt("A")])])),
        (2, "2", card_page([card("", points=[pt("B")], cont=True, section="", examples=["An example."]),
                            card("Describe wind.", points=[pt("C")], section="")])),
    ])
    assert [i["prompt"] for i in items] == ["Define climate.", "Explain rain.", "Describe wind."]
    assert [p["text"] for p in items[1]["points"]] == ["A", "B"] and items[1]["examples"] == ["An example."]
    assert items[1]["pages"] == [1, 2]
    assert items[2]["section"] == "Made-up section"  # no heading on page 2: the last heading is kept


def test_first_row_with_no_prompt_is_a_continuation_even_if_not_marked():
    items = cards.stitch_cards([
        (1, "1", card_page([card("Explain rain.", "grouped", groups=[("Cause", [pt("A")])])])),
        (2, "2", card_page([card("", "grouped", groups=[("Effect", [pt("B")])])])),
    ])
    assert len(items) == 1 and [g["heading"] for g in items[0]["groups"]] == ["Cause", "Effect"]


def test_same_row_on_a_shared_boundary_page_is_dropped():
    row = card("Define climate.", points=[pt("One")])
    items = cards.stitch_cards([(1, "1", card_page([row])), (2, "1", card_page([row]))])
    assert len(items) == 1 and items[0]["pages"] == [1, 2]


def test_stitching_does_not_change_the_stored_page_output():
    first, second = card_page([card("Explain rain.", points=[pt("A")])]), card_page([card("", points=[pt("B")], cont=True)])
    cards.stitch_cards([(1, "1", first), (2, "2", second)])
    assert [p["text"] for p in first["cards"][0]["points"]] == ["A"]


# ---- Splitting ---------------------------------------------------------------------

def _points(n, prefix="P"):
    return [pt(f"{prefix}{i}") for i in range(1, n + 1)]


def test_long_grouped_answer_splits_by_group():
    item = cards.stitch_cards([(1, "1", card_page([card("Explain the causes.", "grouped", groups=[
        ("Natural", _points(5)), ("Human", _points(5))], examples=["Eg."])]))])[0]
    subs = cards.split_long(item)
    assert [s["prompt"] for s in subs] == ["Explain the causes. (Natural)", "Explain the causes. (Human)"]
    assert [s["split_letter"] for s in subs] == ["a", "b"] and all(s["split_from"] == "Explain the causes." for s in subs)
    assert subs[0]["format"] == "bullets" and len(subs[0]["points"]) == 5 and subs[0]["examples"] == ["Eg."]
    record, warnings = cards.build_card(subs[1], cfg=CFG, card_id="x-b", topic="geo-intro", known_topics=KNOWN)
    assert record["split_from"] == "Explain the causes." and warnings == [cards.SPLIT_WARNING]


def test_long_bulleted_answer_with_sub_points_splits_by_top_level_point():
    points = [pt("Cause", sub=[(f"c{i}", []) for i in range(4)]), pt("Effect", sub=[(f"e{i}", []) for i in range(4)])]
    item = cards.stitch_cards([(1, "1", card_page([card("Explain floods.", points=points)]))])[0]
    assert cards.count_points(item) == 10
    subs = cards.split_long(item)
    assert [s["prompt"] for s in subs] == ["Explain floods. (part 1 of 2)", "Explain floods. (part 2 of 2)"]
    assert subs[0]["points"][0]["text"] == "Cause" and len(subs[0]["points"][0]["sub"]) == 4


def test_long_flat_list_is_kept_whole_with_a_warning():
    item = cards.stitch_cards([(1, "1", card_page([card("List the countries.", "list", points=_points(12))]))])[0]
    whole = cards.split_long(item)
    assert len(whole) == 1 and whole[0]["long_unsplit"] == 12
    _record, warnings = cards.build_card(whole[0], cfg=CFG, card_id="x", topic="geo-intro", known_topics=KNOWN)
    assert warnings == ["Long answer (12 points) could not be split: split it by hand or approve as it is"]


def test_eight_points_is_not_long():
    item = cards.stitch_cards([(1, "1", card_page([card("List.", "list", points=_points(8))]))])[0]
    assert cards.split_long(item) == [item]


# ---- Checks and warnings -------------------------------------------------------------

def test_card_without_prompt_or_answer_warns_and_cannot_be_approved():
    record, warnings = build(card("", points=[]))
    assert "Card has no prompt" in warnings and "Card has no answer point" in warnings
    problems = cards.validate_card(record)
    assert "'prompt' must be a non-empty string" in problems and "the answer is empty" in problems


def test_topic_and_unsure_warnings():
    _, warnings = build(card("Define X.", points=[pt("Y")], uncertain=True, reason="bottom of the row is cut off"),
                        topic="unassigned")
    assert warnings == ["Topic is unassigned", "Model unsure about the card: bottom of the row is cut off"]
    assert warning_type(warnings[1]) == "Model unsure (card)"


def test_good_card_passes_the_checks():
    record, _ = build(card("Define X.", points=[pt("Y")]))
    assert cards.validate_card(record) == []
    assert cards.validate_card(dict(record, recall="sometimes")) == ["'recall' must be one of ['typed_or_paper', 'self_tick_only']"]


# ---- IDs ---------------------------------------------------------------------

def test_card_id_is_built_from_the_page_and_a_hash_of_the_normalised_prompt():
    key = cards.norm_prompt("  Define   CLIMATE. ")
    assert key == "define climate" == cards.norm_prompt("Define climate")
    card_id = cards.make_card_id(CFG, "rev_u01", 3, key)
    assert card_id == f"geography-sec1-test-guide-rev_u01-p3-{cards.prompt_hash(key)}"
    assert len(cards.prompt_hash(key)) == 8


def test_near_match_finds_close_but_not_equal_prompts():
    existing = {"define climate": "id-1", "explain how rain forms": "id-2"}
    assert cards.near_match("define the climate", existing) == "define climate"
    assert cards.near_match("define climate", existing) is None  # equal is not a near-match
    assert cards.near_match("describe the water cycle", existing) is None


# ---- Prompt and schema -------------------------------------------------------------

def test_revision_pages_use_the_card_prompt_and_schema():
    text = prompts.system_blocks("revision_tables", "geography")[0]["text"]
    assert "revision guide" in text and '"content" for a page with a revision table' in text
    assert 'the words printed in bold' in text and '"For example" block' in text and '"Remark" box' in text
    assert "parts per thousand of the page width and height" in text
    schema = prompts.schema_for("revision_tables")
    assert "cards" in schema["properties"] and "table_rows" in schema["properties"]
    assert "questions" in prompts.schema_for("questions")["properties"]
