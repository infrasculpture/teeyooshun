"""Page images live in a folder per source (subject/level/slug), and the repair command moves
images stored the old way (slug only). Made-up PDFs, no API calls."""

import hashlib
import json

from app.db import connect
from app.ingest import staging
from app.ingest.rerender import run
from app.revision_cards import get_card
from ingest_helpers import REVISION_YAML, card, card_page, ingest, make_source, pt

HISTORY_YAML = REVISION_YAML.replace("geography", "history").replace("geo-intro", "his-u1-temasek-to-singapore")
NEW = "renders/geography/sec1/test-guide/rev_u01-v1/"
OLD = "renders/test-guide/rev_u01-v1/"


def _one_card_page():
    return [card_page([card("Define a made-up term.", points=[pt("Its meaning.")])])]


def test_two_sources_with_the_same_folder_and_file_names_keep_separate_images(env):
    ingest(make_source(env, "test-guide", REVISION_YAML, {"rev_u01.pdf": ["Geography table"]}), _one_card_page())
    ingest(make_source(env / "other", "test-guide", HISTORY_YAML, {"rev_u01.pdf": ["History table"]}), _one_card_page())
    paths = [r["display_path"] for r in connect().execute("SELECT display_path FROM pages ORDER BY id")]
    assert paths == [NEW + "display/p001.jpg", "renders/history/sec1/test-guide/rev_u01-v1/display/p001.jpg"]
    assert all((env / p).exists() for p in paths)


def _legacy(env):
    """An ingested, approved card whose images are stored the old way, under the slug only."""
    folder = make_source(env, "test-guide", REVISION_YAML, {"rev_u01.pdf": ["Geography table"]})
    ingest(folder, _one_card_page())
    conn = connect()
    item = conn.execute("SELECT * FROM staged_items WHERE kind = 'card'").fetchone()
    staging.approve(conn, item["id"])
    conn.execute("UPDATE pages SET render_path = replace(render_path, ?, ?), display_path = replace(display_path, ?, ?)",
                 (NEW, OLD, NEW, OLD))
    for table in ("staged_items", "revision_cards", "revision_card_versions"):
        conn.execute(f"UPDATE {table} SET record_json = replace(record_json, ?, ?)", (NEW, OLD))
    conn.commit()
    wrong = env / OLD / "display" / "p001.jpg"   # another source's page, written over this one's
    wrong.parent.mkdir(parents=True)
    wrong.write_bytes(b"another book")
    return folder, conn, item["item_id"]


def test_preview_changes_nothing(env):
    folder, conn, card_id = _legacy(env)
    lines = []
    assert run(folder, out=lines.append) == 0
    assert "1 page image(s) would be drawn again" in lines[0] and "Preview only" in lines[-1]
    assert get_card(conn, card_id)["page_images"][0]["display"] == OLD + "display/p001.jpg"


def test_apply_redraws_from_this_sources_pdf_and_repoints_everything(env):
    folder, conn, card_id = _legacy(env)
    (env / NEW / "display" / "p001.jpg").unlink()
    lines = []
    assert run(folder, apply=True, out=lines.append) == 0
    assert "Done: 1 page image(s)" in lines[-1]

    page = conn.execute("SELECT render_path, display_path FROM pages").fetchone()
    assert (page["render_path"], page["display_path"]) == (NEW + "p001.png", NEW + "display/p001.jpg")
    assert (env / page["display_path"]).read_bytes()[:2] == b"\xff\xd8"  # a real JPEG, drawn from the PDF
    record = get_card(conn, card_id)
    assert record["page_images"] == [{"page": 1, "display": NEW + "display/p001.jpg", "model": NEW + "p001.png"}]
    assert record["version"] == 1  # same card, same version
    staged = json.loads(conn.execute("SELECT record_json FROM staged_items").fetchone()[0])
    assert staged["page_images"][0]["display"].startswith(NEW)
    for table in ("revision_cards", "revision_card_versions"):
        row = conn.execute(f"SELECT record_json, content_hash FROM {table}").fetchone()
        assert OLD not in row["record_json"]
        assert row["content_hash"] == hashlib.sha256(row["record_json"].encode("utf-8")).hexdigest()
    assert (env / OLD / "display" / "p001.jpg").read_bytes() == b"another book"  # old files are left alone

    again = []
    assert run(folder, apply=True, out=again.append) == 0
    assert "nothing to do" in again[0] and "already in its own folder" in again[-1]


def test_a_pdf_that_has_gone_is_reported_not_guessed(env):
    folder, conn, _ = _legacy(env)
    (folder / "rev_u01.pdf").unlink()
    lines = []
    assert run(folder, apply=True, out=lines.append) == 0
    assert "NOT moved" in lines[0]
    assert conn.execute("SELECT display_path FROM pages").fetchone()[0].startswith(OLD)
