"""Move a source's page images to its own folder and draw them again from the PDFs (no API calls).

    python -m app.ingest.rerender data/sources/<subject>/<level>/<slug> [--apply]

Page images used to be stored under data/renders/<slug>/, so two sources whose folders have
the same name (say a History and a Geography "revision-guide") and the same file names wrote
over each other's images. They now live under data/renders/<subject>/<level>/<slug>/.
This command redraws every kept page of a source into the new place, with the rotation
recorded at ingest, and points the pages, staged items and approved questions and cards at it.
Nothing is read by the model again and no IDs, versions or attempts change.

Previews by default; --apply does it. Safe to run twice. Old images are left where they are.
"""

import argparse
import hashlib
import sys
from pathlib import Path

from app.config import get_settings
from app.db import connect, init_db
from app.ingest.render import file_sha256, render_display, render_page, rotate_clockwise
from app.sources import SourceError, load_source

# (table, id column) holding approved records and their earlier versions.
APPROVED = (("questions", "id"), ("question_versions", "question_id"),
            ("revision_cards", "id"), ("revision_card_versions", "card_id"))


def _swap(conn, table, where, params, old, new) -> None:
    """Replace the old image folder with the new one inside a table's stored records."""
    conn.execute(f"UPDATE {table} SET record_json = replace(record_json, ?, ?) WHERE {where}", [old, new] + params)
    rows = conn.execute(f"SELECT rowid AS rid, record_json FROM {table} WHERE {where}", params).fetchall()
    for r in rows:  # the hash is how a later import or approval tells whether a record changed
        digest = hashlib.sha256(r["record_json"].encode("utf-8")).hexdigest()
        conn.execute(f"UPDATE {table} SET content_hash = ? WHERE rowid = ?", (digest, r["rid"]))


def rerender_file(conn, cfg, f, settings, apply: bool, out) -> int:
    """Redraw one file version's pages into the source's own folder.

    Returns how many pages moved (or would move), or minus the number that couldn't be."""
    stem = Path(f["filename"]).stem
    version_dir = f"{stem}-v{f['version']}"
    new = f"renders/{cfg.render_key}/{version_dir}/"
    pages = conn.execute("SELECT id, page_no, rotation, render_path, display_path FROM pages WHERE file_id = ? "
                         "ORDER BY page_no", (f["id"],)).fetchall()
    todo = [p for p in pages if any(path and not path.startswith(new) for path in (p["render_path"], p["display_path"]))]
    label = f"{f['filename']} (version {f['version']})"
    if not todo:
        out(f"{label}: nothing to do")
        return 0
    pdf = Path(cfg.folder) / f["filename"]
    if not pdf.exists() or file_sha256(pdf) != f["sha256"]:
        out(f"{label}: {len(todo)} page(s) NOT moved, because the PDF for this version is no longer in the folder")
        return -len(todo)
    if not apply:
        out(f"{label}: {len(todo)} page image(s) would be drawn again under data/{new}")
        return len(todo)

    data_dir = Path(settings.data_dir)
    olds = set()
    for p in todo:
        render_path, display_path = p["render_path"], p["display_path"]
        if render_path and not render_path.startswith(new):
            olds.add(render_path.rsplit("/", 1)[0] + "/")
            png = render_page(pdf, p["page_no"], data_dir / new / f"p{p['page_no']:03d}.png", settings.ingest_max_edge)
            rotate_clockwise(png, p["rotation"] or 0)
            render_path = str(png.relative_to(data_dir))
        if display_path and not display_path.startswith(new):
            olds.add(display_path.rsplit("/", 2)[0] + "/")  # .../<file>-v1/display/p001.jpg
            jpg = render_display(pdf, p["page_no"], data_dir / new / "display" / f"p{p['page_no']:03d}.jpg",
                                 settings.display_max_edge)
            rotate_clockwise(jpg, p["rotation"] or 0)
            display_path = str(jpg.relative_to(data_dir))
        conn.execute("UPDATE pages SET render_path = ?, display_path = ? WHERE id = ?",
                     (render_path, display_path, p["id"]))

    # Point this file's staged items, and whatever was approved from them, at the new images.
    ids = [r["item_id"] for r in conn.execute(
        "SELECT DISTINCT item_id FROM staged_items WHERE file_id = ? AND item_id != ''", (f["id"],))]
    marks = ",".join("?" * len(ids))
    for old in sorted(olds):  # e.g. renders/<slug>/<file>-v1/ ; its display/ folder sits inside it
        conn.execute("UPDATE staged_items SET record_json = replace(record_json, ?, ?) WHERE file_id = ?",
                     (old, new, f["id"]))
        if ids:
            for table, column in APPROVED:
                _swap(conn, table, f"{column} IN ({marks})", ids, old, new)
    conn.commit()
    out(f"{label}: {len(todo)} page image(s) drawn again under data/{new}")
    return len(todo)


def run(folder, *, apply=False, out=print) -> int:
    try:
        cfg = load_source(Path(folder))
    except SourceError as e:
        out(f"source.yaml problems: {e}")
        return 1
    init_db()
    conn = connect()
    try:
        source = conn.execute("SELECT id FROM sources WHERE path = ?", (str(Path(folder)),)).fetchone()
        if source is None:
            out(f"{folder} has not been ingested yet.")
            return 1
        files = conn.execute("SELECT * FROM source_files WHERE source_id = ? ORDER BY filename, version",
                             (source["id"],)).fetchall()
        counts = [rerender_file(conn, cfg, f, get_settings(), apply, out) for f in files]
        moved, stuck = sum(c for c in counts if c > 0), -sum(c for c in counts if c < 0)
        out("")
        if stuck:
            out(f"{stuck} page image(s) could not be moved (see above). They belong to file versions whose PDF "
                "has been replaced; cards and questions from the current version are not affected.")
        if not moved and not stuck:
            out("Every page image of this source is already in its own folder.")
        elif not moved:
            pass
        elif apply:
            out(f"Done: {moved} page image(s) now come from this source's own PDFs.")
        else:
            out("Preview only: nothing was changed. Run again with --apply to do it.")
        return 0
    finally:
        conn.close()


def main(argv=None, out=print) -> int:
    parser = argparse.ArgumentParser(description="Move a source's page images to its own folder (no API calls).")
    parser.add_argument("folder", help="data/sources/<subject>/<level>/<slug>")
    parser.add_argument("--apply", action="store_true", help="do it (default: preview only)")
    args = parser.parse_args(argv)
    return run(args.folder, apply=args.apply, out=out)


if __name__ == "__main__":
    sys.exit(main())
