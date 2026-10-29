"""Send one file's approved questions back to pending review. No API calls.

    python -m app.ingest.unapprove data/sources/<subject>/<level>/<slug> --file NAME [--dry-run] [--include-attempted]

For when items were approved before their answers were matched (approved items are never staged again, so
reassemble can't fix them). Only the named file's current version is looked at, and only its approved
questions. Other files, older versions, rejected items and revision cards are never touched.

What happens to each approved question depends on whether she has attempted it:
- no attempts: it is deleted from the question bank (with its stored versions and cached explanations and help),
  so she can't be given it, and its staged item goes back to pending.
- attempts: left alone and listed, to keep her history intact. With --include-attempted it is retired instead:
  the bank row and every attempt stay (the dashboard still shows them), but practice and sessions skip it, and
  its staged item goes back to pending. Approving the corrected item later makes a new version and brings it back.

--dry-run lists what would happen and changes nothing. Afterwards, run reassemble --apply for the file to
rebuild the pending items with their answers, then review and approve them again.
"""

import argparse
import json
import sys
from pathlib import Path

from app.db import connect, init_db, utc_now
from app.ingest.summary import current_files

QUESTION_FILES = ("questions", "exam_paper_with_key")
# Tables keyed by question id that are only caches of a question version. Safe to drop with the question.
DERIVED_TABLES = ("hint_uses", "question_help", "mcq_explanations", "question_versions")


def _label(item) -> str:
    return f"Q{item['qnum']}{item['part']}".strip() or "(no number)"


def plan(conn, file_row) -> dict:
    """The file's approved question items sorted by what would happen to them.

    {"delete": [...], "retire": [...]}: each entry is (staged item, bank id, number of attempts, in bank?).
    Items that would be deleted have no attempts; items that would be retired have some."""
    delete, retire = [], []
    items = conn.execute("SELECT * FROM staged_items WHERE file_id = ? AND kind = 'question' AND status = 'approved' "
                         "ORDER BY id", (file_row["id"],)).fetchall()
    for item in items:
        entry = plan_entry(conn, item)
        (retire if entry[2] else delete).append(entry)
    return {"delete": delete, "retire": retire}


def plan_entry(conn, item) -> tuple:
    """(staged item, bank id, number of attempts, in the bank?) for one approved question item."""
    bank_id = json.loads(item["record_json"]).get("id") or item["item_id"]  # what approve() wrote
    attempts = conn.execute("SELECT COUNT(*) FROM attempts WHERE question_id = ?", (bank_id,)).fetchone()[0]
    in_bank = conn.execute("SELECT 1 FROM questions WHERE id = ?", (bank_id,)).fetchone() is not None
    return item, bank_id, attempts, in_bank


def plan_item(conn, item) -> dict:
    """The same as plan() for a single approved question item (any file, including generated questions)."""
    entry = plan_entry(conn, item)
    return {"delete": [] if entry[2] else [entry], "retire": [entry] if entry[2] else []}


def apply_plan(conn, work: dict, include_attempted: bool) -> None:
    """Make the changes in one transaction: all of them, or none."""
    now = utc_now()
    chosen = work["delete"] + (work["retire"] if include_attempted else [])
    try:
        for item, bank_id, attempts, _in_bank in chosen:
            if attempts:
                conn.execute("UPDATE questions SET retired = 1 WHERE id = ?", (bank_id,))
            else:
                for table in DERIVED_TABLES:
                    conn.execute(f"DELETE FROM {table} WHERE question_id = ?", (bank_id,))
                conn.execute("DELETE FROM questions WHERE id = ?", (bank_id,))
            conn.execute("UPDATE staged_items SET status = 'pending', reviewed_by = NULL, reviewed_at = NULL, "
                         "updated_at = ? WHERE id = ?", (now, item["id"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _list(out, heading: str, entries: list, show_attempts: bool = False) -> None:
    out(f"{heading} ({len(entries)}):")
    for item, bank_id, attempts, in_bank in entries:
        note = f", {attempts} attempt(s)" if show_attempts else ""
        note += "" if in_bank else ", not in the bank"
        out(f"  {_label(item)}  {bank_id}{note}")


def main(argv=None, out=print) -> int:
    parser = argparse.ArgumentParser(description="Send a file's approved questions back to pending (no API calls).")
    parser.add_argument("folder", help="data/sources/<subject>/<level>/<slug>")
    parser.add_argument("--file", required=True, help="the questions PDF (file name) whose approved items go back")
    parser.add_argument("--dry-run", action="store_true", help="list what would change and change nothing")
    parser.add_argument("--include-attempted", action="store_true",
                        help="also retire and reset questions she has attempted (history is kept)")
    args = parser.parse_args(argv)

    init_db()
    conn = connect()
    try:
        source = conn.execute("SELECT id FROM sources WHERE path = ?", (str(Path(args.folder)),)).fetchone()
        if source is None:
            out(f"{args.folder} has not been ingested yet.")
            return 1
        files = current_files(conn, source["id"], {args.file})
        if not files:
            out(f"{args.file} has not been ingested in {args.folder}.")
            return 1
        f = files[0]
        if f["material_type"] not in QUESTION_FILES:
            out(f"{args.file} is a {f['material_type']} file, not a questions file: nothing to do.")
            return 1

        work = plan(conn, f)
        total = len(work["delete"]) + len(work["retire"])
        out(f"{args.file} (version {f['version']}): {total} approved question(s).")
        if not total:
            out("Nothing to do.")
            return 0
        if work["delete"]:
            _list(out, "Delete from the question bank and set back to pending (no attempts)", work["delete"])
        if work["retire"] and args.include_attempted:
            _list(out, "Retire (hidden from practice, history kept) and set back to pending", work["retire"], True)
        elif work["retire"]:
            _list(out, "Skipped: she has attempted these (use --include-attempted to retire them too)",
                  work["retire"], True)

        if args.dry_run:
            out("\nDry run: nothing was changed.")
            return 0
        apply_plan(conn, work, args.include_attempted)
        changed = len(work["delete"]) + (len(work["retire"]) if args.include_attempted else 0)
        out(f"\nDone: {changed} item(s) are pending again. Next, rebuild them with their answers:")
        out(f"  python -m app.ingest.reassemble {args.folder} --file {args.file} --apply")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
