"""Rebuild staged items from the stored page output, using the current rules. No API calls.

    python -m app.ingest.reassemble data/sources/<subject>/<level>/<slug> [--file NAME] [--apply]

Without --apply it works on an in-memory copy of the database, prints what would
change (items added, removed and changed, warnings by type) and the new summary,
then throws the copy away. With --apply the changes are kept: pending items of the
files are replaced (including any saved edits); approved and rejected items stay.
--apply also makes any missing display images of question pages from the PDFs
(the preview lists the model-size copies instead, so its image paths differ).

    python -m app.ingest.reassemble data/sources/<subject>/<level>/<slug> --report

--report lists the pages whose stored reading lacks something the current rules need (source text,
model answers), with the cost and the exact command to read just those pages. The preview and --apply
print the same list at the end.

Only code-side rules are re-applied. Prompt changes need the pages read again
(python -m app.ingest ... --pages N, or --force for a whole file).
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from app.config import get_settings
from app.db import connect, init_db
from app.ingest import staging
from app.ingest.assemble import normalise_qnum, stitch_questions, warning_type
from app.ingest.display import ensure_display_renders
from app.ingest.summary import current_files, file_summary, format_summary, key_sequence_relabels
from app.pricing import estimate_usd
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


SOURCE_PAGE_REASON = re.compile(r"source|background|extract|picture|map\b|cartoon|photo|stimulus|poster", re.IGNORECASE)
LONG_POINT_WORDS = 40  # a "mark point" this long is a model answer that was split or squeezed into points


def _page_ranges(pages) -> str:
    """[3, 7, 8, 9] -> '3,7-9'."""
    out, pages = [], sorted(pages)
    while pages:
        end = start = pages.pop(0)
        while pages and pages[0] == end + 1:
            end = pages.pop(0)
        out.append(str(start) if start == end else f"{start}-{end}")
    return ",".join(out)


def _answer_pages_missing_questions(conn, f) -> list:
    """Exam paper with its key: answer pages with an answer for a question number the file has no question
    for, and no question read from the page. If such a page prints the question (a "practice and suggested
    answers" page), reading it again with the current prompt gives the question text."""
    if f["material_type"] != "exam_paper_with_key":
        return []
    pages = staging.page_data(conn, f["id"])
    have = {q["qnum"] for q in stitch_questions(pages, embedded_key=True)}
    out = []
    for page_no, _printed, data in pages:
        if data.get("page_type") != "answer" or data.get("questions"):
            continue
        numbers = {normalise_qnum(a.get("qnum")) for a in data.get("answers") or []}
        if any(n and not n.startswith("we") and n not in have for n in numbers):
            out.append(page_no)
    return out


def reread_needs(conn, f) -> dict:
    """Pages of a file whose stored reading can't be fixed by rebuilding, because the information was never
    read: {"sources": [pages], "answers": [pages], "questions": [pages], "per_page_usd": float or None}.

    sources: pages classed "other" whose reason says they hold sources or background text (the reading has no
    source text). answers: pages with a level table or a very long "mark point" read before model answers
    (exemplars) were kept, so a printed model answer may be missing or split into points."""
    sources, answers, costs = [], [], []
    questions = _answer_pages_missing_questions(conn, f)
    for p in conn.execute("SELECT page_no, page_type, cost_usd, raw_json FROM pages WHERE file_id = ? ORDER BY page_no",
                          (f["id"],)):
        if p["cost_usd"]:
            costs.append(p["cost_usd"])
        data = json.loads(p["raw_json"]) if p["raw_json"] else {}
        if p["page_type"] == "other" and SOURCE_PAGE_REASON.search(data.get("page_type_reason") or ""):
            sources.append(p["page_no"])
        elif p["page_type"] == "answer" and any(
                "exemplars" not in a  # read before model answers were kept
                and (a.get("mode") == "lorms"
                     or any(len((mp.get("text") or "").split()) >= LONG_POINT_WORDS for mp in a.get("mark_points") or []))
                for a in data.get("answers") or []):
            answers.append(p["page_no"])
    return {"sources": sources, "answers": answers, "questions": questions, "per_page_usd": sum(costs) / len(costs) if costs else None}


def reread_lines(conn, folder, files, settings) -> list[str]:
    """What has to be read again (with the cost), and the exact commands. Empty files are not listed."""
    lines, total_pages, total_usd, unknown = [], 0, 0.0, False
    fallback = estimate_usd(settings.ingest_model, {"input_tokens": 2500, "output_tokens": 1500})
    for f in files:
        need = reread_needs(conn, f)
        pages = sorted(set(need["sources"]) | set(need["answers"]) | set(need["questions"]))
        if not pages:
            continue
        per_page = need["per_page_usd"] if need["per_page_usd"] is not None else fallback
        total_pages += len(pages)
        if per_page is None:
            unknown = True
        else:
            total_usd += per_page * len(pages)
        lines.append(f"{f['filename']}: read again {len(pages)} page(s), about "
                     + ("an unknown cost" if per_page is None else f"US${per_page * len(pages):.2f}"))
        if need["sources"]:
            lines.append(f"    pages {_page_ranges(need['sources'])}: sources or background text, read as 'other' "
                         "so no source text was kept (certain)")
        if need["answers"]:
            lines.append(f"    pages {_page_ranges(need['answers'])}: level tables or long answers read before model "
                         "answers were kept (a printed model answer may be missing or split: check, then re-read if so)")
        if need["questions"]:
            lines.append(f"    pages {_page_ranges(need['questions'])}: answer pages with an answer but no question text "
                         "(read again only if the page prints the question, as a 'practice and suggested answers' page "
                         "does; a key-only page gains nothing)")
        lines.append(f"    python -m app.ingest {folder} --file {f['filename']} --pages {_page_ranges(pages)}")
    if not lines:
        return ["Nothing needs reading again: every page that matters has its source text and level tables."]
    lines.append(f"Total: {total_pages} page(s), about US${total_usd:.2f}"
                 + (" (price of the model not known)" if unknown else
                    " (this file's average cost per page so far; the sources pages are image-heavy, so allow a little more)"))
    lines.append("These pages still have to be read: run the commands above (with 'docker compose run --rm app' in "
                 "front), then run reassemble with --apply. Approved and rejected items are never changed.")
    return lines


def main(argv=None, out=print) -> int:
    parser = argparse.ArgumentParser(description="Rebuild staged items from stored page output (no API calls).")
    parser.add_argument("folder", help="data/sources/<subject>/<level>/<slug>")
    parser.add_argument("--file", help="only this PDF (file name); matching still covers the whole folder")
    parser.add_argument("--apply", action="store_true", help="keep the changes (default: preview only)")
    parser.add_argument("--report", action="store_true",
                        help="only list the pages that need reading again, with the cost (changes nothing)")
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
        if args.file and cfg.answer_sections:
            # answer_sections is applied when the answer file is rebuilt, so rebuild it too
            have = {f["id"] for f in files}
            files += [f for f in current_files(work, source["id"], None)
                      if f["material_type"] == "answer_key" and f["id"] not in have]
        if not files:
            out("No ingested files to rebuild.")
            return 1

        if args.report:
            out("=== Pages to read again (nothing is changed by this report) ===")
            for line in reread_lines(work, args.folder, files, get_settings()):
                out(line)
            return 0

        known = topic_ids(work)
        before = {f["id"]: snapshot(work, f["id"]) for f in files}
        for f in files:
            if args.apply:
                # Make any missing display images of question pages from the PDF (no API calls).
                made = ensure_display_renders(work, cfg.folder, cfg.render_key, f, get_settings(), out)
                if made:
                    out(f"{f['filename']}: made {made} display image(s) of question pages")
            staging.assemble_file(work, f, cfg, known)
        staging.match_source(work, source["id"], cfg, known)

        out("=== What changes ===" if args.apply else "=== What would change (preview, nothing saved) ===")
        for f in files:
            out("")
            for line in diff_lines(f["filename"], before[f["id"]], snapshot(work, f["id"])):
                out(line)
            relabels = key_sequence_relabels(work, f["id"])
            if relabels:
                out(f"  Key sequence relabels ({len(relabels)}): raw label -> new label (file page)")
                for line in relabels:
                    out(f"    {line}")
        out("\n=== New summary ===")
        for f in current_files(work, source["id"], {f["filename"] for f in files}):
            out("")
            for line in format_summary(file_summary(work, f["id"])):
                out(line)
        out("\n=== Pages to read again ===")
        for line in reread_lines(work, args.folder, files, get_settings()):
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
