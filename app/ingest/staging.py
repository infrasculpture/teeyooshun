"""Database side of ingestion: sources, file versions, pages, staged items,
key entries, matching, and the review actions (approve, reject, edit)."""

import json
from pathlib import Path

from app.db import utc_now
from app.importer import import_questions, validate_question
from app.ingest.assemble import (TWO_ANSWERS, TWO_ANSWERS_WORKED, build_question, numbering_problems,
                                 parent_attached, parent_missing, parent_parts, question_warnings,
                                 split_for_sub_part, stitch_answers, stitch_questions)
from app.ingest.render import file_sha256
from app.sources import topic_for_file


class ReviewError(ValueError):
    """An item can't be approved or saved, with the reasons."""

    def __init__(self, problems):
        self.problems = problems
        super().__init__("; ".join(problems))


# ---- Sources and files -------------------------------------------------------------

def upsert_source(conn, cfg) -> int:
    now = utc_now()
    config = json.dumps({"title": cfg.title, "publisher": cfg.publisher, "subject": cfg.subject,
                         "level": cfg.level, "files": cfg.files, "key_reliability": cfg.key_reliability,
                         "units": cfg.units}, sort_keys=True)
    path = str(cfg.folder)
    row = conn.execute("SELECT id FROM sources WHERE path = ?", (path,)).fetchone()
    if row:
        conn.execute("UPDATE sources SET slug = ?, title = ?, subject = ?, level = ?, config_json = ?, updated_at = ? WHERE id = ?",
                     (cfg.slug, cfg.title, cfg.subject, cfg.level, config, now, row["id"]))
        source_id = row["id"]
    else:
        source_id = conn.execute(
            "INSERT INTO sources (path, slug, title, subject, level, config_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (path, cfg.slug, cfg.title, cfg.subject, cfg.level, config, now, now)).lastrowid
    conn.commit()
    return source_id


def latest_file(conn, source_id: int, filename: str):
    return conn.execute("SELECT * FROM source_files WHERE source_id = ? AND filename = ? ORDER BY version DESC LIMIT 1",
                        (source_id, filename)).fetchone()


def file_action(conn, source_id, pdf: Path, sha256=None) -> str:
    """What a run would do with this file: new / changed / resume / unchanged."""
    sha256 = sha256 or file_sha256(pdf)
    row = latest_file(conn, source_id, pdf.name) if source_id else None
    if row is None:
        return "new"
    if row["sha256"] != sha256:
        return "changed"
    return "unchanged" if row["status"] == "done" else "resume"


def register_file(conn, source_id: int, pdf: Path, material_type: str, unit, force: bool = False):
    """(file row, action). Unchanged files are returned as-is; changed files get a new version.

    force: treat an existing file as changed, so it is read again from scratch as a new version.
    """
    sha256 = file_sha256(pdf)
    action = file_action(conn, source_id, pdf, sha256)
    if force and action != "new":
        action = "forced"
    row = latest_file(conn, source_id, pdf.name)
    if action in ("unchanged", "resume"):
        return row, action
    version = 1
    if action in ("changed", "forced"):
        supersede_file(conn, row["id"])
        version = row["version"] + 1
    file_id = conn.execute(
        """INSERT INTO source_files (source_id, filename, sha256, version, material_type, unit, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'registered', ?)""",
        (source_id, pdf.name, sha256, version, material_type, unit, utc_now())).lastrowid
    conn.commit()
    return conn.execute("SELECT * FROM source_files WHERE id = ?", (file_id,)).fetchone(), action


def supersede_file(conn, file_id: int) -> None:
    """An old version of a changed file: its pending items and key entries no longer count."""
    conn.execute("UPDATE source_files SET status = 'superseded' WHERE id = ?", (file_id,))
    conn.execute("UPDATE staged_items SET status = 'superseded', updated_at = ? WHERE file_id = ? AND status = 'pending'",
                 (utc_now(), file_id))
    conn.execute("UPDATE key_entries SET superseded = 1 WHERE file_id = ?", (file_id,))
    conn.commit()


def finish_file(conn, file_id: int, status: str, page_count: int, warnings: list) -> None:
    conn.execute("UPDATE source_files SET status = ?, page_count = ?, warnings_json = ?, processed_at = ? WHERE id = ?",
                 (status, page_count, json.dumps(warnings), utc_now(), file_id))
    conn.commit()


# ---- Pages -----------------------------------------------------------------------

def finished_pages(conn, file_id: int) -> dict:
    rows = conn.execute("SELECT * FROM pages WHERE file_id = ? AND status IN ('done', 'skipped')", (file_id,)).fetchall()
    return {r["page_no"]: r for r in rows}


def save_page(conn, file_id: int, page_no: int, outcome, render_path) -> None:
    u = outcome.usage
    conn.execute(
        """INSERT INTO pages (file_id, page_no, render_path, rotation, page_type, printed_page, status, model,
                              input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens,
                              cost_usd, error, raw_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (file_id, page_no) DO UPDATE SET
               render_path = excluded.render_path, rotation = excluded.rotation, page_type = excluded.page_type,
               printed_page = excluded.printed_page, status = excluded.status, model = excluded.model,
               input_tokens = pages.input_tokens + excluded.input_tokens,
               output_tokens = pages.output_tokens + excluded.output_tokens,
               cache_creation_input_tokens = pages.cache_creation_input_tokens + excluded.cache_creation_input_tokens,
               cache_read_input_tokens = pages.cache_read_input_tokens + excluded.cache_read_input_tokens,
               cost_usd = COALESCE(pages.cost_usd, 0) + COALESCE(excluded.cost_usd, 0),
               error = excluded.error, raw_json = excluded.raw_json""",
        (file_id, page_no, None if outcome.page_type == "cover" else render_path, outcome.rotation,
         outcome.page_type, outcome.printed_page, outcome.status, outcome.model,
         u["input_tokens"], u["output_tokens"], u["cache_creation_input_tokens"], u["cache_read_input_tokens"],
         outcome.cost_usd, outcome.error,
         None if outcome.data is None else json.dumps(outcome.data, ensure_ascii=False), utc_now()))
    conn.commit()


def page_data(conn, file_id: int) -> list:
    """[(page_no, printed_page, model JSON)] for read content and answer pages, in page order."""
    rows = conn.execute("SELECT page_no, printed_page, raw_json FROM pages WHERE file_id = ? AND status = 'done' "
                        "AND page_type IN ('content', 'answer') AND raw_json IS NOT NULL ORDER BY page_no",
                        (file_id,)).fetchall()
    return [(r["page_no"], r["printed_page"], json.loads(r["raw_json"])) for r in rows]


def assemble_file(conn, file_row) -> None:
    """(Re)build a file's staged questions and key entries from its stored page output. No API calls.

    Pending items of the file are replaced (superseded); approved and rejected items are kept.
    Answers can come from any file type: answer keys, an exam paper's key, or worked-solution
    pages inside a questions file (these take the file's unit).
    """
    pages = page_data(conn, file_row["id"])
    if file_row["material_type"] in ("questions", "exam_paper_with_key"):
        stage_questions(conn, file_row, stitch_questions(pages))
    save_key_entries(conn, file_row, stitch_answers(pages, file_row["unit"] or ""))


def numbering_check(conn, file_id: int) -> list[str]:
    """Gaps, duplicates and answer-key numbers with no question, for one file's chapter or paper."""
    f = conn.execute("SELECT * FROM source_files WHERE id = ?", (file_id,)).fetchone()
    if f["material_type"] not in ("questions", "exam_paper_with_key"):
        return []
    questions = [(r["qnum"], r["part"]) for r in conn.execute(
        "SELECT qnum, part FROM staged_items WHERE file_id = ? AND status != 'superseded' AND kind = 'question'",
        (file_id,))]
    if f["material_type"] == "exam_paper_with_key":
        answers = conn.execute("SELECT qnum, part FROM key_entries WHERE file_id = ? AND superseded = 0",
                               (file_id,)).fetchall()
    else:  # the same chapter's answers, from any file in the source (answer key or worked solutions)
        answers = conn.execute(
            """SELECT k.qnum, k.part FROM key_entries k JOIN source_files kf ON kf.id = k.file_id
               WHERE k.superseded = 0 AND kf.source_id = ? AND kf.status != 'superseded' AND k.unit = ?""",
            (f["source_id"], f["unit"] or "")).fetchall()
    return numbering_problems(questions, [(a["qnum"], a["part"]) for a in answers])


def file_totals(conn, file_id: int) -> dict:
    row = conn.execute(
        """SELECT COUNT(*) AS pages,
                  SUM(status = 'done') AS done, SUM(status = 'skipped') AS skipped, SUM(status = 'failed') AS failed,
                  SUM(page_type = 'cover') AS covers,
                  SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,
                  SUM(cache_read_input_tokens) AS cache_read, SUM(cost_usd) AS cost_usd
           FROM pages WHERE file_id = ?""", (file_id,)).fetchone()
    return {k: (row[k] or 0) for k in row.keys()}


# ---- Staging ---------------------------------------------------------------------

def stage_questions(conn, file_row, items: list) -> int:
    """Stage stitched question items for a file (records get schemes at matching time)."""
    now = utc_now()
    conn.execute("UPDATE staged_items SET status = 'superseded', updated_at = ? WHERE file_id = ? AND status = 'pending'",
                 (now, file_row["id"]))
    for item in items:
        placeholder = {"qnum": item["qnum"], "part": item["part"]}
        conn.execute(
            """INSERT INTO staged_items (file_id, kind, item_id, unit, qnum, part, status, record_json,
                                         warnings_json, page_refs_json, raw_json, created_at, updated_at)
               VALUES (?, 'question', '', ?, ?, ?, 'pending', ?, '[]', ?, ?, ?, ?)""",
            (file_row["id"], file_row["unit"] or "", item["qnum"], item["part"], json.dumps(placeholder),
             json.dumps(item["pages"]), json.dumps(item, ensure_ascii=False), now, now))
    conn.commit()
    return len(items)


def save_key_entries(conn, file_row, entries: list) -> int:
    now = utc_now()
    conn.execute("UPDATE key_entries SET superseded = 1 WHERE file_id = ?", (file_row["id"],))
    for e in entries:
        conn.execute(
            """INSERT INTO key_entries (file_id, unit, qnum, part, scheme_json, page_refs_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (file_row["id"], e["unit"], e["qnum"], e["part"], json.dumps(e, ensure_ascii=False),
             json.dumps(e["pages"]), now))
    conn.commit()
    return len(entries)


def match_source(conn, source_id: int, cfg, known_topics: set) -> dict:
    """Attach key entries to question items across the whole source folder.

    questions files match answer_key entries by unit + question + part;
    exam papers match entries from the same file by question + part.
    Pending, unedited items are rebuilt; reviewed or edited items are left alone.
    """
    files = {r["id"]: r for r in conn.execute(
        "SELECT * FROM source_files WHERE source_id = ? AND status != 'superseded'", (source_id,))}
    if not files:
        return {"matched": 0, "unmatched": 0, "orphans": 0}
    marks = ",".join("?" * len(files))
    entries = conn.execute(f"SELECT * FROM key_entries WHERE superseded = 0 AND file_id IN ({marks}) ORDER BY id",
                           list(files)).fetchall()
    conn.execute(f"UPDATE key_entries SET matched_item_id = NULL WHERE superseded = 0 AND file_id IN ({marks})",
                 list(files))
    index = {}
    for e in entries:
        f = files[e["file_id"]]
        key = ("file", e["file_id"], e["qnum"], e["part"]) if f["material_type"] == "exam_paper_with_key" \
            else ("unit", e["unit"], e["qnum"], e["part"])
        index.setdefault(key, []).append(e)  # entries are in id order: the first found is used

    items = conn.execute(
        f"""SELECT * FROM staged_items WHERE kind = 'question' AND status != 'superseded' AND file_id IN ({marks})""",
        list(files)).fetchall()
    def lookup(it, part):
        f = files[it["file_id"]]
        key = ("file", it["file_id"], it["qnum"], part) if f["material_type"] == "exam_paper_with_key" \
            else ("unit", it["unit"], it["qnum"], part)
        # A worked solution in the question's own file comes before the book's answer key.
        return sorted(index.get(key, []), key=lambda e: (e["file_id"] != it["file_id"], e["id"]))

    matched = unmatched = 0
    for it in items:
        f = files[it["file_id"]]
        extra_warnings = []
        found = lookup(it, it["part"])
        scheme = json.loads(found[0]["scheme_json"]) if found else None
        if not found:
            # Hierarchical parts: an answer for (c) can cover (c)(i), (c)(ii), ...
            for parent, sub in parent_parts(it["part"]):
                found = lookup(it, parent)
                if found:
                    scheme, how = split_for_sub_part(json.loads(found[0]["scheme_json"]), sub)
                    if how == "whole":
                        extra_warnings.append(parent_attached(it["qnum"], parent))
                    elif how == "missing":
                        extra_warnings.append(parent_missing(it["qnum"], parent, sub))
                    break
        for e in found:  # every answer used for this question counts as matched, not orphaned
            conn.execute("UPDATE key_entries SET matched_item_id = ? WHERE id = ?", (it["id"], e["id"]))
        if scheme is not None:
            matched += 1
        else:
            unmatched += 1
        if len(found) > 1:
            extra_warnings.append(TWO_ANSWERS_WORKED if found[0]["file_id"] == it["file_id"] else TWO_ANSWERS)
        if it["status"] != "pending" or it["edited"]:
            continue
        file_topic = None
        if f["material_type"] == "questions":
            file_topic, _ = topic_for_file(cfg, f["filename"])
            file_topic = file_topic or "unassigned"
        record, warnings = build_question(json.loads(it["raw_json"]), cfg=cfg, file_stem=Path(f["filename"]).stem,
                                          file_topic=file_topic, scheme=scheme, known_topics=known_topics)
        warnings += extra_warnings
        conn.execute("UPDATE staged_items SET item_id = ?, record_json = ?, warnings_json = ?, updated_at = ? WHERE id = ?",
                     (record["id"], json.dumps(record, ensure_ascii=False), json.dumps(warnings), utc_now(), it["id"]))
    _flag_duplicate_ids(conn, list(files))
    conn.commit()
    orphans = conn.execute(f"SELECT COUNT(*) FROM key_entries WHERE superseded = 0 AND matched_item_id IS NULL AND file_id IN ({marks})",
                           list(files)).fetchone()[0]
    return {"matched": matched, "unmatched": unmatched, "orphans": orphans}


def _flag_duplicate_ids(conn, file_ids: list) -> None:
    marks = ",".join("?" * len(file_ids))
    dupes = conn.execute(
        f"""SELECT item_id FROM staged_items WHERE status = 'pending' AND file_id IN ({marks})
            GROUP BY item_id HAVING COUNT(*) > 1""", file_ids).fetchall()
    for d in dupes:
        for it in conn.execute(f"SELECT id, warnings_json FROM staged_items WHERE status = 'pending' AND item_id = ? AND file_id IN ({marks})",
                               [d["item_id"]] + file_ids):
            warnings = json.loads(it["warnings_json"])
            if "Duplicate ID: another pending item has the same ID" not in warnings:
                warnings.append("Duplicate ID: another pending item has the same ID")
                conn.execute("UPDATE staged_items SET warnings_json = ? WHERE id = ?", (json.dumps(warnings), it["id"]))


def orphan_answers(conn, file_id: int) -> list:
    return conn.execute("SELECT * FROM key_entries WHERE file_id = ? AND superseded = 0 AND matched_item_id IS NULL ORDER BY id",
                        (file_id,)).fetchall()


# ---- Review actions ------------------------------------------------------------------

def get_item(conn, staged_id: int):
    return conn.execute("SELECT * FROM staged_items WHERE id = ?", (staged_id,)).fetchone()


def approve(conn, staged_id: int, reviewer: str = "parent") -> None:
    """Write the item to the question bank (new version if it exists). Errors block; warnings don't."""
    item = get_item(conn, staged_id)
    if item is None or item["status"] != "pending":
        raise ReviewError(["Only pending items can be approved"])
    record = json.loads(item["record_json"])
    problems = validate_question(record)
    if problems:
        raise ReviewError(problems)
    import_questions(conn, [record])
    now = utc_now()
    conn.execute("UPDATE staged_items SET status = 'approved', reviewed_by = ?, reviewed_at = ?, updated_at = ? WHERE id = ?",
                 (reviewer, now, now, staged_id))
    conn.commit()


def reject(conn, staged_id: int, reviewer: str = "parent") -> None:
    item = get_item(conn, staged_id)
    if item is None or item["status"] != "pending":
        raise ReviewError(["Only pending items can be rejected"])
    now = utc_now()
    conn.execute("UPDATE staged_items SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, updated_at = ? WHERE id = ?",
                 (reviewer, now, now, staged_id))
    conn.commit()


def save_edit(conn, staged_id: int, json_text: str, known_topics: set) -> list[str]:
    """Replace the record with edited JSON. Returns the new warnings (errors included)."""
    item = get_item(conn, staged_id)
    if item is None or item["status"] != "pending":
        raise ReviewError(["Only pending items can be edited"])
    try:
        record = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise ReviewError([f"Not valid JSON: {e}"]) from None
    if not isinstance(record, dict):
        raise ReviewError(["The record must be a JSON object"])
    # After a parent's edit, the model's own doubts and duplicate notes no longer apply.
    raw = {"uncertain": False, "duplicate_number": False}
    warnings = question_warnings(record, raw, {"mode": record.get("marking_mode")}, known_topics)
    warnings = [w for w in warnings if not w.startswith("Check: ")] + \
        [f"Check: {p.split(': ', 1)[-1]}" for p in validate_question(record)]
    conn.execute("UPDATE staged_items SET record_json = ?, item_id = ?, warnings_json = ?, edited = 1, updated_at = ? WHERE id = ?",
                 (json.dumps(record, ensure_ascii=False), str(record.get("id", "")), json.dumps(warnings), utc_now(), staged_id))
    conn.commit()
    return warnings


def approve_clean(conn, file_id: int, reviewer: str = "parent") -> int:
    """Approve every pending item in the file that has no warnings. Returns how many."""
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM staged_items WHERE file_id = ? AND status = 'pending' AND warnings_json = '[]'", (file_id,))]
    count = 0
    for staged_id in ids:
        try:
            approve(conn, staged_id, reviewer)
            count += 1
        except ReviewError:
            pass
    return count
