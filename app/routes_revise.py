"""Revise mode pages: start, study (with the optional underline step), recall, check, result, summary.

docs/REVISE_SPEC.md. The answer is never on the recall page; it is shown again only at the
check (self-tick) or result step.
"""

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app import app_settings, practice, revise
from app.feedback import highlight
from app.page_images import media_type, student_image
from app.parent import is_parent
from app.revision_cards import get_card, get_card_version
from app.web import form_lists, get_conn, now_seconds, templates

router = APIRouter(prefix="/revise")


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _first(form: dict, name: str, default: str = "") -> str:
    values = form.get(name) or []
    return values[-1] if values else default


def _load(conn, set_id: int, i: int):
    revise_set = revise.get_set(conn, set_id)
    if revise_set is None or revise_set["kind"] != "student" or not 0 <= i < len(revise_set["card_ids"]):
        raise HTTPException(status_code=404, detail="No such card in this set")
    card = get_card(conn, revise_set["card_ids"][i])
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
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


def _card_view(conn, card: dict) -> dict:
    """What the study view needs for one card."""
    split = revise.siblings(conn, card)
    return {
        "card": card, "a": card["answer"], "units": revise.check_units(card),
        "self_tick_only": revise.is_self_tick_only(card),
        "page_count": len(card.get("page_images") or []),
        "split": {"k": split[0], "n": split[1], "ids": split[2]} if split else None,
    }


# ---- Start -----------------------------------------------------------------------

def _start_page(request, conn, subject="", message="", status_code=200):
    return templates.TemplateResponse(request, "revise_start.html", {
        "subjects": revise.list_subjects(conn), "subject": subject,
        "topics": revise.list_topics(conn, subject) if subject else None,
        "settings": app_settings.get_all(conn), "message": message, "is_parent": is_parent(request),
    }, status_code=status_code)


@router.get("", response_class=HTMLResponse)
def start(request: Request, subject: str = "", conn=Depends(get_conn)):
    return _start_page(request, conn, subject)


@router.post("/start")
def start_set(request: Request, form=Depends(form_lists), conn=Depends(get_conn)):
    subject = _first(form, "subject")
    topics = [t for t in form.get("topic") or [] if t]
    exam = _first(form, "exam") == "on"
    settings = app_settings.get_all(conn)
    card_ids = revise.build_set(conn, subject, topics, settings, exam_mode=exam)
    if not card_ids:
        due = revise.next_due(conn, subject, topics)
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
        "tapping": tapping, "underline": underline, "show_terms": not tapping,
        "word_lines": revise.underline_words(card) if tapping else None, "is_parent": is_parent(request),
    })
    return templates.TemplateResponse(request, "revise_study.html", view)


@router.get("/sets/{set_id}/study/{i}", response_class=HTMLResponse)
def study(request: Request, set_id: int, i: int, conn=Depends(get_conn)):
    revise_set, card = _load(conn, set_id, i)
    settings = app_settings.get_all(conn)
    tapping = bool(settings["underline_step"]) and not revise.is_self_tick_only(card) \
        and bool(revise.study_lines(card))
    return _study_page(request, conn, revise_set, card, i, tapping=tapping)


@router.post("/sets/{set_id}/underline/{i}", response_class=HTMLResponse)
def underline(request: Request, set_id: int, i: int, form=Depends(form_lists), conn=Depends(get_conn)):
    """Compare the words she tapped with the card's key terms. Code only; nothing is stored."""
    revise_set, card = _load(conn, set_id, i)
    return _study_page(request, conn, revise_set, card, i, underline=revise.underline_result(card, form.get("w")))


@router.get("/cards/{card_id}", response_class=HTMLResponse)
def card_page(request: Request, card_id: str, conn=Depends(get_conn)):
    """One card on its own (used for the links between the parts of a split card)."""
    card = get_card(conn, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    view = _card_view(conn, card)
    view.update({"set": None, "i": 0, "n": 1, "next_url": "", "tapping": False, "underline": None,
                 "show_terms": True, "word_lines": None, "is_parent": is_parent(request)})
    return templates.TemplateResponse(request, "revise_study.html", view)


@router.get("/cards/{card_id}/page/{index}")
def card_page_image(card_id: str, index: int, conn=Depends(get_conn)):
    card = get_card(conn, card_id)
    path = student_image(conn, card, index) if card else None
    if path is None:
        raise HTTPException(status_code=404, detail="No page image")
    return FileResponse(path, media_type=media_type(path))


# ---- Recall -----------------------------------------------------------------------

def _recall_page(request, conn, revise_set, card, i, text="", error="", shown_at=None, status_code=200):
    settings = app_settings.get_all(conn)
    return templates.TemplateResponse(request, "revise_recall.html", {
        "set": revise_set, "i": i, "n": len(revise_set["card_ids"]),
        # Prompt only: nothing from the answer is passed to this page.
        "prompt": card.get("prompt", ""), "command_word": card.get("command_word", ""),
        "section": card.get("section", ""), "self_tick_only": revise.is_self_tick_only(card),
        "default_method": settings["recall_method"], "text": text, "error": error,
        "shown_at": shown_at or now_seconds(), "is_parent": is_parent(request),
    }, status_code=status_code)


@router.get("/sets/{set_id}/recall/{i}", response_class=HTMLResponse)
def recall(request: Request, set_id: int, i: int, conn=Depends(get_conn)):
    revise_set, card = _load(conn, set_id, i)
    if revise.set_attempt(conn, set_id, card["id"]):
        return _redirect(f"/revise/sets/{set_id}/result/{i}")
    return _recall_page(request, conn, revise_set, card, i)


@router.post("/sets/{set_id}/typed/{i}")
def typed(request: Request, set_id: int, i: int, form=Depends(form_lists), conn=Depends(get_conn)):
    revise_set, card = _load(conn, set_id, i)
    if revise.set_attempt(conn, set_id, card["id"]):
        return _redirect(f"/revise/sets/{set_id}/result/{i}")
    raw, shown_at = _first(form, "answer"), _first(form, "shown_at")
    if revise.is_self_tick_only(card):
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
        typed_text=text, result=check["result"], api_call_id=check["api_call_id"],
        seconds_taken=practice.seconds_since(shown_at),
        command_note_text=revise.command_note(card.get("command_word", ""), text, len(units)))
    return _redirect(f"/revise/sets/{set_id}/result/{i}")


# ---- Self-tick (paper, image cards, or after a failed typed check) --------------------------------

@router.get("/sets/{set_id}/tick/{i}", response_class=HTMLResponse)
def tick_page(request: Request, set_id: int, i: int, t: str = "", conn=Depends(get_conn)):
    revise_set, card = _load(conn, set_id, i)
    if revise.set_attempt(conn, set_id, card["id"]):
        return _redirect(f"/revise/sets/{set_id}/result/{i}")
    view = _card_view(conn, card)
    view.update({"set": revise_set, "i": i, "n": len(revise_set["card_ids"]), "show_terms": True,
                 "shown_at": t, "action": f"/revise/sets/{set_id}/tick/{i}", "is_parent": is_parent(request)})
    return templates.TemplateResponse(request, "revise_tick.html", view)


@router.post("/sets/{set_id}/tick/{i}")
def tick(request: Request, set_id: int, i: int, form=Depends(form_lists), conn=Depends(get_conn)):
    revise_set, card = _load(conn, set_id, i)
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
    revise_set, card = _load(conn, set_id, i)
    attempt = revise.set_attempt(conn, set_id, card["id"])
    if attempt is None:
        return _redirect(f"/revise/sets/{set_id}/recall/{i}")
    card = get_card_version(conn, card["id"], attempt["card_version"]) or card  # as she saw it
    covered = set(json.loads(attempt["covered_ids_json"]))
    view = _card_view(conn, card)
    typed_html = None
    if attempt["typed_text"]:
        marking = json.loads(attempt["result_json"]) if attempt["result_json"] else {}
        quotes = [p["evidence_quote"] for p in (marking.get("detail") or {}).get("points", []) if p.get("awarded")]
        typed_html = highlight(attempt["typed_text"], quotes)
    settings = app_settings.get_all(conn)
    view.update({
        "set": revise_set, "i": i, "n": len(revise_set["card_ids"]), "attempt": attempt, "covered": covered,
        "typed_html": typed_html, "key_terms_missed": json.loads(attempt["key_terms_missed_json"]),
        "known": attempt["box_after"] > 1, "interval_days": settings["box_intervals"][attempt["box_after"] - 1],
        "next_url": _after_result(revise_set, i), "show_terms": True, "is_parent": is_parent(request),
    })
    return templates.TemplateResponse(request, "revise_result.html", view)


@router.get("/sets/{set_id}/summary", response_class=HTMLResponse)
def summary(request: Request, set_id: int, conn=Depends(get_conn)):
    revise_set = revise.get_set(conn, set_id)
    if revise_set is None:
        raise HTTPException(status_code=404, detail="Set not found")
    return templates.TemplateResponse(request, "revise_summary.html", {
        "set": revise_set, "summary": revise.set_summary(conn, revise_set), "is_parent": is_parent(request),
    })
