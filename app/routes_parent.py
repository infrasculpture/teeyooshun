"""Parent actions behind the PIN: login, logout, agree/disagree with a marking."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import practice
from app.feedback import build_view
from app.parent import (COOKIE_NAME, SESSION_HOURS, check_pin, make_cookie, parent_enabled,
                        require_parent, safe_next)
from app.web import form_fields, get_conn, is_htmx, templates

router = APIRouter()

LOGIN_ERRORS = {
    "wrong": ("That PIN isn't right.", 401),
    "locked": ("Too many wrong PINs. Try again in a few minutes.", 429),
    "disabled": ("Parent features are switched off. Set PARENT_PIN (not 0000) and SESSION_SECRET in .env.", 503),
}


def _login_page(request, next_url, error="", status_code=200):
    return templates.TemplateResponse(request, "parent_login.html", {
        "next": safe_next(next_url), "error": error, "enabled": parent_enabled(),
    }, status_code=status_code)


@router.get("/parent/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    return _login_page(request, next)


@router.post("/parent/login")
def login(request: Request, form=Depends(form_fields)):
    next_url = safe_next(form.get("next", "/"))
    outcome = check_pin(form.get("pin", ""))
    if outcome != "ok":
        message, status = LOGIN_ERRORS[outcome]
        return _login_page(request, next_url, message, status)
    response = RedirectResponse(next_url, status_code=303)
    response.set_cookie(COOKIE_NAME, make_cookie(), max_age=SESSION_HOURS * 3600,
                        httponly=True, samesite="lax")
    return response


@router.post("/parent/logout")
def logout(form=Depends(form_fields)):
    response = RedirectResponse(safe_next(form.get("next", "/")), status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@router.post("/attempts/{attempt_id}/review", dependencies=[Depends(require_parent)])
def review(request: Request, attempt_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    verdict = form.get("verdict", "")
    if verdict not in ("agree", "disagree"):
        raise HTTPException(status_code=400, detail="verdict must be agree or disagree")
    try:
        practice.add_event(conn, attempt_id, f"parent_{verdict}", "parent", form.get("note", ""))
    except practice.NotAllowed:
        raise HTTPException(status_code=404, detail="Attempt not found") from None
    if is_htmx(request):
        attempt = practice.get_attempt(conn, attempt_id)
        return templates.TemplateResponse(request, "_parent_panel.html", build_view(conn, attempt, True))
    return RedirectResponse(f"/attempts/{attempt_id}", status_code=303)
