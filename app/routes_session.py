"""Student pages for the session: the Today home screen and the step-by-step flow (docs/STUDENT_UI_SPEC.md sections 2-3)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import session, session_plan
from app.parent import is_parent
from app.practice import subject_label
from app.web import form_fields, get_conn, templates

router = APIRouter()


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _load(conn, session_id: int) -> dict:
    s = session.get(conn, session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return s


# ---- Home ("Today") -----------------------------------------------------------------------------

def _minutes(value: str) -> int:
    return int(value) if value.isdigit() and int(value) in session_plan.LENGTHS else session_plan.LENGTHS[0]


def _subject_choice(value: str) -> str:
    """A subject chip's value, or "" for anything else (the rotation decides)."""
    return value if value in session_plan.CHOICES or value == session_plan.MIX else ""


def _home(request, conn, message="", status_code=200):
    now = session_plan.utc_now_dt()
    day = session_plan.local_day(now)
    suggested = session_plan.pick_subject(conn, day)
    chosen = session.todays_choice(conn, now)
    if chosen != session_plan.MIX:   # a remembered subject that has no content now falls back to the suggestion
        chosen = session_plan.pick_subject(conn, day, chosen)
    plan = session.preview(conn, session_plan.LENGTHS[0], now, chosen)
    available = set(session_plan.subjects_with_content(conn))
    return templates.TemplateResponse(request, "home.html", {
        "plan_text": plan["text"], "has_work": plan["has_work"], "lengths": session_plan.LENGTHS,
        "subjects": [{"key": k, "label": subject_label(k), "enabled": k in available} for k in session_plan.CHOICES],
        "suggested": suggested, "selected": chosen, "mix": session_plan.MIX,
        "resume": session.open_today(conn), "year_end": bool(session_plan.question_pool(conn, generated=True)),
        "message": message, "is_parent": is_parent(request),
    }, status_code=status_code)


@router.get("/", response_class=HTMLResponse)
def home(request: Request, conn=Depends(get_conn)):
    return _home(request, conn)


@router.get("/session/preview", response_class=HTMLResponse)
def plan_preview(request: Request, subject: str = "", minutes: str = "", conn=Depends(get_conn)):
    """The plan text and Start button for the subject she has just picked (swapped in by htmx)."""
    plan = session.preview(conn, _minutes(minutes), subject=_subject_choice(subject))
    return templates.TemplateResponse(request, "_home_plan.html", {"plan_text": plan["text"], "has_work": plan["has_work"]})


@router.post("/session/start")
def start(request: Request, form=Depends(form_fields), conn=Depends(get_conn)):
    session_id = session.create_daily(conn, _minutes(form.get("minutes", "")), subject=_subject_choice(form.get("subject", "")))
    if session_id is None:
        return _home(request, conn, "There is nothing to study yet. A parent needs to add questions or cards.", 200)
    return _redirect(f"/session/{session_id}")


@router.post("/session/year-end")
def start_year_end(request: Request, conn=Depends(get_conn)):
    session_id = session.create_year_end(conn, session_plan.LENGTHS[0])
    if session_id is None:
        return _home(request, conn, "There are no approved practice questions for the year-end review yet.", 200)
    return _redirect(f"/session/{session_id}")


# ---- The session ---------------------------------------------------------------------------------

@router.get("/session/{session_id}", response_class=HTMLResponse)
def session_page(request: Request, session_id: int, conn=Depends(get_conn)):
    s = session.refresh(conn, _load(conn, session_id))
    pos = session.position(s)
    base = {"s": s, "is_parent": is_parent(request)}
    if pos["state"] == "break":
        return templates.TemplateResponse(request, "session_break.html", base)
    if pos["state"] == "ended" or pos["step"]["key"] == "wrapup":
        return templates.TemplateResponse(request, "session_wrap.html", {
            **base, "summary": session.summary(conn, s), "ended": pos["state"] == "ended",
            "can_extend": pos["state"] != "ended" and s["kind"] == "daily", "lengths": session_plan.LENGTHS})
    step = pos["step"]
    if step["started_at"] and step["key"] in session.CARD_STEPS:
        return _redirect(session.cards_url(conn, step))
    if step["started_at"] and step["key"] in session.QUESTION_STEPS:
        return _redirect(session.next_question_url(conn, s, step))
    return templates.TemplateResponse(request, "session_step.html", {
        **base, "step": step, "index": pos["index"] + 1, "n": pos["n"], "topic": session_plan.topic_title(
            conn, session.current_block(s)["focus_topic"]) if session.current_block(s)["focus_topic"] else "",
    })


@router.post("/session/{session_id}/start-step")
def start_step(session_id: int, conn=Depends(get_conn)):
    s = _load(conn, session_id)
    session.start_step(conn, s)
    return _redirect(f"/session/{session_id}")


@router.post("/session/{session_id}/done")
def done(session_id: int, conn=Depends(get_conn)):
    """"Let's go" on the focus screen, or "Finish" on the wrap-up."""
    session.finish_step(conn, _load(conn, session_id), "done")
    return _redirect(f"/session/{session_id}")


@router.post("/session/{session_id}/skip")
def skip(session_id: int, conn=Depends(get_conn)):
    session.finish_step(conn, _load(conn, session_id), "skipped")
    return _redirect(f"/session/{session_id}")


@router.post("/session/{session_id}/swap")
def swap(session_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    session.swap_focus(conn, _load(conn, session_id), form.get("topic", ""))
    return _redirect(f"/session/{session_id}")


@router.post("/session/{session_id}/more")
def more(session_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    """"Take a break, then keep going" from the wrap-up: another 30 or 60 minute block after the break screen."""
    minutes = int(form["minutes"]) if form.get("minutes", "").isdigit() else 0
    session.extend(conn, _load(conn, session_id), minutes)
    return _redirect(f"/session/{session_id}")


@router.post("/session/{session_id}/continue")
def continue_after_break(session_id: int, conn=Depends(get_conn)):
    session.next_block(conn, _load(conn, session_id))
    return _redirect(f"/session/{session_id}")
