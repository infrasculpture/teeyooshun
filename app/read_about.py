"""'Read about this': book references with on-screen pages, found by code (no API calls).

For a question or a revision card, find the 2-3 most relevant pages of her notes and show each as a book reference
(book, chapter, printed page) and as a thumbnail of the page that opens full screen, with a "view the whole chapter"
page viewer. References come only from notes material: approved revision cards and reference passages from files
whose material type is revision_tables, teaching_text or school_notes. Questions, answer keys and exam papers are
never used, and the viewer will only serve pages of those same files (and never a page that also holds answers).

The matching is the same as "Show me the card" (Help me tier 3, app/question_help.py): same topic, then overlap of
the question's words (and mark points, which are read here in code and never shown) with the card or passage,
ties to book order, falling back to the topic's first card. Opening it before she submits is help (hint_uses tier 5);
after she submits, and in Revise, it is not. Every open is logged in reference_uses for the parent.
"""

import json
import math

from app import topics
from app.db import utc_now
from app.generate import plan
from app.page_images import resolve_render
from app.question_help import _card_words, _question_words, _topic_title

NOTES_FILE_TYPES = ("revision_tables", "teaching_text", "school_notes")
VIEW_PAGE_TYPES = ("content", "image_only", "glossary", "learning_outcomes")  # pages the viewer may serve
MAX_REFERENCES = 3
HINT_TIER = 5          # hint_uses tier for "read about this before submitting"
STAGES = ("before", "after", "revise")
NO_PRINTED_PAGE = "page not printed"
SLIDES = "slides_4up"
PER_PAGE = 4           # slides per file page (app/ingest/slides.py)


# ---- Settings ----------------------------------------------------------------------------

def allowed(settings: dict, stage: str) -> bool:
    """Whether the button is offered (and the route works) at this stage: 'before', 'after' or 'revise'."""
    return bool(settings["read_enabled"]) and (stage != "before" or bool(settings["read_before_submit"]))


# ---- Finding the references ------------------------------------------------------------------

def _score(wanted: set, words: set) -> float:
    return len(wanted & words) / math.sqrt(len(wanted) * len(words)) if wanted and words else 0.0


def _first_page(page_refs_json) -> int | None:
    pages = [p for p in json.loads(page_refs_json or "[]") if isinstance(p, int) and not isinstance(p, bool)]
    return min(pages) if pages else None


def _candidates(conn, topic: str, level) -> list[dict]:
    """The topic's cards and passages from notes-type files, at the wanted levels. One per card, and one per page
    for passages (all the passages of a page together). Each: kind, id, words, file_id, filename, layout, unit,
    book, page_no, order (book order)."""
    levels = topics.level_set(conn, level)
    level_marks, type_marks = ",".join("?" * len(levels)), ",".join("?" * len(NOTES_FILE_TYPES))
    found, seen = [], set()
    rows = conn.execute(
        f"""SELECT c.id, c.record_json, c.version, c.sort_key, i.page_refs_json, f.id AS file_id, f.filename,
                   f.layout, f.unit, s.title AS book
            FROM revision_cards c
            JOIN staged_items i ON i.item_id = c.id AND i.kind = 'card' AND i.status = 'approved'
            JOIN source_files f ON f.id = i.file_id JOIN sources s ON s.id = f.source_id
            WHERE c.topic = ? AND lower(c.level) IN ({level_marks}) AND f.material_type IN ({type_marks})
            ORDER BY c.sort_key, c.id, i.id DESC""", (topic, *levels, *NOTES_FILE_TYPES)).fetchall()
    for r in rows:
        page_no = _first_page(r["page_refs_json"])
        if r["id"] in seen or page_no is None:
            continue
        seen.add(r["id"])
        card = {**json.loads(r["record_json"]), "version": r["version"]}
        found.append({"kind": "card", "id": r["id"], "words": _card_words(card), "file_id": r["file_id"],
                      "filename": r["filename"], "layout": r["layout"], "unit": r["unit"] or "", "book": r["book"],
                      "page_no": page_no, "order": (r["sort_key"], r["id"])})
    pages = {}
    for r in conn.execute(
            f"""SELECT p.text, p.page_no, f.id AS file_id, f.filename, f.layout, f.unit, s.title AS book
                FROM reference_passages p
                JOIN source_files f ON f.id = p.file_id JOIN sources s ON s.id = f.source_id
                WHERE p.topic = ? AND lower(p.level) IN ({level_marks}) AND f.material_type IN ({type_marks})
                ORDER BY f.filename, p.page_no, p.id""", (topic, *levels, *NOTES_FILE_TYPES)):
        entry = pages.setdefault((r["file_id"], r["page_no"]), {
            "kind": "passage", "id": f"{r['file_id']}:{r['page_no']}", "words": set(), "file_id": r["file_id"],
            "filename": r["filename"], "layout": r["layout"], "unit": r["unit"] or "", "book": r["book"],
            "page_no": r["page_no"], "order": (r["filename"], r["page_no"])})
        entry["words"] |= set(plan._content(r["text"]))
    return found + list(pages.values())


def _pick(candidates: list, wanted: set, own_id=None, limit=MAX_REFERENCES) -> list:
    """The best candidates: her own card first (in Revise), then by overlap with `wanted` (only those that overlap),
    ties to book order. If nothing overlaps, the topic's first card (else its first passage page).
    At most one per page."""
    scored = [(_score(wanted, c["words"]), c) for c in candidates]
    chosen = [c for c in candidates if own_id is not None and c["kind"] == "card" and c["id"] == own_id]
    chosen += [c for s, c in sorted(((s, c) for s, c in scored if s > 0 and c not in chosen),
                                    key=lambda t: (-t[0], t[1]["filename"], t[1]["page_no"], str(t[1]["order"])))]
    if not chosen:
        cards = [c for c in candidates if c["kind"] == "card"]
        pool = cards or candidates
        if pool:
            chosen = [min(pool, key=lambda c: (c["order"], c["page_no"]) if c["kind"] == "card"
                          else (c["filename"], c["page_no"]))]
    out, pages = [], set()
    for c in chosen:
        key = (c["file_id"], c["page_no"])
        if key not in pages:
            pages.add(key)
            out.append(c)
    return out[:limit]


def _slide_page(slide: int) -> int:
    return (slide - 1) // PER_PAGE + 1


def _reference(conn, c: dict, topic_title: str) -> dict:
    page = conn.execute("SELECT printed_page FROM pages WHERE file_id = ? AND page_no = ?",
                        (c["file_id"], c["page_no"])).fetchone()
    printed = (page["printed_page"] if page else "") or ""
    slide = c["layout"] == SLIDES
    if slide:   # page_no is the slide number; the file page holds four slides
        label = f"s{c['page_no']:02d}"
        printed_text, file_page = f"slide {label}", _slide_page(c["page_no"])
        detail = f"{c['filename']}, file page {file_page}, slide {label}"
    else:
        printed_text = f"p. {printed}" if printed.strip() else NO_PRINTED_PAGE
        file_page = c["page_no"]
        detail = f"{c['filename']}, file page {file_page}"
    unit = c["unit"].strip()
    chapter = (f"Unit {int(unit)}" if unit.isdigit() else f"Unit {unit}" if unit else "")
    chapter = f"{chapter} · {topic_title}" if chapter else topic_title
    image = page_image(conn, c["file_id"], c["page_no"])
    return {
        "file_id": c["file_id"], "page_no": c["page_no"], "file_page": file_page, "filename": c["filename"],
        "title": c["book"], "chapter": chapter, "printed": printed, "printed_text": printed_text, "detail": detail,
        "image_url": f"/read/{c['file_id']}/page/{c['page_no']}" if image else "",
        "viewer_url": f"/read/{c['file_id']}?p={c['page_no']}" if viewable_pages(conn, c["file_id"]) else "",
    }


def references_for_question(conn, question: dict, level=None, limit=MAX_REFERENCES) -> list[dict]:
    """The references for a question: same topic, best overlap with its words and mark points. [] if none."""
    candidates = _candidates(conn, question["topic"], level)
    picked = _pick(candidates, _question_words(question), limit=limit)
    title = _topic_title(conn, question["topic"])
    return [_reference(conn, c, title) for c in picked]


def references_for_card(conn, card: dict, level=None, limit=MAX_REFERENCES) -> list[dict]:
    """The references for a Revise card: the card's own page first (if it came from a notes file), then others."""
    candidates = _candidates(conn, card["topic"], level)
    picked = _pick(candidates, _card_words(card), own_id=card["id"], limit=limit)
    title = _topic_title(conn, card["topic"])
    return [_reference(conn, c, title) for c in picked]


def for_ask(refs: list) -> list[dict]:
    """What 'Ask about this' may be told: title, chapter and printed page only (never the page text)."""
    return [{"book": r["title"], "chapter": r["chapter"], "printed_page": r["printed_text"]} for r in refs]


# ---- Pages that may be served ----------------------------------------------------------------------

def notes_file(conn, file_id: int):
    """The source file row (with the book title) if it is a notes-type file, else None."""
    marks = ",".join("?" * len(NOTES_FILE_TYPES))
    return conn.execute(
        f"""SELECT f.*, s.title AS book FROM source_files f JOIN sources s ON s.id = f.source_id
            WHERE f.id = ? AND f.material_type IN ({marks})""", (file_id, *NOTES_FILE_TYPES)).fetchone()


def _page_path(row):
    """The image file for a pages row, or None unless it is a notes page that may be shown: a read page of an
    allowed class with no answers on it, whose image exists inside data/renders."""
    if row is None or row["status"] != "done" or row["page_type"] not in VIEW_PAGE_TYPES:
        return None
    if row["raw_json"] and json.loads(row["raw_json"]).get("answers"):
        return None
    return resolve_render(row["display_path"]) or resolve_render(row["render_path"])


_PAGE_COLUMNS = "page_no, status, page_type, raw_json, display_path, render_path"


def page_image(conn, file_id: int, page_no: int):
    """The image for one page of a notes file, or None if it may not (or cannot) be shown."""
    if notes_file(conn, file_id) is None:
        return None
    row = conn.execute(f"SELECT {_PAGE_COLUMNS} FROM pages WHERE file_id = ? AND page_no = ?",
                       (file_id, page_no)).fetchone()
    return _page_path(row)


def viewable_pages(conn, file_id: int) -> list[int]:
    """The page numbers the chapter viewer steps through, in order. [] for a file that isn't a notes file."""
    if notes_file(conn, file_id) is None:
        return []
    rows = conn.execute(f"SELECT {_PAGE_COLUMNS} FROM pages WHERE file_id = ? ORDER BY page_no", (file_id,))
    return [r["page_no"] for r in rows if _page_path(r) is not None]


# ---- The log ---------------------------------------------------------------------------------------------

def record_use(conn, kind: str, scope_id: str, subject: str, stage: str, *, attempt_id=None, set_id=None,
               session_id=None) -> None:
    """Note one open of 'Read about this'. A read before submitting also has a hint_uses row (the caller adds it)."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage}")
    conn.execute(
        "INSERT INTO reference_uses (kind, scope_id, subject, stage, attempt_id, set_id, session_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (kind, scope_id, subject, stage, attempt_id, set_id, session_id, utc_now()))
    conn.commit()
