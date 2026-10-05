"""Review page for staged ingestion items (parent PIN). INGESTION_SPEC.md section 7."""

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.ingest import staging
from app.page_images import media_type, resolve_render
from app.parent import is_parent, require_parent
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
    files = conn.execute(FILE_COUNTS.format(where="")).fetchall()
    sources = {}
    for f in files:
        sources.setdefault(f["path"], {"title": f["title"], "slug": f["slug"], "files": []})["files"].append(f)
    return templates.TemplateResponse(request, "review_sources.html",
                                      {"sources": sources, "is_parent": True})


def _file_page(request, conn, file_id, message="", status_code=200):
    row = conn.execute(FILE_COUNTS.format(where="AND f.id = ?"), (file_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="File not found")
    items = conn.execute("SELECT * FROM staged_items WHERE file_id = ? AND status != 'superseded' ORDER BY id",
                         (file_id,)).fetchall()
    pages = conn.execute("SELECT page_no, page_type, status, error FROM pages WHERE file_id = ? "
                         "AND status IN ('failed', 'skipped') ORDER BY page_no", (file_id,)).fetchall()
    return templates.TemplateResponse(request, "review_file.html", {
        "f": row, "items": [_item_summary(i) for i in items], "pages": pages,
        "orphans": [dict(o, scheme=json.loads(o["scheme_json"])) for o in staging.orphan_answers(conn, file_id)],
        "totals": staging.file_totals(conn, file_id), "warnings": json.loads(row["warnings_json"]),
        "numbering": staging.numbering_check(conn, file_id),
        "message": message, "is_parent": True,
    }, status_code=status_code)


def _item_summary(item) -> dict:
    record = json.loads(item["record_json"])
    return {"id": item["id"], "item_id": item["item_id"], "status": item["status"], "qnum": item["qnum"],
            "part": item["part"], "question": record.get("question", ""), "marks": record.get("marks"),
            "warnings": json.loads(item["warnings_json"]), "edited": item["edited"]}


@router.get("/files/{file_id}", response_class=HTMLResponse)
def file_page(request: Request, file_id: int, conn=Depends(get_conn)):
    if not is_parent(request):
        return _login_redirect(request)
    return _file_page(request, conn, file_id)


def _item_page(request, conn, staged_id, error="", record_text=None, status_code=200):
    item = staging.get_item(conn, staged_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    record = json.loads(item["record_json"])
    refs = json.loads(item["page_refs_json"])
    marks = ",".join("?" * len(refs)) or "NULL"
    # The item's own pages, plus every page of its question that the student will see.
    refs = sorted(set(refs) | {i.get("page") for i in record.get("page_images") or [] if i.get("page")})
    marks = ",".join("?" * len(refs)) or "NULL"
    pages = conn.execute(f"SELECT id, page_no, render_path, display_path FROM pages WHERE file_id = ? "
                         f"AND page_no IN ({marks}) ORDER BY page_no", [item["file_id"]] + refs).fetchall()
    next_item = conn.execute("SELECT id FROM staged_items WHERE file_id = ? AND status = 'pending' AND id != ? "
                             "ORDER BY id LIMIT 1", (item["file_id"], staged_id)).fetchone()
    return templates.TemplateResponse(request, "review_item.html", {
        "item": item, "r": record, "warnings": json.loads(item["warnings_json"]), "pages": pages,
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


@router.post("/items/{staged_id}/edit", dependencies=[Depends(require_parent)])
def edit_item(request: Request, staged_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    text = form.get("record", "")
    try:
        staging.save_edit(conn, staged_id, text, topic_ids(conn))
    except staging.ReviewError as e:
        return _item_page(request, conn, staged_id, "Not saved: " + "; ".join(e.problems), text, status_code=422)
    return _redirect(f"/review/items/{staged_id}")


@router.post("/files/{file_id}/approve-clean", dependencies=[Depends(require_parent)])
def approve_clean(request: Request, file_id: int, conn=Depends(get_conn)):
    count = staging.approve_clean(conn, file_id)
    return _file_page(request, conn, file_id, f"Approved {count} item(s) without warnings.")
