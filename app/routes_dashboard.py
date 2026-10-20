"""The parent dashboard page (behind the PIN) and its one action: "Make this week's focus"."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import dashboard, schedule, session_plan
from app.parent import is_parent, require_parent
from app.web import form_fields, get_conn, templates

router = APIRouter(prefix="/parent")


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, period: str = "7", conn=Depends(get_conn)):
    if not is_parent(request):
        return RedirectResponse(f"/parent/login?next={quote('/parent/dashboard')}", status_code=303)
    return templates.TemplateResponse(request, "parent_dashboard.html", {"d": dashboard.build(conn, period), "is_parent": True})


@router.post("/dashboard/focus", dependencies=[Depends(require_parent)])
def make_focus(form=Depends(form_fields), conn=Depends(get_conn)):
    """Sets the existing focus-topic setting for 7 days (the same rules and storage as the settings page)."""
    today = session_plan.local_day(session_plan.utc_now_dt())
    problems = schedule.save_focus(conn, form.get("subject", ""), form.get("topic", ""), "", today)
    if problems:
        raise HTTPException(status_code=400, detail=" ".join(problems))
    period = form.get("period", "7")
    return RedirectResponse(f"/parent/dashboard?period={period if period in dashboard.PERIODS else '7'}#topics",
                            status_code=303)
