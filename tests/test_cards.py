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


# ---- Fixes from History rev_u01 ------------------------------------------------------

HEADERS = ["Year", "Event", "Result"]


def _stitch(*pages):
    return cards.stitch_cards([(n, str(n), card_page(list(rows))) for n, rows in enumerate(pages, 1)])


def test_empty_prompt_at_the_top_of_a_page_joins_its_rows_to_the_same_table():
    items = _stitch(
        [card("Describe the treaties.", "table", table=(HEADERS, [["1819", "Treaty A", "Post set up"]]))],
        # No header row is printed overleaf, so the model returns the first data row as "headers".
        [card("", "table", table=(["1824", "Treaty B", "Island ceded"], [["1826", "Grouping C", "Settlements joined"]])),
         card("Explain the free port.", points=[pt("No taxes on trade.")])],
    )
    assert [i["prompt"] for i in items] == ["Describe the treaties.", "Explain the free port."]
    assert items[0]["table"] == {"headers": HEADERS, "rows": [
        ["1819", "Treaty A", "Post set up"], ["1824", "Treaty B", "Island ceded"], ["1826", "Grouping C", "Settlements joined"]]}
    assert items[0]["pages"] == [1, 2] and "joined_other_format" not in items[0]


def test_repeated_header_row_is_not_added_as_data():
    items = _stitch([card("Describe the treaties.", "table", table=(HEADERS, [["1819", "A", "x"]]))],
                    [card("", "table", table=(["year", "EVENT", "Result"], [["1824", "B", "y"]]))])
    assert items[0]["table"]["rows"] == [["1819", "A", "x"], ["1824", "B", "y"]]


@pytest.mark.parametrize("previous_more, says_continues, expected_cards", [
    (False, False, 2),  # previous card complete and the model says this is new: kept separate
    (True, False, 1),   # previous card was cut off: joined
    (False, True, 1),   # the model says it continues: joined
    (None, False, 1),   # older stored reading with no "cut off" information: joined
])
def test_top_of_page_card_stays_separate_only_when_the_model_says_both(previous_more, says_continues, expected_cards):
    items = _stitch([card("Explain rain.", points=[pt("A")], more=previous_more)],
                    [card("", points=[pt("B")], cont=says_continues)])
    assert len(items) == expected_cards
    if expected_cards == 1:
        assert [p["text"] for p in items[0]["points"]] == ["A", "B"]


@pytest.mark.parametrize("prompt", ["(continued)", "cont'd", "Continued:", "(cont.)", "  "])
def test_a_prompt_that_only_says_continued_counts_as_empty(prompt):
    assert cards.clean_prompt(prompt) == ""
    assert len(_stitch([card("Explain rain.", points=[pt("A")])], [card(prompt, points=[pt("B")])])) == 1


def test_real_prompts_are_not_mistaken_for_continued():
    assert cards.clean_prompt("Describe continued trade growth.") == "Describe continued trade growth."


def test_continuation_in_a_different_format_is_flagged():
    items = _stitch([card("Outline the events.", "timeline", events=[("1819", "Post set up.", [])])],
                    [card("", "bullets", points=[pt("1824: a treaty is signed.")])])
    assert len(items) == 1 and items[0]["joined_other_format"] == "bullets"
    _record, warnings = cards.build_card(items[0], cfg=CFG, card_id="x", topic="geo-intro", known_topics=KNOWN)
    assert warnings == ["Joined with a continuation the model read as 'bullets': check nothing from the next page is missing"]
    assert warning_type(warnings[0]) == "Joined across pages in a different format (check)"


def test_timeline_page_with_no_question_column_is_one_card_across_pages():
    heading = "Timeline of important events"
    items = _stitch(
        [card(heading, "timeline", section=heading, command="", more=True,
              events=[("1299", "A made-up settlement is founded.", ["settlement"])])],
        [card("", "timeline", section="", command="", cont=True, events=[("1400", "A made-up port grows.", ["port"])])],
    )
    assert len(items) == 1
    record, warnings = cards.build_card(items[0], cfg=CFG, card_id="x", topic="geo-intro", known_topics=KNOWN)
    assert warnings == [] and record["prompt"] == heading and record["section"] == heading
    assert [e["date"] for e in record["answer"]["events"]] == ["1299", "1400"]


def test_prompt_says_pages_without_a_question_column_are_content():
    text = prompts.system_blocks("revision_tables", "history")[0]["text"]
    assert "even with no question column" in text and '"other" only for pages with nothing to learn from' in text
    assert 'A page of revision content with no question column is still revision content, never "other"' in text
    assert 'format is "timeline" for date and event pairs, otherwise "list" or "bullets"' in text
    assert "continues_on_next_page" in cards.CARD_ITEM["properties"]


# ---- The artefacts-table case: a table ending one page and carrying on at the top of the next ----

ARTEFACT_HEADERS = ["Artefact", "What it tells us"]


def _artefacts(first_card_next_page, more=None):
    """A made-up two-column table card at the bottom of page 17, then page 18's first card."""
    start = card("Describe the made-up artefacts.", "table", command="describe", more=more,
                 table=(ARTEFACT_HEADERS, [["Clay pot", "People stored food."]]))
    log = []
    items = cards.stitch_cards([(17, "17", card_page([start])),
                                (18, "18", card_page([first_card_next_page, card("Explain trade.", points=[pt("X")])]))],
                               log)
    return items, log


def _continuation(prompt, command="", cont=False, headers=(), fmt="table"):
    return card(prompt, fmt, command=command, cont=cont, section="",
                table=(list(headers), [["Glass bead", "People traded."], ["Coin", "People used money."]]))


ALL_ROWS = [["Clay pot", "People stored food."], ["Glass bead", "People traded."], ["Coin", "People used money."]]


@pytest.mark.parametrize("prompt, cont", [
    ("", False),                      # a truly empty left column
    ("(blank)", False),               # a placeholder the model wrote for the empty cell
    ("N/A", False),
    ("(continued from previous page)", False),
    ("Artefacts", False),             # some other text, but a table with no command word and the same columns
    ("Artefacts", True),              # the model marked it as continuing
])
def test_table_continuation_at_the_top_of_a_page_joins_the_same_table(prompt, cont):
    items, log = _artefacts(_continuation(prompt, cont=cont))
    assert [i["prompt"] for i in items] == ["Describe the made-up artefacts.", "Explain trade."]
    assert items[0]["table"] == {"headers": ARTEFACT_HEADERS, "rows": ALL_ROWS}
    assert items[0]["pages"] == [17, 18] and items[0]["command_word"] == "describe"
    assert log[0]["page"] == 18 and log[0]["joined"] is True and log[0]["prompt_as_read"] == prompt


def test_first_data_row_returned_as_headers_is_kept_when_joining_by_shape():
    items, _ = _artefacts(card("Artefacts", "table", command="", section="",
                               table=(["Glass bead", "People traded."], [["Coin", "People used money."]])))
    assert items[0]["table"]["rows"] == ALL_ROWS


def test_dropped_left_column_is_reported_for_review():
    items, _ = _artefacts(_continuation("Artefacts"))
    _record, warnings = cards.build_card(items[0], cfg=CFG, card_id="x", topic="geo-intro", known_topics=KNOWN)
    assert warnings == ["Joined with a row from the next page whose left column read 'Artefacts': "
                        "check it belongs to this card"]
    assert warning_type(warnings[0]) == "Joined across pages, left column dropped (check)"
    items, _ = _artefacts(_continuation("(blank)"))  # a placeholder is not worth a warning
    assert cards.build_card(items[0], cfg=CFG, card_id="x", topic="geo-intro", known_topics=KNOWN)[1] == []


@pytest.mark.parametrize("next_card, why", [
    (card("Describe the tools.", "table", command="describe", section="", table=(ARTEFACT_HEADERS, [["Axe", "Cutting."]])),
     "it has its own prompt and is not marked as continuing"),                  # a real new row: has a command word
    (card("Tools", "table", command="", section="", table=(["A", "B", "C"], [["1", "2", "3"]])),
     "it has its own prompt and is not marked as continuing"),                  # a different table shape
    (card("Tools", "bullets", command="", section="", points=[pt("Axe")]),
     "it has its own prompt and is not marked as continuing"),                  # not a table
])
def test_a_new_row_at_the_top_of_a_page_stays_separate(next_card, why):
    items, log = _artefacts(next_card)
    assert len(items) == 3 and items[0]["table"]["rows"] == [["Clay pot", "People stored food."]]
    assert log[0]["joined"] is False and log[0]["reason"] == why


def test_blank_prompt_stays_separate_only_if_the_model_says_previous_complete_and_not_continuing():
    items, log = _artefacts(_continuation("", cont=False), more=False)
    assert len(items) == 3 and log[0]["joined"] is False
    items, _ = _artefacts(_continuation("", cont=False), more=True)
    assert len(items) == 2


@pytest.mark.parametrize("text", ["(blank)", "blank", "[empty]", "none", "N/A", "n/a", "-", "—", "...", "No prompt",
                                  "(not repeated)", "Table continued", "continued from the previous page", "Cont'd."])
def test_placeholders_count_as_no_prompt(text):
    assert cards.clean_prompt(text) == ""


@pytest.mark.parametrize("text", ["Describe the artefacts.", "None of the rivers flow north: explain.", "Blank maps"])
def test_real_prompts_are_kept(text):
    assert cards.clean_prompt(text) == text


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
