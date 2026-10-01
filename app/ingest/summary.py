"""Ingest run summary, printed at the end of every run and on demand:

    python -m app.ingest.summary data/sources/<subject>/<level>/<slug> [--file NAME]

Per file: pages, items, warning counts by type, unmatched questions, answers
without a question, pages not read (with the model's reason) and file warnings.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from app.db import connect, init_db
from app.ingest import staging
from app.ingest.assemble import warning_type

COVER_REASON = "name box (nothing kept)"


def _answer_label(entry) -> str:
    label = f"Q{entry['qnum']}" + (f"({entry['part']})" if entry["part"] else "")
    return f"c{entry['unit']} {label}" if entry["unit"] else label


def file_summary(conn, file_id: int) -> dict:
    f = conn.execute("SELECT * FROM source_files WHERE id = ?", (file_id,)).fetchone()
    items = conn.execute("SELECT * FROM staged_items WHERE file_id = ? AND status != 'superseded' ORDER BY id",
                         (file_id,)).fetchall()
    matched = {r["matched_item_id"] for r in conn.execute(
        "SELECT matched_item_id FROM key_entries WHERE superseded = 0 AND matched_item_id IS NOT NULL")}

    warning_counts = Counter()
    for it in items:
        if it["status"] == "pending":
            for w in json.loads(it["warnings_json"]):
                warning_counts[warning_type(w)] += 1

    not_read = []
    for p in conn.execute("SELECT * FROM pages WHERE file_id = ? ORDER BY page_no", (file_id,)):
        if p["page_type"] == "cover":
            not_read.append((p["page_no"], "cover", COVER_REASON))
        elif p["status"] == "failed":
            not_read.append((p["page_no"], "failed", p["error"] or "no reason given"))
        elif p["page_type"] in ("blank", "other"):
            data = json.loads(p["raw_json"]) if p["raw_json"] else {}
            not_read.append((p["page_no"], p["page_type"], data.get("page_type_reason") or "no reason given"))

    return {
        "file": f,
        "totals": staging.file_totals(conn, file_id),
        "status_counts": Counter(it["status"] for it in items),
        "warning_counts": warning_counts,
        "unmatched": [it["item_id"] or f"Q{it['qnum']}{it['part']}" for it in items
                      if it["kind"] == "question" and it["id"] not in matched],
        "orphans": [_answer_label(e) for e in staging.orphan_answers(conn, file_id)],
        "not_read": not_read,
        "file_warnings": json.loads(f["warnings_json"]),
    }


def format_summary(s: dict) -> list[str]:
    f, t, c = s["file"], s["totals"], s["status_counts"]
    cost = "unknown" if not t["cost_usd"] and t["input_tokens"] else f"US${t['cost_usd']:.3f}"
    lines = [
        f"{f['filename']} ({f['material_type']}, version {f['version']}, {f['status']})",
        f"  Pages: {t['pages']} ({t['done']} read, {t['skipped']} skipped, {t['failed']} failed). "
        f"Tokens in {t['input_tokens']}, out {t['output_tokens']}, cache read {t['cache_read']}. Cost about {cost}.",
        f"  Items: {c['pending']} pending, {c['approved']} approved, {c['rejected']} rejected.",
    ]
    if s["warning_counts"]:
        lines.append("  Warnings on pending items, by type:")
        lines += [f"    {label}: {n}" for label, n in sorted(s["warning_counts"].items(), key=lambda x: (-x[1], x[0]))]
    else:
        lines.append("  Warnings on pending items: none")
    if s["unmatched"]:
        lines.append(f"  Questions without an answer ({len(s['unmatched'])}): " + ", ".join(s["unmatched"]))
    if s["orphans"]:
        lines.append(f"  Answers without a question ({len(s['orphans'])}): " + ", ".join(s["orphans"]))
    if s["not_read"]:
        lines.append("  Pages not read:")
        lines += [f"    p{n} {kind}: {reason}" for n, kind, reason in s["not_read"]]
    if s["file_warnings"]:
        lines.append("  File warnings:")
        lines += [f"    {w}" for w in s["file_warnings"]]
    return lines


def current_files(conn, source_id: int, filenames=None) -> list:
    rows = conn.execute("SELECT * FROM source_files WHERE source_id = ? AND status != 'superseded' ORDER BY filename",
                        (source_id,)).fetchall()
    return [r for r in rows if filenames is None or r["filename"] in filenames]


def print_source_summary(conn, source_id: int, out=print, filenames=None) -> int:
    files = current_files(conn, source_id, filenames)
    for f in files:
        out("")
        for line in format_summary(file_summary(conn, f["id"])):
            out(line)
    return len(files)


def main(argv=None, out=print) -> int:
    parser = argparse.ArgumentParser(description="Show the ingest summary for a source folder again.")
    parser.add_argument("folder", help="data/sources/<subject>/<level>/<slug>")
    parser.add_argument("--file", help="only this PDF (file name)")
    args = parser.parse_args(argv)

    init_db()
    conn = connect()
    try:
        source = conn.execute("SELECT id FROM sources WHERE path = ?", (str(Path(args.folder)),)).fetchone()
        if source is None:
            out(f"{args.folder} has not been ingested yet.")
            return 1
        shown = print_source_summary(conn, source["id"], out, {args.file} if args.file else None)
        if not shown:
            out(f"No ingested file called {args.file} in {args.folder}.")
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
