"""Student pages for the session: the Today home screen and the step-by-step flow (docs/STUDENT_UI_SPEC.md sections 2-3)."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app import rewards, schedule, session, session_plan, session_track
from app.parent import is_parent
from app.practice import subject_label
from app.web import form_fields, get_conn, templates, track_activity

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
    rewards.award_pending(conn, now)
    session_track.sweep_abandoned(conn, now)   # record sessions she left and never came back to
    day = session_plan.local_day(now)
    suggested = session_plan.pick_subject(conn, day)
    default = schedule.for_day(conn, day)      # the parent's length and subject for this weekday
    # What she chose earlier today wins; else the weekday's subject (she can still change it); else the rotation.
    chosen = session.todays_choice(conn, now) or default["subject"]
    if chosen != session_plan.MIX:   # a remembered subject that has no content now falls back to the suggestion
        chosen = session_plan.pick_subject(conn, day, chosen)
    plan = session.preview(conn, default["minutes"], now, chosen)
    available = set(session_plan.subjects_with_content(conn))
    return templates.TemplateResponse(request, "home.html", {
        "plan_text": plan["text"], "has_work": plan["has_work"], "lengths": session_plan.LENGTHS,
        "default_minutes": default["minutes"],
        "subjects": [{"key": k, "label": subject_label(k), "enabled": k in available} for k in session_plan.CHOICES],
        "suggested": suggested, "selected": chosen, "mix": session_plan.MIX,
        "resume": session.open_today(conn), "year_end": bool(session_plan.question_pool(conn, generated=True)),
        "message": message, "is_parent": is_parent(request),
        # rewards (STUDENT_UI_SPEC.md section 8): rank bar, today's commissions, the small map
        "rank": rewards.rank_info(conn), "commissions": rewards.commissions_today(conn, now),
        "regions": rewards.map_view(conn),
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
    _load(conn, session_id)   # 404 for an unknown session
    idle = track_activity(request, conn, session_id)   # first, so the idle time is counted before the time-out check
    s = session.refresh(conn, _load(conn, session_id))
    session.apply_timeout(conn, s)   # planned length used up: cut the rest, on to the wrap-up
    rewards.award_pending(conn)   # steps she has just finished pay out here
    pos = session.position(s)
    timeout = s["plan"].get("timeout")
    base = {"s": s, "is_parent": is_parent(request), "timed_out": bool(timeout), "continued": bool(timeout and timeout.get("continued"))}
    if pos["state"] == "ended":
        request.state.track_sid = ""   # nothing more to track
    elif pos["state"] == "break":
        session_track.start_break(conn, s["id"], session_plan.utc_now_dt())
    elif idle:   # a gap of more than 3 minutes: ask before carrying on
        request.state.track_sid = ""   # this screen is the question; the page's own overlay would ask it twice
        return templates.TemplateResponse(request, "session_welcome.html", base)
    if pos["state"] == "break":
        return templates.TemplateResponse(request, "session_break.html", base)
    if pos["state"] == "ended" or pos["step"]["key"] == "wrapup":
        return templates.TemplateResponse(request, "session_wrap.html", {
            **base, "summary": session.summary(conn, s), "ended": pos["state"] == "ended",
            "earned": rewards.session_earnings(conn, s), "kpop": schedule.kpop_enabled(conn),
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


@router.post("/session/{session_id}/ping", status_code=204)
def ping(session_id: int, conn=Depends(get_conn)):
    """The page says she is still active (every 60 seconds while she types or taps). Nothing is sent back."""
    session_track.touch(conn, session_id)
    return Response(status_code=204)


@router.post("/session/{session_id}/carry-on")
def carry_on(session_id: int, conn=Depends(get_conn)):
    """"Carry on" on the Welcome back screen."""
    session_track.touch(conn, session_id)
    return _redirect(f"/session/{session_id}")


@router.post("/session/{session_id}/keep-going")
def keep_going(session_id: int, conn=Depends(get_conn)):
    """"Keep going" after the time-out: the rest of the plan comes back."""
    session_track.touch(conn, session_id)
    session.keep_going(conn, _load(conn, session_id))
    return _redirect(f"/session/{session_id}")


@router.post("/session/{session_id}/start-step")
def start_step(session_id: int, conn=Depends(get_conn)):
    session_track.touch(conn, session_id)
    s = _load(conn, session_id)
    session.start_step(conn, s)
    return _redirect(f"/session/{session_id}")


@router.post("/session/{session_id}/done")
def done(session_id: int, conn=Depends(get_conn)):
    """"Let's go" on the focus screen, or "Finish" on the wrap-up."""
    session_track.touch(conn, session_id)
    session.finish_step(conn, _load(conn, session_id), "done")
    return _redirect(f"/session/{session_id}")


@router.post("/session/{session_id}/skip")
def skip(session_id: int, conn=Depends(get_conn)):
    session_track.touch(conn, session_id)
    session.finish_step(conn, _load(conn, session_id), "skipped")
    return _redirect(f"/session/{session_id}")


@router.post("/session/{session_id}/swap")
def swap(session_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    session_track.touch(conn, session_id)
    session.swap_focus(conn, _load(conn, session_id), form.get("topic", ""))
    return _redirect(f"/session/{session_id}")


@router.post("/session/{session_id}/more")
def more(session_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    """"Take a break, then keep going" from the wrap-up: another 30 or 60 minute block after the break screen."""
    minutes = int(form["minutes"]) if form.get("minutes", "").isdigit() else 0
    session_track.touch(conn, session_id)
    session.extend(conn, _load(conn, session_id), minutes)
    return _redirect(f"/session/{session_id}")


@router.post("/session/{session_id}/continue")
def continue_after_break(session_id: int, conn=Depends(get_conn)):
    session_track.touch(conn, session_id)
    session.next_block(conn, _load(conn, session_id))
    return _redirect(f"/session/{session_id}")
