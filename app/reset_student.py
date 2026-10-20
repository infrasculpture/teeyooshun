"""Wipe the student's activity, keep all content and setup.

    python -m app.reset_student --dry-run     # row counts per table, changes nothing
    python -m app.reset_student --yes         # do it, in one transaction

Wiped: attempts (rewrites, tutor explanations, attempt events), recall attempts, card progress,
revise sets, help views, sessions, hint uses, discussions, and all rewards state (reward_meta.started_at is reset to now). Everything else is kept.

Before deleting, it lists the "marking looks wrong" reports the parent upheld (the parent
pressed Disagree with the marking), so they can be dealt with first.
"""

import argparse
import json
import sys

from app.db import connect, utc_now

# Deleted, in an order that satisfies the foreign keys (children before parents).
WIPE = [
    "attempt_events",
    "hint_uses",
    "discussions",
    "attempts",
    "recall_attempts",
    "help_views",
    "revise_sets",
    "card_progress",
    "reward_events",
    "reward_unlocks",
    "reward_equipped",
    "commissions",
    "reward_requests",
    "sessions",
]

# Kept: content, setup, and per-question / per-card caches.
KEEP = {
    "questions", "question_versions", "revision_cards", "revision_card_versions",
    "staged_items", "key_entries", "sources", "source_files", "pages", "topics",
    "reference_passages", "topic_outcomes", "topic_status", "app_settings",
    "reward_items", "uploaded_images", "api_calls",
    "question_help", "card_help", "mcq_explanations", "help_flags",
}

# Kept, except that reward_meta.started_at is reset to now (companion_image and other keys stay).
PARTIAL = {"reward_meta"}


def all_tables(conn) -> set[str]:
    return {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}


def unclassified(conn) -> list[str]:
    """Tables in the database that this command knows nothing about."""
    return sorted(all_tables(conn) - set(WIPE) - KEEP - PARTIAL)


def row_counts(conn) -> dict[str, int]:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in WIPE}


def upheld_reports(conn) -> list[dict]:
    """'Looks wrong' reports whose latest parent verdict is Disagree (with the marking)."""
    rows = conn.execute(
        """SELECT e.attempt_id, a.question_id, q.record_json
           FROM attempt_events e
           JOIN attempts a ON a.id = e.attempt_id
           LEFT JOIN questions q ON q.id = a.question_id
           WHERE e.event = 'marking_looks_wrong'
             AND (SELECT x.event FROM attempt_events x
                  WHERE x.attempt_id = e.attempt_id AND x.event IN ('parent_agree', 'parent_disagree')
                  ORDER BY x.id DESC LIMIT 1) = 'parent_disagree'
           GROUP BY e.attempt_id ORDER BY e.attempt_id""").fetchall()
    out = []
    for r in rows:
        text = json.loads(r["record_json"]).get("question", "") if r["record_json"] else ""
        out.append({"attempt_id": r["attempt_id"], "question_id": r["question_id"], "text": text})
    return out


def unreviewed_reports(conn) -> int:
    return conn.execute(
        """SELECT COUNT(DISTINCT e.attempt_id) FROM attempt_events e
           WHERE e.event = 'marking_looks_wrong'
             AND NOT EXISTS (SELECT 1 FROM attempt_events x WHERE x.attempt_id = e.attempt_id
                             AND x.event IN ('parent_agree', 'parent_disagree'))""").fetchone()[0]


def reset(conn) -> dict[str, int]:
    """Delete the student's activity in one transaction. Returns rows deleted per table."""
    deleted = {}
    try:
        conn.execute("BEGIN")
        for table in WIPE:
            deleted[table] = conn.execute(f"DELETE FROM {table}").rowcount
        conn.execute("INSERT OR REPLACE INTO reward_meta (key, value) VALUES ('started_at', ?)", (utc_now(),))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return deleted


def print_counts(title: str, counts: dict[str, int]) -> None:
    print(title)
    for table, n in counts.items():
        print(f"  {table:<18}{n:>8}")
    print(f"  {'total':<18}{sum(counts.values()):>8}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.reset_student", description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="print row counts per table; change nothing")
    parser.add_argument("--yes", action="store_true", help="confirm the wipe (required unless --dry-run)")
    args = parser.parse_args(argv)

    conn = connect()
    try:
        extra = unclassified(conn)
        if extra:
            print("Stopping: tables this command does not know about (decide wipe or keep, then update app/reset_student.py):")
            for t in extra:
                print(f"  {t}")
            return 2

        reports = upheld_reports(conn)
        print(f"'Looks wrong' reports the parent upheld (Disagree with the marking): {len(reports)}")
        for r in reports:
            print(f"  attempt {r['attempt_id']}  question {r['question_id']}: {r['text']}")
        pending = unreviewed_reports(conn)
        if pending:
            print(f"  (also {pending} report(s) with no parent verdict yet; they will be deleted too)")
        print()
        print("Also: reward_meta.started_at is reset to now (other reward_meta keys are kept).")
        print()

        if args.dry_run:
            print_counts("Dry run, nothing changed. Rows that would be deleted:", row_counts(conn))
            return 0
        if not args.yes:
            print_counts("Rows that would be deleted:", row_counts(conn))
            print("\nRefusing to delete without --yes (use --dry-run to preview).")
            return 1

        print_counts("Deleted:", reset(conn))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
