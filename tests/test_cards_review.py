"""Revision cards on the review page: list, preview with page images, approve into
revision_cards with versions, reject, checked edits, parent PIN. Fake API only."""

import json

import pytest

from app.revision_cards import get_card, get_card_version
from conftest import PARENT_PIN
from ingest_helpers import REVISION_YAML, card, card_page, ingest, make_source, pt

CLIMATE = card("Define climate.", points=[pt("The average weather over a long period.", ["average weather"])],
               examples=["Singapore is hot and wet all year."], notes="Remark: see Unit 2.")
RIVERS = card("Locate the three rivers on the map.", "image", command="locate", alt="A made-up map of three rivers.")
COMPARE = card("Compare weather and climate.", "table", table=(["", "Weather", "Climate"], [["Time", "short", "long"]]),
               table_terms=["long"])


@pytest.fixture
def staged_cards(client, bank, tmp_path):
    """A made-up revision file ingested into the app's own database. Returns {prompt: staged item row}."""
    folder = make_source(tmp_path / "data", "test-guide", REVISION_YAML, {"rev_u01.pdf": ["Table 1"]})
    ingest(folder, [card_page([CLIMATE, RIVERS, COMPARE])])
    rows = bank.execute("SELECT * FROM staged_items WHERE kind = 'card' ORDER BY id").fetchall()
    return {json.loads(r["record_json"])["prompt"]: r for r in rows}


def login(client):
    client.post("/parent/login", data={"pin": PARENT_PIN, "next": "/"}, follow_redirects=False)


def test_card_review_needs_the_pin(client, staged_cards):
    item = staged_cards["Define climate."]
    assert client.get(f"/review/items/{item['id']}", follow_redirects=False).status_code == 303
    assert client.post(f"/review/items/{item['id']}/approve").status_code == 403


def test_file_page_lists_cards(client, staged_cards):
    login(client)
    item = staged_cards["Define climate."]
    html = client.get(f"/review/files/{item['file_id']}").text
    assert "Define climate." in html and "Locate the three rivers on the map." in html
    assert "Made-up section" in html and "bullets" in html and "image" in html


def test_card_preview_shows_the_page_image_key_terms_examples_and_remark(client, staged_cards):
    login(client)
    html = client.get(f"/review/items/{staged_cards['Define climate.']['id']}").text
    assert "/review/render/" in html  # the page image
    assert "The <mark>average weather</mark> over a long period." in html
    assert "Singapore is hot and wet all year." in html and "Remark: see Unit 2." in html
    assert "recall: typed_or_paper" in html
    picture = client.get(f"/review/items/{staged_cards['Locate the three rivers on the map.']['id']}").text
    assert "Picture card." in picture and "A made-up map of three rivers." in picture and "recall: self_tick_only" in picture
    table = client.get(f"/review/items/{staged_cards['Compare weather and climate.']['id']}").text
    assert "<strong>Weather</strong>" in table and "<mark>long</mark>" in table


def test_approve_writes_the_card_and_a_changed_card_gets_the_next_version(client, bank, staged_cards, tmp_path):
    login(client)
    item = staged_cards["Define climate."]
    r = client.post(f"/review/items/{item['id']}/approve", follow_redirects=False)
    assert r.status_code == 303
    stored = get_card(bank, item["item_id"])
    assert stored["version"] == 1 and stored["prompt"] == "Define climate." and stored["answer"]["format"] == "bullets"
    row = bank.execute("SELECT * FROM staged_items WHERE id = ?", (item["id"],)).fetchone()
    assert row["status"] == "approved" and row["reviewed_by"] == "parent"
    assert bank.execute("SELECT COUNT(*) FROM questions WHERE id = ?", (item["item_id"],)).fetchone()[0] == 0
    # Read the file again with a corrected answer: same ID, and approving it makes version 2.
    changed = card("Define climate.", points=[pt("The average weather of a place over a long period.", ["average weather"])])
    ingest(tmp_path / "data" / "sources" / "science" / "sec1" / "test-guide", [card_page([changed])], force=True)
    again = bank.execute("SELECT * FROM staged_items WHERE status = 'pending' AND kind = 'card'").fetchone()
    assert again["item_id"] == item["item_id"]
    client.post(f"/review/items/{again['id']}/approve")
    assert get_card(bank, item["item_id"])["version"] == 2
    assert get_card_version(bank, item["item_id"], 1)["answer"]["points"][0]["text"] == "The average weather over a long period."


def test_reject_card(client, bank, staged_cards):
    login(client)
    item = staged_cards["Locate the three rivers on the map."]
    client.post(f"/review/items/{item['id']}/reject")
    assert bank.execute("SELECT status FROM staged_items WHERE id = ?", (item["id"],)).fetchone()[0] == "rejected"
    assert get_card(bank, item["item_id"]) is None


def test_card_edit_is_checked_keeps_its_id_and_blocks_a_broken_card(client, bank, staged_cards):
    login(client)
    item = staged_cards["Define climate."]
    record = json.loads(item["record_json"])
    record["prompt"] = "Define the term climate."
    record["id"] = "tampered"
    client.post(f"/review/items/{item['id']}/edit", data={"record": json.dumps(record)})
    row = bank.execute("SELECT * FROM staged_items WHERE id = ?", (item["id"],)).fetchone()
    saved = json.loads(row["record_json"])
    assert row["edited"] == 1 and saved["prompt"] == "Define the term climate." and saved["id"] == item["item_id"]
    record["answer"] = {"format": "bullets", "points": []}
    client.post(f"/review/items/{item['id']}/edit", data={"record": json.dumps(record)})
    warnings = json.loads(bank.execute("SELECT warnings_json FROM staged_items WHERE id = ?", (item["id"],)).fetchone()[0])
    assert "Card has no answer point" in warnings and "Check: the answer is empty" in warnings
    r = client.post(f"/review/items/{item['id']}/approve")
    assert r.status_code == 422 and get_card(bank, item["item_id"]) is None


def test_approve_all_clean_approves_cards_without_warnings(client, bank, staged_cards):
    login(client)
    file_id = staged_cards["Define climate."]["file_id"]
    r = client.post(f"/review/files/{file_id}/approve-clean")
    assert "Approved 3 item(s) without warnings." in r.text
    assert bank.execute("SELECT COUNT(*) FROM revision_cards").fetchone()[0] == 3
