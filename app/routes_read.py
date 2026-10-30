"""'Read about this': the button's panel (before submit, after submit, in Revise) and the chapter page viewer.

The panel shows 2-3 book references with page thumbnails (app/read_about.py). The viewer serves only pages of notes
files, by file and page number (never by path), and checks again at serve time. Parents can switch the feature off
(read_enabled) and the before-submit use off (read_before_submit).
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from app import app_settings, mode_level, practice, read_about, revise, session
from app.importer import get_question
from app.page_images import media_type
from app.parent import is_parent
from app.revision_cards import get_card
from app.topics import parent_study_level
from app.web import form_fields, form_lists, get_conn, templates

router = APIRouter()


def _level(request, conn, mode: str, session_id):
    """The Study level to use: the parent's inside a session, else her chip choice this visit."""
    return parent_study_level(conn) if session_id else mode_level.current(request, conn, mode)


def _require(conn, stage: str) -> None:
    if not read_about.allowed(app_settings.get_all(conn), stage):
        raise HTTPException(status_code=403, detail="Read about this is switched off")


def _panel(request, refs: list, before: bool):
    return templates.TemplateResponse(request, "_read_panel.html", {
        "refs": refs, "before": before, "is_parent": is_parent(request)})


# ---- The button -----------------------------------------------------------------------------------------

@router.post("/q/{question_id}/read", response_class=HTMLResponse)
def read_before(request: Request, question_id: str, form=Depends(form_fields), conn=Depends(get_conn)):
    """Before she submits. Counts as help: a hint_uses row (tier 5) marks her next attempt "with help"."""
    _require(conn, "before")
    question = get_question(conn, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    session_id = session.session_of(form.get("scope", ""))
    refs = read_about.references_for_question(conn, question, _level(request, conn, "practice", session_id))
    if refs:   # nothing shown, nothing recorded
        practice.record_hint(conn, question_id, read_about.HINT_TIER)
        read_about.record_use(conn, "question", question_id, question["subject"], "before", session_id=session_id)
    return _panel(request, refs, before=True)


@router.post("/attempts/{attempt_id}/read", response_class=HTMLResponse)
def read_after(request: Request, attempt_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    """After she submits: always available, not counted as help."""
    _require(conn, "after")
    attempt = practice.get_attempt(conn, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="Attempt not found")
    question = practice.question_for_attempt(conn, attempt)
    session_id = session.session_of(form.get("scope", ""))
    refs = read_about.references_for_question(conn, question, _level(request, conn, "practice", session_id))
    if refs:
        read_about.record_use(conn, "question", question["id"], question["subject"], "after",
                              attempt_id=attempt_id, session_id=session_id)
    return _panel(request, refs, before=False)


@router.post("/revise/cards/{card_id}/read", response_class=HTMLResponse)
def read_card(request: Request, card_id: str, form=Depends(form_lists), conn=Depends(get_conn)):
    """In Revise (card view and after recall): always available, not counted as help."""
    _require(conn, "revise")
    card = get_card(conn, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    raw = (form.get("set_id") or [""])[-1]
    revise_set = revise.get_set(conn, int(raw)) if raw.isdigit() else None
    session_id = revise_set.get("session_id") if revise_set else None
    refs = read_about.references_for_card(conn, card, _level(request, conn, "revise", session_id))
    if refs:
        read_about.record_use(conn, "card", card_id, card["subject"], "revise",
                              set_id=revise_set["id"] if revise_set else None, session_id=session_id)
    return _panel(request, refs, before=False)


# ---- The chapter viewer ---------------------------------------------------------------------------------------

@router.get("/read/{file_id}/page/{page_no}")
def page_image(file_id: int, page_no: int, conn=Depends(get_conn)):
    """One page of a notes file. 404 for any other file or page (answers, exam papers, covers, blanks...)."""
    _require(conn, "after")
    path = read_about.page_image(conn, file_id, page_no)
    if path is None:
        raise HTTPException(status_code=404, detail="No page image")
    return FileResponse(path, media_type=media_type(path))


@router.get("/read/{file_id}", response_class=HTMLResponse)
def chapter(request: Request, file_id: int, p: int = 1, conn=Depends(get_conn)):
    """A simple page viewer for a notes file: previous / next and a page counter, starting at page `p`
    (or the nearest page that can be shown)."""
    _require(conn, "after")
    file = read_about.notes_file(conn, file_id)
    pages = read_about.viewable_pages(conn, file_id) if file else []
    if not pages:
        raise HTTPException(status_code=404, detail="Nothing to show")
    start = next((n for n in pages if n >= p), pages[-1])
    i = pages.index(start)
    row = conn.execute("SELECT printed_page FROM pages WHERE file_id = ? AND page_no = ?", (file_id, start)).fetchone()
    slide = file["layout"] == read_about.SLIDES
    return templates.TemplateResponse(request, "read_chapter.html", {
        "file": file, "page_no": start, "i": i, "n": len(pages), "slide": slide,
        "printed": (row["printed_page"] if row else "") or "",
        "prev_url": f"/read/{file_id}?p={pages[i - 1]}" if i > 0 else "",
        "next_url": f"/read/{file_id}?p={pages[i + 1]}" if i + 1 < len(pages) else "",
        "image_url": f"/read/{file_id}/page/{start}", "is_parent": is_parent(request),
    })
