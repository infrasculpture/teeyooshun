"""Revise mode pages: start, study (with the optional underline step), recall, check, result, summary.

docs/REVISE_SPEC.md. The answer is never on the recall page; it is shown again only at the
check (self-tick) or result step.
"""

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app import app_settings, ask, mode_level, practice, read_about, revise, revise_help, session
from app.feedback import highlight
from app.page_images import media_type, student_image
from app.parent import is_parent
from app.revision_cards import get_card, get_card_version
from app.web import form_lists, get_conn, now_seconds, templates, track_activity

router = APIRouter(prefix="/revise")


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _first(form: dict, name: str, default: str = "") -> str:
    values = form.get(name) or []
    return values[-1] if values else default


def _load(conn, set_id: int, i: int, request=None):
    revise_set = revise.get_set(conn, set_id)
    if revise_set is None or revise_set["kind"] != "student" or not 0 <= i < len(revise_set["card_ids"]):
        raise HTTPException(status_code=404, detail="No such card in this set")
    card = get_card(conn, revise_set["card_ids"][i])
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    if request is not None:   # a card of a session step: log her activity and load the pinger
        track_activity(request, conn, revise_set.get("session_id"))
    return revise_set, card


def _after_study(revise_set: dict, i: int) -> str:
    base, n = f"/revise/sets/{revise_set['id']}", len(revise_set["card_ids"])
    if revise_set["recall_timing"] == "after_each":
        return f"{base}/recall/{i}"
    return f"{base}/study/{i + 1}" if i + 1 < n else f"{base}/recall/0"


def _after_result(revise_set: dict, i: int) -> str:
    base, n = f"/revise/sets/{revise_set['id']}", len(revise_set["card_ids"])
    if i + 1 >= n:
        return f"{base}/summary"
    return f"{base}/study/{i + 1}" if revise_set["recall_timing"] == "after_each" else f"{base}/recall/{i + 1}"


def _bar(conn, revise_set, i):
    """The session's "Step 2 of 5 · Card 3 of 8" bar, when this set is a step of a session.
    `i` is None on the study grid, which shows the whole set ("All 4 cards")."""
    if not revise_set or not revise_set.get("session_id"):
        return None
    return session.set_bar(conn, revise_set["session_id"], revise_set["id"], 0 if i is None else i + 1,
                           len(revise_set["card_ids"]))


def _grid_mode(revise_set: dict, settings: dict) -> bool:
    """A set studied "after the whole set" shows all its cards together, in a grid. One card at a time stays for
    after-each sets (Quick-fire too), one-card sets and the underline step, which taps words card by card."""
    return (revise_set["recall_timing"] == "after_set" and len(revise_set["card_ids"]) > 1
            and not settings["underline_step"])


def _card_view(conn, card: dict) -> dict:
    """What the study view needs for one card."""
    split = revise.siblings(conn, card)
    return {
        "card": card, "a": card["answer"], "units": revise.check_units(card),
        "self_tick_only": revise.is_self_tick_only(card),
        "page_count": len(card.get("page_images") or []),
        "read_on": read_about.allowed(app_settings.get_all(conn), "revise"),   # "Read about this" (not counted as help)
        "split": {"k": split[0], "n": split[1], "ids": split[2]} if split else None,
    }


# ---- Start -----------------------------------------------------------------------

def _start_page(request, conn, subject="", message="", status_code=200, level=None):
    level = level or mode_level.current(request, conn, "revise")
    return templates.TemplateResponse(request, "revise_start.html", {
        "subjects": revise.list_subjects(conn, level=level), "subject": subject,
        "topics": revise.list_topics(conn, subject, level=level) if subject else None,
        "settings": app_settings.get_all(conn), "message": message, "is_parent": is_parent(request),
        "chips": mode_level.chips(level, "/revise", subject),
    }, status_code=status_code)


@router.get("", response_class=HTMLResponse)
def start(request: Request, subject: str = "", lv: str = "", conn=Depends(get_conn)):
    """The level chips start at the parent's Study level on every fresh visit (see app/mode_level.py)."""
    level, action = mode_level.resolve(request, conn, "revise", lv)
    response = _start_page(request, conn, subject, level=level)
    mode_level.apply(response, "revise", action)
    return response


@router.post("/start")
def start_set(request: Request, form=Depends(form_lists), conn=Depends(get_conn)):
    subject = _first(form, "subject")
    topics = [t for t in form.get("topic") or [] if t]
    exam = _first(form, "exam") == "on"
    settings = app_settings.get_all(conn)
    level = mode_level.current(request, conn, "revise")
    card_ids = revise.build_set(conn, subject, topics, settings, exam_mode=exam, level=level)
    if not card_ids:
        due = revise.next_due(conn, subject, topics, level)
        message = "There are no cards for that choice yet."
        if due:
            message = (f"Nothing is due right now. The next card is due on {due[:10]}. "
                       "Tick exam mode to revise anyway.")
        return _start_page(request, conn, subject, message)
    set_id = revise.create_set(conn, card_ids=card_ids, subject=subject, topics=topics, exam_mode=exam,
                               recall_timing=settings["recall_timing"])
    return _redirect(f"/revise/sets/{set_id}/study/0")


# ---- Study and the underline step -----------------------------------------------------------

def _study_page(request, conn, revise_set, card, i, underline=None, tapping=False):
    view = _card_view(conn, card)
    view.update({
        "set": revise_set, "i": i, "n": len(revise_set["card_ids"]), "next_url": _after_study(revise_set, i),
        "bar": _bar(conn, revise_set, i),
        "tapping": tapping, "underline": underline, "show_terms": not tapping,
        "word_lines": revise.underline_words(card) if tapping else None, "is_parent": is_parent(request),
        "ask": ask.panel(conn, f"/ask/card/{quote(card['id'])}", set_id=revise_set["id"]),
    })
    return templates.TemplateResponse(request, "revise_study.html", view)


def _grid_page(request, conn, revise_set):
    """The study view of a whole set: every card at once (prompt, answer, help), then on to the recall."""
    cards = [get_card(conn, card_id) for card_id in revise_set["card_ids"]]
    return templates.TemplateResponse(request, "revise_study.html", {
        "grid": [_card_view(conn, card) for card in cards if card], "set": revise_set, "n": len(revise_set["card_ids"]),
        "next_url": f"/revise/sets/{revise_set['id']}/recall/0", "bar": _bar(conn, revise_set, None),
        "ask_on": bool(app_settings.get_all(conn)["ask_enabled"]), "is_parent": is_parent(request),
    })


@router.get("/sets/{set_id}/study/{i}", response_class=HTMLResponse)
def study(request: Request, set_id: int, i: int, conn=Depends(get_conn)):
    revise_set, card = _load(conn, set_id, i, request)
    settings = app_settings.get_all(conn)
    if _grid_mode(revise_set, settings):
        return _grid_page(request, conn, revise_set)
    tapping = bool(settings["underline_step"]) and not revise.is_self_tick_only(card) \
        and bool(revise.study_lines(card))
    return _study_page(request, conn, revise_set, card, i, tapping=tapping)


@router.post("/sets/{set_id}/underline/{i}", response_class=HTMLResponse)
def underline(request: Request, set_id: int, i: int, form=Depends(form_lists), conn=Depends(get_conn)):
    """Compare the words she tapped with the card's key terms. Code only; nothing is stored."""
    revise_set, card = _load(conn, set_id, i, request)
    return _study_page(request, conn, revise_set, card, i, underline=revise.underline_result(card, form.get("w")))


@router.get("/cards/{card_id}", response_class=HTMLResponse)
def card_page(request: Request, card_id: str, conn=Depends(get_conn)):
    """One card on its own (used for the links between the parts of a split card)."""
    card = get_card(conn, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    view = _card_view(conn, card)
    view.update({"set": None, "i": 0, "n": 1, "next_url": "", "tapping": False, "underline": None,
                 "show_terms": True, "word_lines": None, "is_parent": is_parent(request),
                 "ask": ask.panel(conn, f"/ask/card/{quote(card['id'])}")})
    return templates.TemplateResponse(request, "revise_study.html", view)


@router.get("/cards/{card_id}/page/{index}")
def card_page_image(card_id: str, index: int, conn=Depends(get_conn)):
    card = get_card(conn, card_id)
    path = student_image(conn, card, index) if card else None
    if path is None:
        raise HTTPException(status_code=404, detail="No page image")
    return FileResponse(path, media_type=media_type(path))


# ---- "Help me remember" (docs/REVISE_SPEC.md section 11) ---------------------------------------

def _help_set_id(conn, form: dict):
    """The set she is studying in, or None on the standalone card page (or for an unknown set)."""
    raw = _first(form, "set_id")
    return int(raw) if raw.isdigit() and revise.get_set(conn, int(raw)) else None


def _help_panel(request, conn, card, set_id, help_, reply="", message="", question=""):
    return templates.TemplateResponse(request, "_card_help.html", {
        "card": card, "help": help_, "set_id": set_id, "reply": reply, "message": message, "question": question,
        "followups_left": revise_help.followups_left(conn, card["id"], set_id) if help_ else 0,
        "is_parent": is_parent(request),
    })


def _card_or_404(conn, card_id: str) -> dict:
    card = get_card(conn, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return card


@router.post("/cards/{card_id}/help", response_class=HTMLResponse)
def help_open(request: Request, card_id: str, form=Depends(form_lists), conn=Depends(get_conn)):
    """Open the help panel: the stored help, or written now (once). A failure shows a retry message."""
    card, set_id = _card_or_404(conn, card_id), _help_set_id(conn, form)
    help_ = revise_help.generate(conn, card)
    if help_ is None:
        return _help_panel(request, conn, card, set_id, None, message="Couldn't load help, try again")
    revise_help.record_view(conn, card, set_id)
    return _help_panel(request, conn, card, set_id, help_)


@router.post("/cards/{card_id}/help/followup", response_class=HTMLResponse)
def help_followup(request: Request, card_id: str, form=Depends(form_lists), conn=Depends(get_conn)):
    card, set_id = _card_or_404(conn, card_id), _help_set_id(conn, form)
    help_ = revise_help.get(conn, card_id, card["version"])
    if help_ is None:
        return _help_panel(request, conn, card, set_id, None, message="Couldn't load help, try again")
    question = _first(form, "question")
    try:
        reply = revise_help.followup(conn, card, question, set_id=set_id)
    except revise_help.FollowupLimit:
        return _help_panel(request, conn, card, set_id, help_, message="That's all the questions for this card.")
    if reply is None:
        message = "Type your question first." if not question.strip() else "Couldn't get an answer, try again."
        return _help_panel(request, conn, card, set_id, help_, message=message, question=question)
    return _help_panel(request, conn, card, set_id, help_, reply=reply)


@router.post("/cards/{card_id}/help/flag", response_class=HTMLResponse)
def help_flag(request: Request, card_id: str, form=Depends(form_lists), conn=Depends(get_conn)):
    card = _card_or_404(conn, card_id)
    revise_help.add_flag(conn, card, _first(form, "note"), "parent" if is_parent(request) else "student")
    return HTMLResponse("<p class=\"small\">Thanks. This help has been flagged"
                        + ("" if is_parent(request) else " and a parent will check it") + ".</p>")


# ---- Recall -----------------------------------------------------------------------

def _recall_page(request, conn, revise_set, card, i, text="", error="", shown_at=None, status_code=200):
    settings = app_settings.get_all(conn)
    return templates.TemplateResponse(request, "revise_recall.html", {
        "set": revise_set, "i": i, "n": len(revise_set["card_ids"]), "bar": _bar(conn, revise_set, i),
        # Prompt only: nothing from the answer is passed to this page.
        "prompt": card.get("prompt", ""), "command_word": card.get("command_word", ""),
        "section": card.get("section", ""), "self_tick_only": revise.recalls_by_self_tick(revise_set, card),
        "quickfire": bool(revise_set.get("self_tick")),
        "default_method": settings["recall_method"], "text": text, "error": error,
        "shown_at": shown_at or now_seconds(), "is_parent": is_parent(request),
    }, status_code=status_code)


@router.get("/sets/{set_id}/recall/{i}", response_class=HTMLResponse)
def recall(request: Request, set_id: int, i: int, conn=Depends(get_conn)):
    revise_set, card = _load(conn, set_id, i, request)
    if revise.set_attempt(conn, set_id, card["id"]):
        return _redirect(f"/revise/sets/{set_id}/result/{i}")
    return _recall_page(request, conn, revise_set, card, i)


@router.post("/sets/{set_id}/typed/{i}")
def typed(request: Request, set_id: int, i: int, form=Depends(form_lists), conn=Depends(get_conn)):
    revise_set, card = _load(conn, set_id, i, request)
    if revise.set_attempt(conn, set_id, card["id"]):
        return _redirect(f"/revise/sets/{set_id}/result/{i}")
    raw, shown_at = _first(form, "answer"), _first(form, "shown_at")
    if revise.recalls_by_self_tick(revise_set, card):
        return _redirect(f"/revise/sets/{set_id}/tick/{i}?t={quote(shown_at)}")
    try:
        text = practice.clean_answer(raw)
    except ValueError as e:
        return _recall_page(request, conn, revise_set, card, i, raw, str(e), shown_at, 422)
    try:
        check = revise.typed_check(conn, card, text)
    except revise.CheckFailed:
        return _recall_page(request, conn, revise_set, card, i, raw,
                            "The check didn't work just now. Try again, or tick it yourself.", shown_at, 503)
    units = revise.check_units(card)
    revise.record_attempt(
        conn, card, method="typed", covered_ids=check["covered_ids"], units_total=len(units),
        settings=app_settings.get_all(conn), set_id=set_id, key_terms_missed=check["key_terms_missed"],
        points_covered=check["covered_count"],
        typed_text=text, result=check["result"], api_call_id=check["api_call_id"],
        seconds_taken=practice.seconds_since(shown_at),
        command_note_text=revise.command_note(card.get("command_word", ""), text, len(units)))
    return _redirect(f"/revise/sets/{set_id}/result/{i}")


# ---- Self-tick (paper, image cards, or after a failed typed check) --------------------------------

@router.get("/sets/{set_id}/tick/{i}", response_class=HTMLResponse)
def tick_page(request: Request, set_id: int, i: int, t: str = "", conn=Depends(get_conn)):
    revise_set, card = _load(conn, set_id, i, request)
    if revise.set_attempt(conn, set_id, card["id"]):
        return _redirect(f"/revise/sets/{set_id}/result/{i}")
    view = _card_view(conn, card)
    view.update({"set": revise_set, "i": i, "n": len(revise_set["card_ids"]), "show_terms": True,
                 "bar": _bar(conn, revise_set, i), "shown_at": t, "action": f"/revise/sets/{set_id}/tick/{i}", "is_parent": is_parent(request)})
    return templates.TemplateResponse(request, "revise_tick.html", view)


@router.post("/sets/{set_id}/tick/{i}")
def tick(request: Request, set_id: int, i: int, form=Depends(form_lists), conn=Depends(get_conn)):
    revise_set, card = _load(conn, set_id, i, request)
    if not revise.set_attempt(conn, set_id, card["id"]):
        units = revise.check_units(card)
        revise.record_attempt(
            conn, card, method="paper", covered_ids=revise.tick_check(units, form.get("unit")),
            units_total=len(units), settings=app_settings.get_all(conn), set_id=set_id,
            seconds_taken=practice.seconds_since(_first(form, "shown_at")))
    return _redirect(f"/revise/sets/{set_id}/result/{i}")


# ---- Result and summary ------------------------------------------------------------------

@router.get("/sets/{set_id}/result/{i}", response_class=HTMLResponse)
def result(request: Request, set_id: int, i: int, conn=Depends(get_conn)):
    revise_set, card = _load(conn, set_id, i, request)
    attempt = revise.set_attempt(conn, set_id, card["id"])
    if attempt is None:
        return _redirect(f"/revise/sets/{set_id}/recall/{i}")
    card = get_card_version(conn, card["id"], attempt["card_version"]) or card  # as she saw it
    covered = set(json.loads(attempt["covered_ids_json"]))
    view = _card_view(conn, card)
    typed_html, extras = None, {"in_notes": [], "unverified": [], "incorrect": []}
    if attempt["typed_text"]:
        marking = json.loads(attempt["result_json"]) if attempt["result_json"] else {}
        detail = marking.get("detail") or {}
        quotes = [p["evidence_quote"] for p in detail.get("points", []) if p.get("awarded")]
        for e in detail.get("other_points") or []:
            extras.setdefault(e["classification"], []).append(e)
        typed_html = highlight(attempt["typed_text"], quotes + [e["quote"] for e in extras["in_notes"]])
    settings = app_settings.get_all(conn)
    next_url = _after_result(revise_set, i)
    session_id = revise_set.get("session_id")
    if session_id and i + 1 < len(revise_set["card_ids"]) and session.overtime(conn, session_id):
        next_url = f"/session/{session_id}"   # the planned length is used up: this card was the last one
    view.update({
        "set": revise_set, "i": i, "n": len(revise_set["card_ids"]), "bar": _bar(conn, revise_set, i),
        "attempt": attempt, "covered": covered,
        "typed_html": typed_html, "extras": extras, "key_terms_missed": json.loads(attempt["key_terms_missed_json"]),
        "known": attempt["box_after"] > 1, "interval_days": settings["box_intervals"][attempt["box_after"] - 1],
        "next_url": next_url, "show_terms": True, "is_parent": is_parent(request),
        "ask": ask.panel(conn, f"/ask/card/{quote(card['id'])}", set_id=set_id),
    })
    return templates.TemplateResponse(request, "revise_result.html", view)


@router.get("/sets/{set_id}/summary", response_class=HTMLResponse)
def summary(request: Request, set_id: int, conn=Depends(get_conn)):
    revise_set = revise.get_set(conn, set_id)
    if revise_set is None:
        raise HTTPException(status_code=404, detail="Set not found")
    track_activity(request, conn, revise_set.get("session_id"))
    return templates.TemplateResponse(request, "revise_summary.html", {
        "set": revise_set, "summary": revise.set_summary(conn, revise_set), "is_parent": is_parent(request),
    })
