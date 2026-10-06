"""Ingest a source folder: python -m app.ingest data/sources/<subject>/<level>/<slug>

Options: --file NAME (one PDF only), --dry-run (no API calls, no database changes),
--force (read the selected files again as new versions, even if unchanged; this run only),
--pages 18-20 (with --file: read only those pages again and rebuild the items).
See docs/INGESTION_SPEC.md. Handles questions, answer_key, exam_paper_with_key and revision_tables.
"""

import argparse
import json
import sys
from pathlib import Path

from app.config import get_settings
from app.db import connect, init_db
from app.ingest import staging
from app.ingest.assemble import normalise_qnum, normalise_unit
from app.ingest.display import ensure_display_renders
from app.ingest.extract import process_page
from app.ingest.prompts import page_message
from app.ingest.render import RenderError, file_sha256, page_count, render_page
from app.ingest.summary import current_files, print_source_summary
from app.llm import LLMError
from app.pricing import estimate_usd
from app.sources import HANDLED_TYPES, SourceError, check_unit_topics, load_source, material_type, topic_for_file, unit_of
from app.topics import load_topics, topic_ids, topics_for_subject

# Rough per-page estimate for --dry-run (to confirm on the pilot).
EST_INPUT_TOKENS, EST_OUTPUT_TOKENS = 2500, 1500


def _pdfs(folder: Path, only_file):
    pdfs = sorted(p for p in folder.glob("*.pdf") if p.is_file())
    return [p for p in pdfs if p.name == only_file] if only_file else pdfs


def _fmt_cost(usd) -> str:
    return "unknown" if usd is None else f"US${usd:.3f}"


def dry_run(conn, cfg, pdfs, settings, out, force=False, only_pages=None) -> int:
    row = conn.execute("SELECT id FROM sources WHERE path = ?", (str(cfg.folder),)).fetchone()
    source_id = row["id"] if row else None
    total_pages = 0
    for pdf in pdfs:
        mtype = material_type(cfg, pdf.name)
        if mtype not in HANDLED_TYPES:
            out(f"{pdf.name}: {mtype or 'no material type'}: would be skipped")
            continue
        action = staging.file_action(conn, source_id, pdf, file_sha256(pdf))
        if force and action != "new":
            action = "forced"
        pages = page_count(pdf)
        if only_pages:
            wanted = sorted(p for p in only_pages if p <= pages)
            total_pages += len(wanted)
            out(f"{pdf.name}: {mtype}, {pages} pages, re-reading page(s) {', '.join(str(p) for p in wanted)}")
            continue
        if action != "unchanged":
            total_pages += pages
        out(f"{pdf.name}: {mtype}, {pages} pages, {action}")
    usage = {"input_tokens": total_pages * EST_INPUT_TOKENS, "output_tokens": total_pages * EST_OUTPUT_TOKENS}
    out(f"Would process {total_pages} pages with {settings.ingest_model}: about {usage['input_tokens']} input and "
        f"{usage['output_tokens']} output tokens, roughly {_fmt_cost(estimate_usd(settings.ingest_model, usage))}.")
    return 0


def parse_pages(text: str) -> set:
    """'18-20,22' -> {18, 19, 20, 22}. Raises ValueError if it can't be read."""
    pages = set()
    for piece in text.replace(" ", "").split(","):
        first, _, last = piece.partition("-")
        start, end = int(first), int(last or first)
        if start < 1 or end < start:
            raise ValueError(piece)
        pages.update(range(start, end + 1))
    return pages


def ingest_file(conn, cfg, source_id, pdf, mtype, settings, known_topics, topic_list, client, out, force=False,
                pages=None):
    """Process one PDF. Returns the file row id, or None if nothing was done.

    pages: re-read only these page numbers of a file that has already been ingested. The file
    keeps its version; the other pages keep their stored readings; items are then rebuilt.
    """
    unit = unit_of(pdf.name)
    if pages:
        file_row = staging.latest_file(conn, source_id, pdf.name) if source_id else None
        if file_row is None or file_row["status"] == "superseded":
            out(f"{pdf.name}: not ingested yet, so there are no pages to re-read. Run without --pages first.")
            return None
        if file_sha256(pdf) != file_row["sha256"]:
            out(f"{pdf.name}: the PDF has changed since it was read. Ingest the whole file instead of using --pages.")
            return None
        action = "pages"
        out(f"{pdf.name}: {mtype} (re-reading page(s) {', '.join(str(p) for p in sorted(pages))}, "
            f"version {file_row['version']})")
    else:
        file_row, action = staging.register_file(conn, source_id, pdf, mtype, unit, force=force)
        if action == "unchanged":
            out(f"{pdf.name}: unchanged since the last run, skipped")
            return None
        out(f"{pdf.name}: {mtype} ({action}, version {file_row['version']})")

    # On a resumed or partial run keep earlier warnings (e.g. about name-box pages), except the old
    # failure count and anything about the pages being read again.
    warnings = []
    if action in ("resume", "pages"):
        again = {f"Page {p} " for p in pages or ()}
        warnings = [w for w in json.loads(file_row["warnings_json"])
                    if "page(s) failed" not in w and not any(w.startswith(a) for a in again)]
    if mtype in ("questions", "revision_tables"):
        _topic, warning = topic_for_file(cfg, pdf.name)
        if warning and warning not in warnings:
            warnings.append(warning)

    count = page_count(pdf)
    render_dir = Path(settings.data_dir) / "renders" / cfg.slug / f"{pdf.stem}-v{file_row['version']}"
    finished = staging.finished_pages(conn, file_row["id"])
    if pages:
        beyond = sorted(p for p in pages if p > count)
        if beyond:
            out(f"  (the file has {count} pages: ignoring {', '.join(str(p) for p in beyond)})")
        finished = {n: row for n, row in finished.items() if n not in pages}  # these are read again
    last_unit, last_qnum = normalise_unit(unit) if mtype == "answer_key" else "", ""
    last_section = ""
    failed = 0

    for n in range(1, count + 1):
        if n in finished:
            raw = finished[n]["raw_json"]
            data = json.loads(raw) if raw else None
        else:
            png = render_dir / f"p{n:03d}.png"
            render_page(pdf, n, png, settings.ingest_max_edge)
            outcome = process_page(conn, png=png, material_type=mtype, subject=cfg.subject,
                                   message=page_message(n, count, last_unit, last_qnum, last_section),
                                   settings=settings, topic_list=topic_list, client=client)
            rel = str(png.relative_to(settings.data_dir)) if png.exists() else None
            staging.save_page(conn, file_row["id"], n, outcome, rel)
            if outcome.page_type == "cover":
                staging.drop_display_render(conn, file_row["id"], n, settings.data_dir)  # nothing kept from covers
            data = outcome.data
            label = outcome.page_type or outcome.status
            out(f"  page {n}/{count}: {label}" + (f" (rotated {outcome.rotation})" if outcome.rotation else "")
                + (f" FAILED: {outcome.error}" if outcome.status == "failed" else ""))
            if outcome.status == "failed":
                failed += 1
            if outcome.page_type == "cover" and (n > 1 or outcome.cover_has_other_content):
                msg = f"Page {n} has a name box and was skipped."
                if outcome.cover_has_other_content:
                    msg += " It also had questions or answers: check them by hand."
                warnings.append(msg)
        if data and data.get("page_type") in ("content", "answer"):  # hints for the next page
            if data.get("cards"):
                last_section = (data["cards"][-1].get("section") or "").strip() or last_section
            last_unit = normalise_unit(data.get("chapter_heading")) or last_unit
            if data.get("answers"):
                last_unit = normalise_unit(data["answers"][-1].get("unit")) or last_unit
                last_qnum = normalise_qnum(data["answers"][-1].get("qnum")) or last_qnum
            if data.get("questions"):
                last_qnum = normalise_qnum(data["questions"][-1].get("qnum")) or last_qnum

    # A sharper copy of each question page for the question and review screens (no API calls).
    ensure_display_renders(conn, cfg.folder, cfg.slug, file_row, settings, out)
    staging.assemble_file(conn, file_row, cfg, known_topics)
    if failed:
        warnings.append(f"{failed} page(s) failed; run the command again to retry them.")
    staging.finish_file(conn, file_row["id"], "failed" if failed else "done", count, warnings)
    return file_row["id"]


def run(folder, *, only_file=None, dry=False, force=False, pages=None, client=None, out=print) -> int:
    settings = get_settings()
    folder = Path(folder)
    try:
        cfg = load_source(folder)
    except SourceError as e:
        out(f"source.yaml problems: {e}")
        return 1

    init_db()
    conn = connect()
    try:
        load_topics(conn)
        known = topic_ids(conn)
        for w in cfg.warnings + check_unit_topics(cfg, known):
            out(f"Warning: {w}")
        pdfs = _pdfs(folder, only_file)
        if not pdfs:
            out("No PDF files to process.")
            return 1
        if pages and (not only_file or force):
            out("--pages needs --file (one PDF) and can't be combined with --force.")
            return 1
        if dry:
            return dry_run(conn, cfg, pdfs, settings, out, force, pages)
        if client is None and not settings.api_key_set:
            out("ANTHROPIC_API_KEY is not set in .env. Nothing was processed.")
            return 2

        source_id = staging.upsert_source(conn, cfg)
        topic_list = [dict(t) for t in topics_for_subject(conn, cfg.subject)]
        processed = []
        for pdf in pdfs:
            mtype = material_type(cfg, pdf.name)
            if mtype is None:
                out(f"{pdf.name}: matches no pattern in source.yaml, skipped")
                continue
            if mtype not in HANDLED_TYPES:
                out(f"{pdf.name}: {mtype} is not handled yet, skipped")
                continue
            try:
                file_id = ingest_file(conn, cfg, source_id, pdf, mtype, settings, known, topic_list, client, out,
                                      force=force, pages=pages)
            except RenderError as e:
                out(f"{pdf.name}: could not render: {e}")
                continue
            except LLMError as e:
                out(f"\nStopped: {e}\nThis would repeat for every page (check the API key and INGEST_MODEL in .env).")
                return 2
            if file_id:
                processed.append((pdf.name, file_id))

        result = staging.match_source(conn, source_id, cfg, known)
        # Summarise the files processed now, plus answer keys (their unmatched answers change as questions arrive).
        names = {name for name, _ in processed} | {
            f["filename"] for f in current_files(conn, source_id) if f["material_type"] == "answer_key"}
        out("\n=== Summary ===")
        if names:
            print_source_summary(conn, source_id, out, names)
        else:
            out("No files changed.")
        out("")
        out(f"Answer matching across the folder: {result['matched']} matched, {result['unmatched']} questions "
            f"without an answer, {result['orphans']} answers without a question.")
        out(f"Show this again: python -m app.ingest.summary {cfg.folder} [--file NAME]")
        out("Review and approve items on the review page: /review")
        return 0
    finally:
        conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ingest a source folder of scanned PDFs.")
    parser.add_argument("folder", help="data/sources/<subject>/<level>/<slug>")
    parser.add_argument("--file", help="process only this PDF (file name)")
    parser.add_argument("--dry-run", action="store_true", help="count pages and estimate cost; no API calls")
    parser.add_argument("--force", action="store_true",
                        help="read the selected files again as new versions, even if unchanged (this run only); "
                             "pending items and their edits from the old version are superseded")
    parser.add_argument("--pages", metavar="LIST",
                        help="with --file: read only these pages again, e.g. 18-20 or 3,7-9. The file keeps its "
                             "version, other pages keep their stored readings, and the items are rebuilt")
    args = parser.parse_args(argv)
    pages = None
    if args.pages:
        try:
            pages = parse_pages(args.pages)
        except ValueError:
            print(f"Can't read --pages '{args.pages}'. Use numbers and ranges, e.g. 18-20 or 3,7-9.", file=sys.stderr)
            return 1
    return run(args.folder, only_file=args.file, dry=args.dry_run, force=args.force, pages=pages)


if __name__ == "__main__":
    sys.exit(main())
