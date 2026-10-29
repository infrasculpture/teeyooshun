"""Review page for staged ingestion items (parent PIN). INGESTION_SPEC.md section 7."""

import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.generate import run as generate_run
from app.generate import store as generate_store
from app.ingest import staging, unapprove
from app.ingest.slides import slide_label, slide_page
from app.llm import LLMError
from app.page_images import media_type, resolve_render
from app.parent import is_parent, require_parent
from app.revision_cards import get_card
from app.sources import SourceError, load_source
from app.topics import topic_ids
from app.web import form_fields, get_conn, templates

router = APIRouter(prefix="/review")


def _login_redirect(request: Request):
    return RedirectResponse(f"/parent/login?next={quote(request.url.path)}", status_code=303)


def _redirect(url: str):
    return RedirectResponse(url, status_code=303)


FILE_COUNTS = """
    SELECT f.*, s.slug, s.title, s.path,
           COUNT(i.id) AS total,
           COALESCE(SUM(i.status = 'pending'), 0) AS pending,
           COALESCE(SUM(i.status = 'approved'), 0) AS approved,
           COALESCE(SUM(i.status = 'rejected'), 0) AS rejected,
           COALESCE(SUM(i.status = 'pending' AND i.warnings_json != '[]'), 0) AS warned
    FROM source_files f
    JOIN sources s ON s.id = f.source_id
    LEFT JOIN staged_items i ON i.file_id = f.id AND i.status != 'superseded'
    WHERE f.status != 'superseded' {where}
    GROUP BY f.id ORDER BY s.path, f.filename"""


@router.get("", response_class=HTMLResponse)
def sources_page(request: Request, conn=Depends(get_conn)):
    if not is_parent(request):
        return _login_redirect(request)
    only_generated = request.query_params.get("filter") == "generated"
    where = f"AND f.material_type = '{staging.GENERATED_TYPE}'" if only_generated else ""
    files = conn.execute(FILE_COUNTS.format(where=where)).fetchall()
    sources = {}
    for f in files:
        sources.setdefault(f["path"], {"title": f["title"], "slug": f["slug"], "files": []})["files"].append(f)
    return templates.TemplateResponse(request, "review_sources.html",
                                      {"sources": sources, "only_generated": only_generated, "is_parent": True})


def _file_page(request, conn, file_id, message="", status_code=200, topic=""):
    row = conn.execute(FILE_COUNTS.format(where="AND f.id = ?"), (file_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="File not found")
    items = conn.execute("SELECT * FROM staged_items WHERE file_id = ? AND status != 'superseded' ORDER BY id",
                         (file_id,)).fetchall()
    generated = row["material_type"] == staging.GENERATED_TYPE
    versions = {c["id"]: c["version"] for c in conn.execute("SELECT id, version FROM revision_cards")} \
        if generated else {}
    summaries = [_item_summary(i, row["layout"] == "slides_4up", versions) for i in items]
    topics = _topic_counts(summaries) if generated else []
    if topic:
        summaries = [s for s in summaries if s["topic"] == topic]
    pages = conn.execute("SELECT page_no, printed_page, page_type, status, error FROM pages WHERE file_id = ? "
                         "AND status IN ('failed', 'skipped') ORDER BY page_no", (file_id,)).fetchall()
    slides = row["layout"] == "slides_4up"
    sendback = None  # approved questions that could be sent back to pending, counted by what would happen
    if row["approved"] and row["material_type"] in unapprove.QUESTION_FILES:
        work = unapprove.plan(conn, row)
        sendback = {"delete": len(work["delete"]), "retire": len(work["retire"])}
    return templates.TemplateResponse(request, "review_file.html", {
        "sendback": sendback,
        "f": row, "items": summaries, "pages": pages, "generated": generated, "topics": topics, "topic": topic,
        "slides": slides, "slide_label": slide_label, "slide_page": slide_page,
        "slide_groups": _slide_groups(conn, file_id) if slides else [],
        "orphans": [dict(o, scheme=json.loads(o["scheme_json"])) for o in staging.orphan_answers(conn, file_id)],
        "totals": staging.file_totals(conn, file_id), "warnings": json.loads(row["warnings_json"]),
        "numbering": staging.numbering_check(conn, file_id),
        "message": message, "is_parent": True,
    }, status_code=status_code)


def _slide_groups(conn, file_id) -> list:
    """The slide crops of each file page, in the order they were read, so a wrong slide_order is obvious."""
    groups = {}
    for p in conn.execute("SELECT id, page_no, page_type, render_path FROM pages WHERE file_id = ? ORDER BY page_no",
                          (file_id,)):
        groups.setdefault(slide_page(p["page_no"]), []).append(
            {"id": p["id"], "label": slide_label(p["page_no"]), "type": p["page_type"] or "",
             "has_image": bool(p["render_path"])})
    return [{"page": page, "slides": slides} for page, slides in sorted(groups.items())]


def _topic_counts(summaries) -> list:
    """[{topic, pending, clean}] for the generated-questions file: pending items, and those without warnings."""
    counts = {}
    for s in summaries:
        c = counts.setdefault(s["topic"], {"topic": s["topic"], "pending": 0, "clean": 0})
        if s["status"] == "pending":
            c["pending"] += 1
            c["clean"] += 0 if s["warnings"] else 1
    return sorted(counts.values(), key=lambda c: c["topic"])


def _item_summary(item, slides=False, card_versions=None) -> dict:
    record = json.loads(item["record_json"])
    summary = {"id": item["id"], "item_id": item["item_id"], "status": item["status"], "kind": item["kind"],
               "warnings": json.loads(item["warnings_json"]), "edited": item["edited"], "where": "",
               "topic": record.get("topic", ""), "stale": False}
    if record.get("source") == "generated":
        # A card that has changed since: the question is still usable until regenerated or rejected.
        current = (card_versions or {}).get(record.get("origin_card_id"))
        summary["stale"] = item["status"] in ("pending", "approved") and current not in (None, record.get("origin_card_version"))
    refs = json.loads(item["page_refs_json"])
    if slides and refs:  # page refs of a slide handout are slide numbers
        summary["where"] = f"sheet {slide_page(refs[0])}, slide {slide_label(refs[0])}" + (
            f" to {slide_label(refs[-1])}" if len(refs) > 1 else "")
    if item["kind"] == "card":
        summary.update({"label": record.get("section") or "Card", "text": record.get("prompt", ""),
                        "detail": (record.get("answer") or {}).get("format", "")})
    elif record.get("source") == "generated":
        summary.update({"label": item["item_id"], "text": record.get("question", ""),
                        "detail": f"[{record.get('marks')}]"})
    else:
        summary.update({"label": record.get("question_label") or f"Q{item['qnum']}{item['part']}",
                        "text": record.get("question", ""), "detail": f"[{record.get('marks')}]"})
    return summary


@router.get("/files/{file_id}", response_class=HTMLResponse)
def file_page(request: Request, file_id: int, conn=Depends(get_conn)):
    if not is_parent(request):
        return _login_redirect(request)
    return _file_page(request, conn, file_id, topic=request.query_params.get("topic", ""))


def _item_page(request, conn, staged_id, error="", record_text=None, status_code=200, message=""):
    item = staging.get_item(conn, staged_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    record = json.loads(item["record_json"])
    refs = json.loads(item["page_refs_json"])
    marks = ",".join("?" * len(refs)) or "NULL"
    # The item's own pages, plus every page of its question that the student will see.
    refs = sorted(set(refs) | {i.get("page") for i in record.get("page_images") or [] if i.get("page")})
    marks = ",".join("?" * len(refs)) or "NULL"
    pages = conn.execute(f"SELECT id, page_no, printed_page, render_path, display_path FROM pages WHERE file_id = ? "
                         f"AND page_no IN ({marks}) ORDER BY page_no", [item["file_id"]] + refs).fetchall()
    next_item = conn.execute("SELECT id FROM staged_items WHERE file_id = ? AND status = 'pending' AND id != ? "
                             "ORDER BY id LIMIT 1", (item["file_id"], staged_id)).fetchone()
    # A likely duplicate is shown beside the card it overlaps with (an approved card).
    duplicate = get_card(conn, record["possible_duplicate"]) if record.get("possible_duplicate") else None
    # A generated question is shown beside the card it came from, and flagged if that card has changed since.
    origin, stale = (generate_store.origin_of(conn, record) if record.get("source") == "generated"
                     else (None, False))
    sendback = None  # an approved question can be taken out of the bank and sent back to pending
    if item["status"] == "approved" and item["kind"] == "question":
        entry = unapprove.plan_entry(conn, item)
        sendback = {"attempts": entry[2], "in_bank": entry[3]}
    return templates.TemplateResponse(request, "review_item.html", {
        "sendback": sendback, "message": message,
        "item": item, "r": record, "duplicate": duplicate, "origin": origin,
        "stale": stale and item["status"] in ("pending", "approved"),
        "slide_file": conn.execute("SELECT layout FROM source_files WHERE id = ?",
                                   (item["file_id"],)).fetchone()["layout"] == "slides_4up",
        "slide_label": slide_label, "slide_page": slide_page, "warnings": json.loads(item["warnings_json"]),
        "pages": pages,
        "record_text": record_text if record_text is not None else json.dumps(record, indent=2, ensure_ascii=False),
        "error": error, "next_id": next_item["id"] if next_item else None, "is_parent": True,
    }, status_code=status_code)


@router.get("/items/{staged_id}", response_class=HTMLResponse)
def item_page(request: Request, staged_id: int, conn=Depends(get_conn)):
    if not is_parent(request):
        return _login_redirect(request)
    return _item_page(request, conn, staged_id)


@router.get("/render/{page_id}")
def render_image(request: Request, page_id: int, conn=Depends(get_conn)):
    """A page render, for a logged-in parent only, and only from inside data/renders."""
    if not is_parent(request):
        raise HTTPException(status_code=403, detail="Parent PIN required")
    row = conn.execute("SELECT render_path, display_path FROM pages WHERE id = ?", (page_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No render for this page")
    # The sharper display copy when there is one (question pages), else the model-size copy.
    path = resolve_render(row["display_path"]) or resolve_render(row["render_path"])
    if path is None:
        raise HTTPException(status_code=404, detail="No render for this page")
    return FileResponse(path, media_type=media_type(path))


def _after_action(conn, item) -> RedirectResponse:
    """Go to the next pending item in the file, or back to the file."""
    nxt = conn.execute("SELECT id FROM staged_items WHERE file_id = ? AND status = 'pending' ORDER BY id LIMIT 1",
                       (item["file_id"],)).fetchone()
    return _redirect(f"/review/items/{nxt['id']}" if nxt else f"/review/files/{item['file_id']}")


@router.post("/items/{staged_id}/approve", dependencies=[Depends(require_parent)])
def approve_item(request: Request, staged_id: int, conn=Depends(get_conn)):
    try:
        staging.approve(conn, staged_id)
    except staging.ReviewError as e:
        return _item_page(request, conn, staged_id, "Can't approve: " + "; ".join(e.problems), status_code=422)
    return _after_action(conn, staging.get_item(conn, staged_id))


@router.post("/items/{staged_id}/reject", dependencies=[Depends(require_parent)])
def reject_item(request: Request, staged_id: int, conn=Depends(get_conn)):
    try:
        staging.reject(conn, staged_id)
    except staging.ReviewError as e:
        return _item_page(request, conn, staged_id, "; ".join(e.problems), status_code=422)
    return _after_action(conn, staging.get_item(conn, staged_id))


@router.post("/items/{staged_id}/unapprove", dependencies=[Depends(require_parent)])
def unapprove_item(request: Request, staged_id: int, conn=Depends(get_conn)):
    """One approved question out of the bank and back to pending. Unattempted: deleted from the bank.
    Attempted: retired (hidden from practice, her attempts stay). Then it can be edited, approved again or rejected."""
    item = staging.get_item(conn, staged_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if item["status"] != "approved" or item["kind"] != "question":
        return _item_page(request, conn, staged_id, "Only an approved question can be sent back to pending.",
                          status_code=422)
    work = unapprove.plan_item(conn, item)
    unapprove.apply_plan(conn, work, include_attempted=True)
    attempts = (work["retire"] or work["delete"])[0][2]
    message = ("Hidden from practice (she has attempted it, so her history is kept) and back to pending."
               if attempts else "Removed from the question bank and back to pending.")
    f = conn.execute("SELECT * FROM source_files WHERE id = ?", (item["file_id"],)).fetchone()
    if f["material_type"] != staging.GENERATED_TYPE:  # a generated question has no answer key to match
        source = conn.execute("SELECT * FROM sources WHERE id = ?", (f["source_id"],)).fetchone()
        try:
            staging.match_source(conn, source["id"], load_source(Path(source["path"])), topic_ids(conn))
            message += " Answers were matched again: check it before approving."
        except (SourceError, OSError) as e:
            message += f" Answers could not be matched again ({e}): run reassemble --apply for this file."
    return _item_page(request, conn, staged_id, message=message)


@router.post("/items/{staged_id}/edit", dependencies=[Depends(require_parent)])
def edit_item(request: Request, staged_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    text = form.get("record", "")
    try:
        staging.save_edit(conn, staged_id, text, topic_ids(conn))
    except staging.ReviewError as e:
        return _item_page(request, conn, staged_id, "Not saved: " + "; ".join(e.problems), text, status_code=422)
    return _redirect(f"/review/items/{staged_id}")


@router.post("/items/{staged_id}/regenerate", dependencies=[Depends(require_parent)])
def regenerate_item(request: Request, staged_id: int, conn=Depends(get_conn)):
    """A new API call for this question's card. Its pending questions are replaced; approved ones stay."""
    item = staging.get_item(conn, staged_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    record = json.loads(item["record_json"])
    if record.get("source") != "generated" or not record.get("origin_card_id"):
        return _item_page(request, conn, staged_id, "Only generated questions can be regenerated.", status_code=422)
    try:
        result = generate_run.regenerate_card(conn, record["origin_card_id"])
    except (LLMError, ValueError) as e:
        return _item_page(request, conn, staged_id, f"Not regenerated: {e}", status_code=502)
    note = f" {len(result['dropped'])} dropped." if result["dropped"] else ""
    return _file_page(request, conn, item["file_id"],
                      f"Regenerated {record['origin_card_id']}: {result['staged']} new pending question(s).{note}")


@router.post("/files/{file_id}/approve-clean-topic", dependencies=[Depends(require_parent)])
def approve_clean_topic(request: Request, file_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    topic = form.get("topic", "")
    count = staging.approve_clean_topic(conn, file_id, topic)
    return _file_page(request, conn, file_id, f"Approved {count} item(s) without warnings in {topic}.", topic=topic)


@router.post("/files/{file_id}/unapprove", dependencies=[Depends(require_parent)])
def unapprove_file(request: Request, file_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    """Approved by mistake: this file's approved questions go back to pending (see app/ingest/unapprove.py),
    then the source's answers are matched again so the pending items are rebuilt. No API calls."""
    f = conn.execute("SELECT * FROM source_files WHERE id = ? AND status != 'superseded'", (file_id,)).fetchone()
    if f is None:
        raise HTTPException(status_code=404, detail="File not found")
    if f["material_type"] not in unapprove.QUESTION_FILES:
        return _file_page(request, conn, file_id, "Only a questions file's approved items can be sent back.",
                          status_code=422)
    include = form.get("include_attempted") == "1"
    work = unapprove.plan(conn, f)
    unapprove.apply_plan(conn, work, include)
    changed = len(work["delete"]) + (len(work["retire"]) if include else 0)
    message = f"Sent {changed} approved question(s) back to pending."
    if work["retire"] and not include:
        message += f" Left {len(work['retire'])} she has attempted as they were."
    source = conn.execute("SELECT * FROM sources WHERE id = ?", (f["source_id"],)).fetchone()
    try:
        staging.match_source(conn, source["id"], load_source(Path(source["path"])), topic_ids(conn))
        message += " Answers were matched again: check the items before approving them."
    except (SourceError, OSError) as e:
        message += f" Answers could not be matched again ({e}): run reassemble --apply for this file."
    return _file_page(request, conn, file_id, message)


@router.post("/files/{file_id}/approve-clean", dependencies=[Depends(require_parent)])
def approve_clean(request: Request, file_id: int, conn=Depends(get_conn)):
    count = staging.approve_clean(conn, file_id)
    return _file_page(request, conn, file_id, f"Approved {count} item(s) without warnings.")
