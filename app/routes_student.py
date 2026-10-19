"""Student pages: start, topics, question, submit, feedback, rewrite, skip, "looks wrong"."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app import app_settings, practice, question_help, session
from app.feedback import build_view, command_note
from app.importer import get_question
from app.marking import mcq_explain, tutor
from app.page_images import media_type, student_image
from app.parent import is_parent
from app.web import form_fields, get_conn, is_htmx, now_seconds, templates

router = APIRouter()


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _next_url(subject: str, scope: str) -> str:
    session_id = session.session_of(scope)
    if session_id:  # inside a session, "next" means the session's next question or step
        return f"/session/{session_id}"
    url = f"/practice?subject={quote(subject)}"
    return url + (f"&topic={quote(scope)}" if scope else "")


# ---- Start and topics ------------------------------------------------------------

@router.get("/choose", response_class=HTMLResponse)
def start(request: Request, subject: str = "", conn=Depends(get_conn)):
    """"Choose my own": subject, then topic. The home screen ("/") is the session (routes_session.py)."""
    return _start_page(request, conn, subject)


def _start_page(request, conn, subject="", message=""):
    subjects = practice.list_subjects(conn)
    topics = practice.list_topics(conn, subject) if subject else None
    return templates.TemplateResponse(request, "start.html", {
        "subjects": subjects, "subject": subject, "topics": topics,
        "message": message, "is_parent": is_parent(request),
    })


@router.get("/topics", response_class=HTMLResponse)
def topics(request: Request, subject: str, conn=Depends(get_conn)):
    return templates.TemplateResponse(request, "_topics.html", {
        "subject": subject, "topics": practice.list_topics(conn, subject),
    })


@router.get("/practice")
def next_question(request: Request, subject: str, topic: str = "", conn=Depends(get_conn)):
    question_id = practice.next_question_id(conn, subject, topic or None)
    if question_id is None:
        return _start_page(request, conn, subject, "There are no questions for that choice yet.")
    return _redirect(f"/q/{quote(question_id)}" + (f"?scope={quote(topic)}" if topic else ""))


# ---- Question and first attempt ----------------------------------------------------

def _question_page(request, conn, question, scope, answer="", error="", shown_at=None, status_code=200):
    q = practice.question_for_student(question)  # never the mark scheme
    return templates.TemplateResponse(request, "question.html", {
        "q": q, "command_note": command_note(q["command_word"], q["marks"]),
        "bar": session.bar(conn, session.session_of(scope)),  # "Step 3 of 6", only inside a session
        "scope": scope, "answer": answer, "error": error,
        "shown_at": shown_at or now_seconds(), "is_parent": is_parent(request),
        "help_on": app_settings.get_all(conn)["help_enabled"],  # the parent can switch "Help me" off
    }, status_code=status_code)


@router.get("/q/{question_id}", response_class=HTMLResponse)
def question_page(request: Request, question_id: str, scope: str = "", conn=Depends(get_conn)):
    question = get_question(conn, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    return _question_page(request, conn, question, scope)


@router.get("/q/{question_id}/page/{index}")
def question_page_image(question_id: str, index: int, conn=Depends(get_conn)):
    """A page image for the question: question pages only, never answer pages."""
    question = get_question(conn, question_id)
    path = student_image(conn, question, index) if question else None
    if path is None:
        raise HTTPException(status_code=404, detail="No page image")
    return FileResponse(path, media_type=media_type(path))


@router.post("/q/{question_id}/help", response_class=HTMLResponse)
def help_open(request: Request, question_id: str, conn=Depends(get_conn)):
    """"Help me": opens the next tier of help for this question (before she submits). Nothing from the mark
    scheme is shown except the related card in tier 3. Opening a tier marks her next attempt "with help"."""
    question = get_question(conn, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    settings = app_settings.get_all(conn)
    if not settings["help_enabled"]:
        raise HTTPException(status_code=403, detail="Help is switched off")
    view = question_help.open_help(conn, question, settings)
    card = view["card"]
    return templates.TemplateResponse(request, "_question_help.html", {
        **view, "q": practice.question_for_student(question),
        "a": card["answer"] if card else None, "show_terms": True,  # for _card_answer.html
    })


@router.post("/q/{question_id}/answer")
def submit_answer(request: Request, question_id: str, form=Depends(form_fields), conn=Depends(get_conn)):
    question = get_question(conn, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    scope, shown_at, raw = form.get("scope", ""), form.get("shown_at"), form.get("answer", "")
    try:
        if question["marking_mode"] == "mcq":
            answer = practice.clean_choice(question, raw)
        else:
            answer = practice.clean_answer(raw)
    except ValueError as e:
        return _question_page(request, conn, question, scope, raw, str(e), shown_at, 422)
    try:
        attempt_id = practice.submit_first_attempt(conn, question_id, answer, practice.seconds_since(shown_at))
    except practice.MarkingFailed:
        return _question_page(request, conn, question, scope, raw,
                              "Marking didn't work just now. Your answer is still here, so please try again.",
                              shown_at, 503)
    if question["marking_mode"] == "mcq":
        # Scored by code already. Fetch or write the short explanation for this choice;
        # if that fails, the feedback page still shows the result with a "try again" link.
        mcq_explain.explain(conn, question, answer)
    else:
        _tutor_explain(conn, attempt_id)
    return _redirect(f"/attempts/{attempt_id}" + (f"?scope={quote(scope)}" if scope else ""))


def _tutor_explain(conn, attempt_id: int, force: bool = False) -> None:
    """Write (or rewrite) the tutor explanation for a marked attempt. A failure changes nothing:
    the page shows the existing feedback with a "Try again" button."""
    attempt = practice.get_attempt(conn, attempt_id)
    tutor.generate(conn, attempt, practice.question_for_attempt(conn, attempt), force=force)


@router.post("/attempts/{attempt_id}/tutor")
def tutor_again(request: Request, attempt_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    """"Try again" for a missing tutor explanation; a parent can also regenerate a stored one."""
    _load_attempt(conn, attempt_id)
    _tutor_explain(conn, attempt_id, force=bool(form.get("regenerate")) and is_parent(request))
    scope = form.get("scope", "")
    return _redirect(f"/attempts/{attempt_id}?all=1" + (f"&scope={quote(scope)}" if scope else ""))


@router.post("/attempts/{attempt_id}/explain")
def explain_again(attempt_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    """Try again for an MCQ explanation that couldn't be loaded. Uses the stored one if it exists."""
    attempt = practice.get_attempt(conn, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="Attempt not found")
    question = practice.question_for_attempt(conn, attempt)
    if question["marking_mode"] == "mcq":
        mcq_explain.explain(conn, question, attempt["answer_text"])
    scope = form.get("scope", "")
    return _redirect(f"/attempts/{attempt_id}" + (f"?scope={quote(scope)}" if scope else ""))


# ---- Feedback, rewrite, skip -----------------------------------------------------------

def _load_attempt(conn, attempt_id):
    attempt = practice.get_attempt(conn, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="Attempt not found")
    return attempt


def _feedback_page(request, conn, attempt, scope, error="", rewrite_answer=None, status_code=200, open_all=False):
    view = build_view(conn, attempt, is_parent(request))
    view.update({
        "scope": scope, "error": error, "shown_at": now_seconds(), "q": view["question"],
        "command_note": command_note(view["question"]["command_word"], view["question"]["marks"]),
        # All steps open when she comes back to an attempt, or when a rewrite needs fixing.
        "open_all": open_all or bool(error),
        "rewrite_answer": attempt["answer_text"] if rewrite_answer is None else rewrite_answer,
        "next_url": _next_url(view["question"]["subject"], scope),
    })
    return templates.TemplateResponse(request, "feedback.html", view, status_code=status_code)


@router.get("/attempts/{attempt_id}", response_class=HTMLResponse)
def feedback(request: Request, attempt_id: int, scope: str = "", all: str = "", conn=Depends(get_conn)):
    return _feedback_page(request, conn, _load_attempt(conn, attempt_id), scope, open_all=bool(all))


@router.post("/attempts/{attempt_id}/rewrite")
def rewrite(request: Request, attempt_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    first = _load_attempt(conn, attempt_id)
    scope, raw = form.get("scope", ""), form.get("answer", "")
    try:
        answer = practice.clean_answer(raw)
    except ValueError as e:
        return _feedback_page(request, conn, first, scope, str(e), raw, 422)
    try:
        new_id = practice.submit_rewrite(conn, attempt_id, answer, practice.seconds_since(form.get("shown_at")))
    except practice.NotAllowed:
        return _redirect(f"/attempts/{attempt_id}")
    except practice.MarkingFailed:
        return _feedback_page(request, conn, first, scope,
                              "Marking didn't work just now. Your rewrite is still here, so please try again.",
                              raw, 503)
    _tutor_explain(conn, new_id)
    return _redirect(f"/attempts/{new_id}" + (f"?scope={quote(scope)}" if scope else ""))


@router.post("/attempts/{attempt_id}/skip")
def skip(request: Request, attempt_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    attempt = _load_attempt(conn, attempt_id)
    try:
        practice.skip_rewrite(conn, attempt_id)
    except practice.NotAllowed:
        return _redirect(f"/attempts/{attempt_id}")
    question = practice.question_for_attempt(conn, attempt)
    return _redirect(_next_url(question["subject"], form.get("scope", "")))


# ---- "This marking looks wrong" (student or parent) ----------------------------------------

@router.post("/attempts/{attempt_id}/looks-wrong")
def looks_wrong(request: Request, attempt_id: int, form=Depends(form_fields), conn=Depends(get_conn)):
    attempt = _load_attempt(conn, attempt_id)
    parent = is_parent(request)
    practice.add_event(conn, attempt_id, "marking_looks_wrong", "parent" if parent else "student",
                       form.get("note", ""))
    if is_htmx(request):
        return templates.TemplateResponse(request, "_looks_wrong.html", {
            "attempt": attempt, "is_parent": parent,
            "looks_wrong": [e for e in practice.events_for(conn, attempt_id) if e["event"] == "marking_looks_wrong"],
        })
    return _redirect(f"/attempts/{attempt_id}")
