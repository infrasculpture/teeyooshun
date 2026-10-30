"""'Ask about this': the chat routes (student) and the saved-conversations page (parent, behind the PIN)."""

import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import app_settings, ask, mode_level, practice, revise
from app.importer import get_question
from app.parent import is_parent
from app.revision_cards import get_card
from app.web import form_fields, get_conn, templates

router = APIRouter()
KEY = re.compile(r"^[A-Za-z0-9_-]{6,40}$")


def _chat(request, conn, scope, post_url, form):
    """Run one message and draw the panel again with the conversation so far."""
    key = form.get("key", "")
    if not KEY.match(key):
        raise HTTPException(status_code=400, detail="Bad conversation")
    try:
        result = ask.send(conn, scope, key, form.get("text", ""))
    except ask.WrongScope:
        raise HTTPException(status_code=400, detail="Bad conversation") from None
    return templates.TemplateResponse(request, "_ask_panel.html", {
        "ask": ask.panel(conn, post_url, key=key, set_id=scope.set_id, messages=result["messages"],
                         notice=result["notice"], text=result["text"]),
    })


def _require_on(conn) -> None:
    if not app_settings.get_all(conn)["ask_enabled"]:
        raise HTTPException(status_code=403, detail="Ask about this is switched off")


@router.post("/ask/question/{question_id}", response_class=HTMLResponse)
def ask_question(request: Request, question_id: str, form=Depends(form_fields), conn=Depends(get_conn)):
    """Before she submits: the payload has no mark scheme and no answer points from the cards."""
    _require_on(conn)
    question = get_question(conn, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    scope = ask.question_scope(conn, question, mode_level.current(request, conn, "practice"))
    return _chat(request, conn, scope, f"/ask/question/{quote(question_id)}", form)


@router.post("/ask/attempt/{attempt_id}", response_class=HTMLResponse)
def ask_attempt(request: Request, attempt_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    """After she submits: her answer, the marking and the explanation are included."""
    _require_on(conn)
    attempt = practice.get_attempt(conn, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="Attempt not found")
    scope = ask.attempt_scope(conn, attempt, mode_level.current(request, conn, "practice"))
    return _chat(request, conn, scope, f"/ask/attempt/{attempt_id}", form)


@router.post("/ask/card/{card_id}", response_class=HTMLResponse)
def ask_card(request: Request, card_id: str, form=Depends(form_fields), conn=Depends(get_conn)):
    _require_on(conn)
    card = get_card(conn, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    raw = form.get("set_id", "")
    set_id = int(raw) if raw.isdigit() else None  # an unknown set is treated as the standalone card page
    if set_id is not None and revise.get_set(conn, set_id) is None:
        set_id = None
    scope = ask.card_scope(conn, card, set_id, mode_level.current(request, conn, "revise"))
    return _chat(request, conn, scope, f"/ask/card/{quote(card_id)}", form)


@router.get("/parent/discussions", response_class=HTMLResponse)
def discussions(request: Request, conn=Depends(get_conn)):
    if not is_parent(request):
        return RedirectResponse("/parent/login?next=/parent/discussions", status_code=303)
    return templates.TemplateResponse(request, "parent_discussions.html", {
        "items": ask.list_discussions(conn), "ask_on": app_settings.get_all(conn)["ask_enabled"], "is_parent": True,
    })
