"""Database side of ingestion: sources, file versions, pages, staged items,
key entries, matching, and the review actions (approve, reject, edit)."""

import json
from pathlib import Path

from app.db import utc_now
from app.importer import import_questions, validate_question
from app.ingest import cards, notes
from app.revision_cards import import_cards
from app.ingest.assemble import (TWO_ANSWERS, TWO_ANSWERS_WORKED, align_sections, build_question,
                                 matched_by_position, normalise_qnum, numbering_problems, parent_attached,
                                 parent_missing, parent_parts, question_warnings, show_qnum, split_for_sub_part,
                                 stitch_answers, stitch_questions, worked_example_label)
from app.ingest.render import file_sha256
from app.ingest.slides import slide_label, slide_page
from app.sources import NOTES_TYPES, topic_for_file


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
                         "units": cfg.units,
                         "numbering_overrides": getattr(cfg, "numbering_overrides", {}),
                         "file_options": getattr(cfg, "file_options", {})}, sort_keys=True)
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


def file_action(conn, source_id, pdf: Path, sha256=None, layout: str = "a4", slide_order: str = "") -> str:
    """What a run would do with this file: new / changed / resume / unchanged.

    A file whose layout or slide order in source.yaml no longer matches how it was read counts as changed,
    so changing slide_order and running again reads it again."""
    sha256 = sha256 or file_sha256(pdf)
    row = latest_file(conn, source_id, pdf.name) if source_id else None
    if row is None:
        return "new"
    if row["sha256"] != sha256 or (row["layout"], row["slide_order"]) != (layout, slide_order):
        return "changed"
    return "unchanged" if row["status"] == "done" else "resume"


def register_file(conn, source_id: int, pdf: Path, material_type: str, unit, force: bool = False,
                  layout: str = "a4", slide_order: str = ""):
    """(file row, action). Unchanged files are returned as-is; changed files get a new version.

    force: treat an existing file as changed, so it is read again from scratch as a new version.
    layout, slide_order: how the pages are laid out (slides_4up for a slide handout), from source.yaml.
    """
    sha256 = file_sha256(pdf)
    action = file_action(conn, source_id, pdf, sha256, layout, slide_order)
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
        """INSERT INTO source_files (source_id, filename, sha256, version, material_type, unit, status, created_at,
                                    layout, slide_order)
           VALUES (?, ?, ?, ?, ?, ?, 'registered', ?, ?, ?)""",
        (source_id, pdf.name, sha256, version, material_type, unit, utc_now(), layout, slide_order)).lastrowid
    conn.commit()
    return conn.execute("SELECT * FROM source_files WHERE id = ?", (file_id,)).fetchone(), action


def supersede_file(conn, file_id: int) -> None:
    """An old version of a changed file: its pending items and key entries no longer count."""
    conn.execute("UPDATE source_files SET status = 'superseded' WHERE id = ?", (file_id,))
    conn.execute("UPDATE staged_items SET status = 'superseded', updated_at = ? WHERE file_id = ? AND status = 'pending'",
                 (utc_now(), file_id))
    conn.execute("UPDATE key_entries SET superseded = 1 WHERE file_id = ?", (file_id,))
    conn.execute("DELETE FROM reference_passages WHERE file_id = ?", (file_id,))
    conn.execute("DELETE FROM topic_outcomes WHERE file_id = ?", (file_id,))
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


def page_data(conn, file_id: int, types=("content", "answer")) -> list:
    """[(page_no, printed_page, model JSON)] for read pages of these classes, in page order.
    For a slide handout page_no is the slide number."""
    marks = ",".join("?" * len(types))
    rows = conn.execute("SELECT page_no, printed_page, raw_json FROM pages WHERE file_id = ? AND status = 'done' "
                        f"AND page_type IN ({marks}) AND raw_json IS NOT NULL ORDER BY page_no",
                        (file_id, *types)).fetchall()
    return [(r["page_no"], r["printed_page"], json.loads(r["raw_json"])) for r in rows]


def section_marks(conn, file_id: int) -> dict:
    """{section letter ('' if none): marks per question}, as stated on the file's pages
    (usually the instructions page). Empty for readings stored before this was asked for."""
    marks = {}
    for r in conn.execute("SELECT raw_json FROM pages WHERE file_id = ? AND raw_json IS NOT NULL ORDER BY page_no",
                          (file_id,)):
        for entry in json.loads(r["raw_json"]).get("section_marks") or []:
            if isinstance(entry.get("marks_each"), int) and entry["marks_each"] > 0:
                section = "".join(c for c in str(entry.get("section") or "").lower() if c.isalpha())
                marks.setdefault(section[-1:] if section else "", entry["marks_each"])
    return marks


def showable_pages(conn, file_id: int) -> dict:
    """{page_no: {"page", "display", "model"}} for pages that may be shown to the student.

    Only pages classed as question pages, and never one that also has solutions on it.
    Answer pages are never included. "display" is the sharper copy when it exists.
    """
    shown = {}
    for r in conn.execute("SELECT page_no, render_path, display_path, raw_json FROM pages "
                          "WHERE file_id = ? AND status = 'done' AND page_type IN ('content', 'image_only')",
                          (file_id,)):
        data = json.loads(r["raw_json"]) if r["raw_json"] else {}
        if data.get("answers") or not (r["display_path"] or r["render_path"]):
            continue
        shown[r["page_no"]] = {"page": r["page_no"], "display": r["display_path"] or r["render_path"],
                               "model": r["render_path"]}
    return shown


def assemble_file(conn, file_row, cfg=None, known_topics=None) -> None:
    """(Re)build a file's staged items and key entries from its stored page output. No API calls.

    Pending items of the file are replaced (superseded); approved and rejected items are kept.
    Revision tables become staged cards (cfg is needed for their IDs and topics).
    Answers can come from any file type: answer keys, an exam paper's key, or worked-solution
    pages inside a questions file (these take the file's unit).
    """
    if file_row["material_type"] in NOTES_TYPES:
        assemble_notes(conn, cfg, file_row, known_topics or set())
        return
    pages = page_data(conn, file_row["id"])
    if file_row["material_type"] == "revision_tables":
        stage_cards(conn, cfg, file_row, cards.stitch_cards(pages), known_topics or set())
        return
    unnumbered_label = None
    if file_row["material_type"] in ("questions", "exam_paper_with_key"):
        questions = stitch_questions(pages)
        stage_questions(conn, file_row, questions)
        if file_row["material_type"] == "questions":
            # Unnumbered answers in a questions file are the worked example's solutions.
            unnumbered_label = worked_example_label(questions)
    save_key_entries(conn, file_row, stitch_answers(pages, file_row["unit"] or "", unnumbered_label))


def assemble_notes(conn, cfg, file_row, known_topics: set) -> None:
    """School notes and slide handouts: derived cards go through the same stitching and staging as
    revision tables; outcomes and reference passages are stored beside them. No API calls."""
    slide_file = file_row["layout"] == "slides_4up"
    pages = page_data(conn, file_row["id"], notes.READ_CLASSES)
    items = cards.stitch_cards(notes.notes_pages(pages, file_row["material_type"], slide_file),
                               max_pages=notes.SLIDE_CARD_LIMIT if slide_file else None)
    stage_cards(conn, cfg, file_row, items, known_topics)
    save_notes_extras(conn, cfg, file_row, *notes.page_extras(pages), known_topics, slide_file)


def _extra_topic(entry: dict, file_topic, known_topics: set) -> str:
    """The topic of a passage or outcome: the file's, else the model's if it is a known topic."""
    if file_topic and file_topic != "unassigned":
        return file_topic
    suggested = (entry.get("topic") or "").strip()
    return suggested if suggested and (not known_topics or suggested in known_topics) else "unassigned"


def save_notes_extras(conn, cfg, file_row, outcomes: list, passages: list, known_topics: set, slide_file: bool) -> None:
    """Replace the file's learning outcomes and reference passages (they are not reviewed items)."""
    now = utc_now()
    file_topic, _warning = topic_for_file(cfg, file_row["filename"])
    renders = {r["page_no"]: r["display_path"] or r["render_path"] or "" for r in conn.execute(
        "SELECT page_no, render_path, display_path FROM pages WHERE file_id = ?", (file_row["id"],))}
    conn.execute("DELETE FROM topic_outcomes WHERE file_id = ?", (file_row["id"],))
    conn.execute("DELETE FROM reference_passages WHERE file_id = ?", (file_row["id"],))

    def ref(entry):
        return cards.source_ref(cfg, entry["printed"], entry["page_no"] if slide_file else None)

    for o in outcomes:
        conn.execute("INSERT INTO topic_outcomes (file_id, subject, level, topic, text, source_ref, page_no, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (file_row["id"], cfg.subject, cfg.level, _extra_topic(o, file_topic, known_topics),
                      o["text"].strip(), ref(o), o["page_no"], now))
    for p in passages:
        conn.execute("INSERT INTO reference_passages (file_id, subject, level, topic, source_ref, text, page_no, "
                     "page_render, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (file_row["id"], cfg.subject, cfg.level, _extra_topic(p, file_topic, known_topics), ref(p),
                      p["text"].strip(), p["page_no"], renders.get(p["page_no"], ""), now))
    conn.commit()


def numbering_check(conn, file_id: int) -> list[str]:
    """Gaps, duplicates and answer-key numbers with no question, for one file's chapter or paper."""
    f = conn.execute("SELECT * FROM source_files WHERE id = ?", (file_id,)).fetchone()
    if f["material_type"] not in ("questions", "exam_paper_with_key"):
        return []
    rows = conn.execute(
        "SELECT qnum, part, record_json, warnings_json FROM staged_items WHERE file_id = ? "
        "AND status != 'superseded' AND kind = 'question' ORDER BY id", (file_id,)).fetchall()
    questions = [(r["qnum"], r["part"]) for r in rows]
    mapping = number_mapping(rows)
    if f["material_type"] == "exam_paper_with_key":
        answers = conn.execute("SELECT qnum, part FROM key_entries WHERE file_id = ? AND superseded = 0",
                               (file_id,)).fetchall()
    else:  # the same chapter's answers, from any file in the source (answer key or worked solutions)
        answers = conn.execute(
            """SELECT k.qnum, k.part FROM key_entries k JOIN source_files kf ON kf.id = k.file_id
               WHERE k.superseded = 0 AND kf.source_id = ? AND kf.status != 'superseded' AND k.unit = ?""",
            (f["source_id"], f["unit"] or "")).fetchall()
    return numbering_problems(questions, [(a["qnum"], a["part"]) for a in answers],
                              {q: k for q, (k, _how) in mapping.items()})


def number_mapping(item_rows) -> dict:
    """{question number: (answer-key number, "by position" or "override")} recorded on a file's items."""
    mapping = {}
    for r in item_rows:
        record = json.loads(r["record_json"])
        if record.get("key_number"):
            by_position = any(w.startswith("Matched by position") for w in json.loads(r["warnings_json"]))
            mapping.setdefault(r["qnum"], (normalise_qnum(record["key_number"]),
                                           "by position" if by_position else "override"))
    return mapping


def drop_display_render(conn, file_id: int, page_no: int, data_dir) -> None:
    """Remove a page's display image (a page re-read as a cover keeps nothing)."""
    row = conn.execute("SELECT display_path FROM pages WHERE file_id = ? AND page_no = ?", (file_id, page_no)).fetchone()
    if row and row["display_path"]:
        (Path(data_dir) / row["display_path"]).unlink(missing_ok=True)
        conn.execute("UPDATE pages SET display_path = NULL WHERE file_id = ? AND page_no = ?", (file_id, page_no))
        conn.commit()


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


def existing_card_ids(conn, file_row) -> dict:
    """{normalised prompt: card id} for cards already staged from this file, in any version and
    any status, so a re-read or rebuild gives the same row the same ID. Both the prompt as first
    read and the prompt as later edited are recognised."""
    known = {}
    rows = conn.execute(
        """SELECT i.item_id, i.raw_json, i.record_json FROM staged_items i
           JOIN source_files f ON f.id = i.file_id
           WHERE i.kind = 'card' AND f.source_id = ? AND f.filename = ? AND i.item_id != '' ORDER BY i.id""",
        (file_row["source_id"], file_row["filename"])).fetchall()
    for r in rows:
        raw = json.loads(r["raw_json"]) if r["raw_json"] else {}
        if raw.get("split_from"):
            # A sub-card: its ID is the parent row's ID plus a letter, and it carries the parent's prompt key.
            letter = raw.get("split_letter", "")
            parent_id = r["item_id"][:-len(letter)] if letter and r["item_id"].endswith(letter) else r["item_id"]
            if raw.get("prompt_key"):
                known.setdefault(raw["prompt_key"], parent_id)
            continue
        for key in (raw.get("prompt_key"), cards.norm_prompt(json.loads(r["record_json"]).get("prompt"))):
            if key:
                known.setdefault(key, r["item_id"])
    return known


def stage_cards(conn, cfg, file_row, items: list, known_topics: set) -> int:
    """Stage stitched revision-table rows as cards. Returns how many were staged.

    IDs are stable: a row whose normalised prompt matches a card already staged from this file
    reuses that card's ID; otherwise it gets <subject>-<level>-<slug>-<file>-p<page>-<hash8>.
    A long answer is split into sub-cards (parent ID plus a, b, ...).
    """
    now = utc_now()
    known = existing_card_ids(conn, file_row)
    conn.execute("UPDATE staged_items SET status = 'superseded', updated_at = ? WHERE file_id = ? AND status = 'pending'",
                 (now, file_row["id"]))
    file_topic, _warning = topic_for_file(cfg, file_row["filename"])
    shown = showable_pages(conn, file_row["id"])
    file_stem = Path(file_row["filename"]).stem
    # Derived cards: every card of the first file read from a source is flagged for a full check.
    first_file = conn.execute("SELECT filename FROM source_files WHERE source_id = ? ORDER BY id LIMIT 1",
                              (file_row["source_id"],)).fetchone()["filename"] == file_row["filename"]
    approved = {}  # topic -> [(card id, record)] already approved, for the likely-duplicate check
    done_ids = {r["item_id"] for r in conn.execute(
        "SELECT i.item_id FROM staged_items i JOIN source_files f ON f.id = i.file_id "
        "WHERE i.kind = 'card' AND i.status = 'approved' AND f.source_id = ? AND f.filename = ?",
        (file_row["source_id"], file_row["filename"]))}
    done_ids |= {r["id"] for r in conn.execute("SELECT id FROM revision_cards")}
    used, staged = set(), 0
    for item in items:
        key = item["prompt_key"]
        extra = []
        topic = file_topic or "unassigned"
        if item.get("derived"):
            item["derived_needs_check"] = first_file
            if file_topic not in (None, "unassigned"):  # source.yaml set the topic: nothing left to doubt
                item["topic_uncertain"] = False
            else:  # the model picks the topic from the subject and level topic list
                suggested = (item.get("topic") or "").strip()
                if suggested and (not known_topics or suggested in known_topics):
                    topic = suggested
                elif suggested:
                    extra.append(f"The model suggested topic '{suggested}', which is not in topics.yaml")
        parent_id = known.get(key)
        if parent_id is None:
            parent_id = cards.make_card_id(cfg, file_stem, item["pages"][0], key)
            close = cards.near_match(key, known)
            if close:
                extra.append(f"Prompt is close to existing card {known[close]} but not the same: "
                             f"a new ID was given, so check whether it is the same card")
        n = 2
        base = parent_id
        while parent_id in used:  # two rows with the same prompt in one file
            parent_id, n = f"{base}-{n}", n + 1
        used.add(parent_id)
        for piece in cards.split_long(item):
            card_id = parent_id + piece.get("split_letter", "")
            if card_id in done_ids:  # already approved: never stage a pending copy of it
                continue
            images = [shown[p] for p in piece["pages"] if p in shown]
            record, warnings = cards.build_card(piece, cfg=cfg, card_id=card_id, topic=topic, images=images,
                                                known_topics=known_topics)
            piece_extra = list(extra)
            if piece.get("derived") and topic != "unassigned":
                if topic not in approved:
                    approved[topic] = [(r["id"], json.loads(r["record_json"])) for r in conn.execute(
                        "SELECT id, record_json FROM revision_cards WHERE topic = ?", (topic,))]
                duplicate = cards.find_duplicate(record, approved[topic])
                if duplicate:
                    record["possible_duplicate"] = duplicate
                    piece_extra.append(cards.possible_duplicate_warning(duplicate))
            conn.execute(
                """INSERT INTO staged_items (file_id, kind, item_id, unit, qnum, part, status, record_json,
                                             warnings_json, page_refs_json, raw_json, created_at, updated_at)
                   VALUES (?, 'card', ?, ?, '', '', 'pending', ?, ?, ?, ?, ?, ?)""",
                (file_row["id"], card_id, file_row["unit"] or "", json.dumps(record, ensure_ascii=False),
                 json.dumps(warnings + piece_extra), json.dumps(piece["pages"]), json.dumps(piece, ensure_ascii=False),
                 now, now))
            staged += 1
    conn.commit()
    return staged


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
        f"""SELECT * FROM staged_items WHERE kind = 'question' AND status != 'superseded' AND file_id IN ({marks})
            ORDER BY id""", list(files)).fetchall()
    # Numbers that differ between a file's questions and its answers:
    # source.yaml overrides first, then sections aligned by position (same length and part structure).
    configured = getattr(cfg, "numbering_overrides", None) or {}
    aligned, overridden = {}, {}
    for file_id, f in files.items():
        mine = [(it["qnum"], it["part"]) for it in items if it["file_id"] == file_id]
        if f["material_type"] == "exam_paper_with_key":
            theirs = [(e["qnum"], e["part"]) for e in entries if e["file_id"] == file_id]
        else:
            theirs = [(e["qnum"], e["part"]) for e in entries if e["unit"] == (f["unit"] or "")
                      and files[e["file_id"]]["material_type"] != "exam_paper_with_key"]
        aligned[file_id] = align_sections(mine, theirs) if mine else {}
        overridden[file_id] = {normalise_qnum(q): normalise_qnum(k)
                               for q, k in (configured.get(f["filename"]) or {}).items()}

    def key_number(it) -> str:
        """The number this question's answer is filed under in the key."""
        return overridden[it["file_id"]].get(it["qnum"]) or aligned[it["file_id"]].get(it["qnum"]) or it["qnum"]

    def lookup(it, part):
        f = files[it["file_id"]]
        key = ("file", it["file_id"], key_number(it), part) if f["material_type"] == "exam_paper_with_key" \
            else ("unit", it["unit"], key_number(it), part)
        # A worked solution in the question's own file comes before the book's answer key.
        return sorted(index.get(key, []), key=lambda e: (e["file_id"] != it["file_id"], e["id"]))

    mcq_marks = {file_id: section_marks(conn, file_id) for file_id in files}

    # Page images for each question: every page holding any part of that question number
    # (so sub-parts also get the page with the stem), question pages only.
    shown = {file_id: showable_pages(conn, file_id) for file_id in files}
    pages_of_question = {}
    for it in items:
        raw = json.loads(it["raw_json"]) if it["raw_json"] else {}
        pages_of_question.setdefault((it["file_id"], it["qnum"]), set()).update(raw.get("pages") or [])

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
        images = [shown[it["file_id"]][p] for p in sorted(pages_of_question[(it["file_id"], it["qnum"])])
                  if p in shown[it["file_id"]]]
        record, warnings = build_question(json.loads(it["raw_json"]), cfg=cfg, file_stem=Path(f["filename"]).stem,
                                          file_topic=file_topic, scheme=scheme, known_topics=known_topics,
                                          images=images, mcq_marks=mcq_marks[it["file_id"]])
        if key_number(it) != it["qnum"]:
            # Recorded on the item so the summary and review page can show the mapping.
            record["key_number"] = show_qnum(key_number(it))
            if it["qnum"] not in overridden[it["file_id"]]:
                extra_warnings.append(matched_by_position(it["qnum"], key_number(it)))
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
    if item["kind"] == "card":
        problems = cards.validate_card(record)
        if problems:
            raise ReviewError(problems)
        record.pop("possible_duplicate", None)  # a review note, not part of the card
        # Into revision_cards; a changed card gets its next version. The sort key keeps book order:
        # file name, first file page, then the row's position in the file.
        refs = json.loads(item["page_refs_json"]) or [0]
        filename = conn.execute("SELECT filename FROM source_files WHERE id = ?", (item["file_id"],)).fetchone()[0]
        import_cards(conn, [record], {record["id"]: f"{filename}:{int(refs[0]):04d}:{item['id']:08d}"})
    else:
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
    if item["kind"] == "card":
        # A card keeps the ID it was first given, whatever is edited (including its prompt).
        record["id"] = item["item_id"]
        warnings = cards.card_warnings(record, {}, known_topics)
        warnings = [w for w in warnings if not w.startswith("Check: ")] + \
            [f"Check: {p}" for p in cards.validate_card(record)]
        conn.execute("UPDATE staged_items SET record_json = ?, warnings_json = ?, edited = 1, updated_at = ? WHERE id = ?",
                     (json.dumps(record, ensure_ascii=False), json.dumps(warnings), utc_now(), staged_id))
        conn.commit()
        return warnings
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
