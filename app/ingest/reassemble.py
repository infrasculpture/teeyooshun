"""Rebuild staged items from the stored page output, using the current rules. No API calls.

    python -m app.ingest.reassemble data/sources/<subject>/<level>/<slug> [--file NAME] [--apply]

Without --apply it works on an in-memory copy of the database, prints what would
change (items added, removed and changed, warnings by type) and the new summary,
then throws the copy away. With --apply the changes are kept: pending items of the
files are replaced (including any saved edits); approved and rejected items stay.
--apply also makes any missing display images of question pages from the PDFs
(the preview lists the model-size copies instead, so its image paths differ).

Only code-side rules are re-applied. Prompt changes need the pages read again
(python -m app.ingest ... --force).
"""

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from app.config import get_settings
from app.db import connect, init_db
from app.ingest import staging
from app.ingest.assemble import warning_type
from app.ingest.display import ensure_display_renders
from app.ingest.summary import current_files, file_summary, format_summary
from app.sources import HANDLED_TYPES, SourceError, load_source
from app.topics import load_topics, topic_ids


def _memory_copy(conn) -> sqlite3.Connection:
    copy = sqlite3.connect(":memory:", check_same_thread=False)
    conn.backup(copy)
    copy.row_factory = sqlite3.Row
    copy.execute("PRAGMA foreign_keys = ON")
    return copy


def snapshot(conn, file_id: int) -> dict:
    items, seen = {}, Counter()
    edited = 0
    for it in conn.execute("SELECT * FROM staged_items WHERE file_id = ? AND status = 'pending' ORDER BY id", (file_id,)):
        key = it["item_id"] or f"Q{it['qnum']}{it['part']}"
        seen[key] += 1
        if seen[key] > 1:
            key = f"{key} (#{seen[key]})"
        items[key] = (json.loads(it["record_json"]), json.loads(it["warnings_json"]))
        edited += it["edited"]
    warnings = Counter(warning_type(w) for _record, ws in items.values() for w in ws)
    return {"items": items, "edited": edited, "warnings": warnings}


def diff_lines(name: str, before: dict, after: dict) -> list[str]:
    b, a = before["items"], after["items"]
    lines = [f"{name}: pending items {len(b)} -> {len(a)}"]
    removed, added = [k for k in b if k not in a], [k for k in a if k not in b]
    if removed:
        lines.append(f"  Removed ({len(removed)}): " + ", ".join(removed))
    if added:
        lines.append(f"  Added ({len(added)}): " + ", ".join(added))
    changed = []
    for key in b:
        if key in a:
            (rec_b, warn_b), (rec_a, warn_a) = b[key], a[key]
            fields = sorted(k for k in set(rec_b) | set(rec_a) if rec_b.get(k) != rec_a.get(k))
            if warn_b != warn_a:
                fields.append("warnings")
            if fields:
                changed.append(f"    {key}: {', '.join(fields)}")
    if changed:
        lines.append(f"  Changed ({len(changed)}):")
        lines += changed
    if not (removed or added or changed):
        lines.append("  No changes to pending items.")
    types = sorted(set(before["warnings"]) | set(after["warnings"]))
    moves = [f"{t} {before['warnings'][t]} -> {after['warnings'][t]}" for t in types
             if before["warnings"][t] != after["warnings"][t]]
    if moves:
        lines.append("  Warnings by type: " + "; ".join(moves))
    if before["edited"]:
        lines.append(f"  {before['edited']} pending item(s) had saved edits; they are replaced.")
    return lines


def main(argv=None, out=print) -> int:
    parser = argparse.ArgumentParser(description="Rebuild staged items from stored page output (no API calls).")
    parser.add_argument("folder", help="data/sources/<subject>/<level>/<slug>")
    parser.add_argument("--file", help="only this PDF (file name); matching still covers the whole folder")
    parser.add_argument("--apply", action="store_true", help="keep the changes (default: preview only)")
    args = parser.parse_args(argv)

    try:
        cfg = load_source(Path(args.folder))
    except SourceError as e:
        out(f"source.yaml problems: {e}")
        return 1

    init_db()
    conn = connect()
    try:
        source = conn.execute("SELECT id FROM sources WHERE path = ?", (str(Path(args.folder)),)).fetchone()
        if source is None:
            out(f"{args.folder} has not been ingested yet.")
            return 1
        work = conn if args.apply else _memory_copy(conn)
        load_topics(work)
        files = [f for f in current_files(work, source["id"], {args.file} if args.file else None)
                 if f["material_type"] in HANDLED_TYPES]
        if not files:
            out("No ingested files to rebuild.")
            return 1

        known = topic_ids(work)
        before = {f["id"]: snapshot(work, f["id"]) for f in files}
        for f in files:
            if args.apply:
                # Make any missing display images of question pages from the PDF (no API calls).
                made = ensure_display_renders(work, cfg.folder, cfg.slug, f, get_settings(), out)
                if made:
                    out(f"{f['filename']}: made {made} display image(s) of question pages")
            staging.assemble_file(work, f, cfg, known)
        staging.match_source(work, source["id"], cfg, known)

        out("=== What changes ===" if args.apply else "=== What would change (preview, nothing saved) ===")
        for f in files:
            out("")
            for line in diff_lines(f["filename"], before[f["id"]], snapshot(work, f["id"])):
                out(line)
        out("\n=== New summary ===")
        for f in current_files(work, source["id"], {f["filename"] for f in files}):
            out("")
            for line in format_summary(file_summary(work, f["id"])):
                out(line)
        out("")
        out("Changes saved." if args.apply else
            "Preview only: nothing was changed. Run again with --apply to keep these changes.")
        if work is not conn:
            work.close()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
