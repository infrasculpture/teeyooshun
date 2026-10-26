"""The parent dashboard page (behind the PIN) and its one action: "Make this week's focus"."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import dashboard, mode_level, schedule, session_plan, session_track
from app.parent import is_parent, require_parent
from app.web import form_fields, get_conn, templates

router = APIRouter(prefix="/parent")


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, period: str = "7", level: str = "all", conn=Depends(get_conn)):
    if not is_parent(request):
        return RedirectResponse(f"/parent/login?next={quote('/parent/dashboard')}", status_code=303)
    return templates.TemplateResponse(request, "parent_dashboard.html",
                                      {"d": dashboard.build(conn, period, level=level), "is_parent": True})


@router.get("/sessions", response_class=HTMLResponse)
def sessions_page(request: Request, conn=Depends(get_conn)):
    """The Session log: every session, newest first, with how it ended and how much of it she was really at it."""
    if not is_parent(request):
        return RedirectResponse(f"/parent/login?next={quote('/parent/sessions')}", status_code=303)
    return templates.TemplateResponse(request, "parent_sessions.html", {
        "sessions": dashboard.session_log(conn), "is_parent": True,
        "level_switches": mode_level.recent_switches(conn),
        "gap_minutes": session_track.GAP_SECONDS // 60, "short_words": session_track.SHORT_WORDS,
        "short_marks": session_track.SHORT_MIN_MARKS})


@router.post("/dashboard/focus", dependencies=[Depends(require_parent)])
def make_focus(form=Depends(form_fields), conn=Depends(get_conn)):
    """Sets the existing focus-topic setting for 7 days (the same rules and storage as the settings page)."""
    today = session_plan.local_day(session_plan.utc_now_dt())
    problems = schedule.save_focus(conn, form.get("subject", ""), form.get("topic", ""), "", today)
    if problems:
        raise HTTPException(status_code=400, detail=" ".join(problems))
    period, level = form.get("period", "7"), form.get("level", "all")
    url = f"/parent/dashboard?period={period if period in dashboard.PERIODS else '7'}"
    if level in ("sec1", "sec2"):   # "all" is the default, so it stays out of the address
        url += f"&level={level}"
    return RedirectResponse(url + "#topics", status_code=303)
