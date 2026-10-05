"""Revision tables through the ingest command: staging, stable IDs, splitting, the row check,
summary and reassemble. Made-up PDFs, fake API client."""

import json

from app.db import connect
from app.ingest import staging
from app.ingest.reassemble import main as reassemble
from app.ingest.summary import file_summary, format_summary
from ingest_helpers import REVISION_YAML, card, card_page, ingest, make_source, pt

CLIMATE = card("Define climate.", points=[pt("The average weather over a long period.", ["average weather"])])
RIVERS = card("Locate the three rivers on the map.", "image", command="locate", alt="A made-up map of three rivers.",
              box=[100, 200, 900, 800])
EVENTS = card("Outline the events.", "timeline", events=[("1819", "A trading post is set up.", ["trading post"])],
              section="Second made-up section")


def guide(env, pages=("Contents", "Table 1", "Table 2"), yaml_text=REVISION_YAML):
    return make_source(env, "test-guide", yaml_text, {"rev_u01.pdf": list(pages)})


def responses():
    return [card_page(page_type="other", type_reason="contents page"),
            card_page([CLIMATE, RIVERS], printed="2"),
            card_page([EVENTS], printed="3")]


def pending():
    return connect().execute("SELECT * FROM staged_items WHERE status = 'pending' ORDER BY id").fetchall()


def records():
    return {json.loads(i["record_json"])["prompt"]: (i, json.loads(i["record_json"])) for i in pending()}


def summary_text():
    conn = connect()
    file_id = conn.execute("SELECT id FROM source_files WHERE status != 'superseded'").fetchone()["id"]
    return "\n".join(format_summary(file_summary(conn, file_id)))


def test_revision_tables_become_staged_cards(env):
    code, client, lines = ingest(guide(env), responses())
    assert code == 0 and len(client.requests) == 3
    assert "revision guide" in client.requests[0]["system"][0]["text"]
    assert "Table heading on the previous page: Made-up section." in client.messages_text(2)
    items = pending()
    assert [i["kind"] for i in items] == ["card", "card", "card"]
    item, record = records()["Define climate."]
    assert item["item_id"] == record["id"] and record["id"].startswith("geography-sec1-test-guide-rev_u01-p2-")
    assert record["topic"] == "geo-intro" and record["source"] == "revision_guide"
    assert json.loads(item["warnings_json"]) == [] and json.loads(item["page_refs_json"]) == [2]
    _, rivers = records()["Locate the three rivers on the map."]
    assert rivers["answer"]["image"] == {"page_render": "renders/test-guide/rev_u01-v1/display/p002.jpg",
                                         "bbox": [100, 200, 900, 800]}
    assert rivers["recall"] == "self_tick_only"
    assert records()["Outline the events."][1]["section"] == "Second made-up section"
    conn = connect()
    assert conn.execute("SELECT COUNT(*) FROM revision_cards").fetchone()[0] == 0  # nothing live before approval
    assert conn.execute("SELECT COUNT(*) FROM key_entries").fetchone()[0] == 0


def test_summary_accounts_for_every_row(env):
    ingest(guide(env), responses())
    text = summary_text()
    assert "rev_u01.pdf (revision_tables, version 1, done)" in text
    assert "Table rows the model counted: 3. Card entries it returned: 3. Cards after joining rows across pages: 3. " \
           "Cards now: 3 (0 made by splitting long answers)." in text
    assert "Cards by format: bullets 1, image 1, timeline 1" in text
    assert "p1 other: contents page" in text and "CHECK" not in text
    assert "Numbering check" not in text and "Questions without an answer" not in text


def test_row_count_mismatch_is_flagged(env):
    pages = responses()
    pages[1] = card_page([CLIMATE, RIVERS], rows=3)  # the model saw three rows but returned two
    ingest(guide(env), pages)
    assert "CHECK: the model counted 4 table rows but returned 3 entries" in summary_text()


def test_ids_stay_the_same_on_a_reread_even_when_the_row_moves_page(env):
    folder = guide(env)
    ingest(folder, responses())
    first = {prompt: rec["id"] for prompt, (_i, rec) in records().items()}
    # Read again: the climate row is now found on page 3 and the rest are unchanged.
    moved = [card_page(page_type="other", type_reason="contents page"), card_page([RIVERS]), card_page([CLIMATE, EVENTS])]
    ingest(folder, moved, force=True)
    again = {prompt: rec["id"] for prompt, (_i, rec) in records().items()}
    assert again == first and "-p2-" in again["Define climate."]  # the ID it was first given
    assert all(json.loads(i["warnings_json"]) == [] for i in pending())


def test_edited_prompt_keeps_its_id_and_is_recognised_next_time(env):
    folder = guide(env)
    ingest(folder, responses())
    conn = connect()
    item, record = records()["Define climate."]
    staging.save_edit(conn, item["id"], json.dumps(dict(record, prompt="Define the term climate.", id="something-else")),
                      {"geo-intro"})
    edited = conn.execute("SELECT * FROM staged_items WHERE id = ?", (item["id"],)).fetchone()
    assert edited["item_id"] == record["id"] and json.loads(edited["record_json"])["id"] == record["id"]
    # A later read recognises the card by its edited wording...
    ingest(folder, [card_page(page_type="other"), card_page([card("Define the term climate.", points=[pt("X")])]),
                    card_page([])], force=True)
    assert records()["Define the term climate."][1]["id"] == record["id"]
    # ...and still by the wording it was first read with.
    ingest(folder, [card_page(page_type="other"), card_page([CLIMATE]), card_page([])], force=True)
    assert records()["Define climate."][1]["id"] == record["id"]


def test_near_match_gets_a_new_id_and_a_warning(env):
    folder = guide(env)
    ingest(folder, responses())
    old_id = records()["Define climate."][1]["id"]
    ingest(folder, [card_page(page_type="other"), card_page([card("Define the climate.", points=[pt("X")])]),
                    card_page([])], force=True)
    item, record = records()["Define the climate."]
    assert record["id"] != old_id
    warnings = json.loads(item["warnings_json"])
    assert warnings == [f"Prompt is close to existing card {old_id} but not the same: a new ID was given, "
                        f"so check whether it is the same card"]


def test_long_answer_is_staged_as_sub_cards_with_stable_ids(env):
    long_card = card("Explain the causes of floods.", "grouped", groups=[
        ("Natural", [pt(f"n{i}") for i in range(5)]), ("Human", [pt(f"h{i}") for i in range(5)])])
    folder = guide(env, pages=("Table",))
    ingest(folder, [card_page([long_card])])
    items = pending()
    ids = [i["item_id"] for i in items]
    assert len(ids) == 2 and ids[0].endswith("a") and ids[1].endswith("b") and ids[0][:-1] == ids[1][:-1]
    assert all("Split from a long answer: check it" in json.loads(i["warnings_json"]) for i in items)
    assert json.loads(items[0]["record_json"])["prompt"] == "Explain the causes of floods. (Natural)"
    assert "Cards now: 2 (2 made by splitting long answers)" in summary_text()
    lines = []
    assert reassemble([str(folder), "--apply"], out=lines.append) == 0
    assert [i["item_id"] for i in pending()] == ids  # rebuilt from stored pages with the same IDs


def test_reassemble_preview_changes_nothing_for_cards(env):
    folder = guide(env)
    ingest(folder, responses())
    before = [tuple(r) for r in connect().execute("SELECT id, status, item_id, record_json FROM staged_items ORDER BY id")]
    lines = []
    assert reassemble([str(folder)], out=lines.append) == 0
    text = "\n".join(lines)
    assert "rev_u01.pdf: pending items 3 -> 3" in text and "No changes to pending items." in text
    assert "Preview only: nothing was changed." in text
    after = [tuple(r) for r in connect().execute("SELECT id, status, item_id, record_json FROM staged_items ORDER BY id")]
    assert after == before


def test_file_without_a_units_entry_gives_unassigned_cards(env):
    folder = make_source(env, "test-guide", REVISION_YAML, {"rev_u09.pdf": ["Table"]})
    ingest(folder, [card_page([CLIMATE])])
    item = pending()[0]
    assert json.loads(item["record_json"])["topic"] == "unassigned"
    assert "Topic is unassigned" in json.loads(item["warnings_json"])
    assert "no 'units' entry for 09" in connect().execute("SELECT warnings_json FROM source_files").fetchone()[0]


def test_dry_run_lists_revision_files(env):
    code, client, lines = ingest(guide(env), [], dry=True)
    assert code == 0 and client.requests == []
    assert any("rev_u01.pdf: revision_tables, 3 pages, new" in line for line in lines)
