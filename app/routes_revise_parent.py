"""Parent pages for Revise mode (behind the PIN): the quiz view and the settings page."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import app_settings, revise, revise_help
from app.parent import is_parent, require_parent
from app.revision_cards import get_card
from app.topics import set_topic_status, topics_in_use
from app.web import form_fields, form_lists, get_conn, templates

router = APIRouter(prefix="/parent")


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _login(request: Request) -> RedirectResponse:
    return _redirect(f"/parent/login?next={quote(request.url.path)}")


# ---- Quiz: a parent asks, she answers aloud, the parent ticks ---------------------------------

def _quiz_start_page(request, conn, message=""):
    subjects = [dict(s, topics=revise.list_topics(conn, s["subject"])) for s in revise.list_subjects(conn)]
    return templates.TemplateResponse(request, "parent_quiz_start.html", {
        "subjects": subjects, "due": len(revise.due_only(conn, dict(app_settings.get_all(conn), cards_per_set=9999))),
        "message": message, "is_parent": True,
    })


@router.get("/quiz", response_class=HTMLResponse)
def quiz_start(request: Request, conn=Depends(get_conn)):
    if not is_parent(request):
        return _login(request)
    return _quiz_start_page(request, conn)


@router.post("/quiz/start", dependencies=[Depends(require_parent)])
def quiz_begin(request: Request, form=Depends(form_fields), conn=Depends(get_conn)):
    settings = app_settings.get_all(conn)
    subject, topic = form.get("subject", ""), form.get("topic", "")
    if form.get("choice") == "due":
        card_ids = revise.due_only(conn, settings)
        subject, topic = "", ""
    else:
        card_ids = revise.build_set(conn, subject, [topic] if topic else [], settings, exam_mode=False)
    if not card_ids:
        return _quiz_start_page(request, conn, "There are no cards to ask for that choice right now.")
    set_id = revise.create_set(conn, card_ids=card_ids, subject=subject, topics=[topic] if topic else [],
                               kind="parent_quiz")
    return _redirect(f"/parent/quiz/{set_id}/0")


def _quiz_card(conn, set_id: int, i: int):
    revise_set = revise.get_set(conn, set_id)
    if revise_set is None or revise_set["kind"] != "parent_quiz" or not 0 <= i < len(revise_set["card_ids"]):
        raise HTTPException(status_code=404, detail="No such card in this quiz")
    card = get_card(conn, revise_set["card_ids"][i])
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return revise_set, card


@router.get("/quiz/{set_id}/done", response_class=HTMLResponse)
def quiz_done(request: Request, set_id: int, conn=Depends(get_conn)):
    if not is_parent(request):
        return _login(request)
    revise_set = revise.get_set(conn, set_id)
    if revise_set is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    return templates.TemplateResponse(request, "revise_summary.html", {
        "set": revise_set, "summary": revise.set_summary(conn, revise_set), "is_parent": True,
    })


@router.get("/quiz/{set_id}/{i}", response_class=HTMLResponse)
def quiz_card(request: Request, set_id: int, i: int, conn=Depends(get_conn)):
    if not is_parent(request):
        return _login(request)
    revise_set, card = _quiz_card(conn, set_id, i)
    return templates.TemplateResponse(request, "parent_quiz.html", {
        "set": revise_set, "i": i, "n": len(revise_set["card_ids"]), "card": card, "a": card["answer"],
        "units": revise.check_units(card), "done": revise.set_attempt(conn, set_id, card["id"]) is not None,
        "show_terms": True, "is_parent": True,
    })


@router.post("/quiz/{set_id}/{i}", dependencies=[Depends(require_parent)])
def quiz_tick(set_id: int, i: int, form=Depends(form_lists), conn=Depends(get_conn)):
    revise_set, card = _quiz_card(conn, set_id, i)
    if not revise.set_attempt(conn, set_id, card["id"]):
        units = revise.check_units(card)
        revise.record_attempt(conn, card, method="parent_quiz", set_id=set_id, units_total=len(units),
                              covered_ids=revise.tick_check(units, form.get("unit")),
                              settings=app_settings.get_all(conn))
    last = i + 1 >= len(revise_set["card_ids"])
    return _redirect(f"/parent/quiz/{set_id}/done" if last else f"/parent/quiz/{set_id}/{i + 1}")


# ---- Card help: review, edit and regenerate "Help me remember" text ---------------------------------

def _help_page(request, conn, topic="", message="", status_code=200):
    flags = revise_help.open_flags(conn)
    rows = conn.execute(
        "SELECT id FROM revision_cards WHERE (? = '' OR topic = ?) ORDER BY topic, sort_key, id",
        (topic, topic)).fetchall()
    cards = []
    for r in rows:
        card = get_card(conn, r["id"])
        cards.append({"card": card, "help": revise_help.get(conn, card["id"], card["version"]),
                      "flags": [f for f in flags if f["card_id"] == card["id"]]})
    cards.sort(key=lambda c: not c["flags"])  # flagged cards first (the sort keeps book order otherwise)
    return templates.TemplateResponse(request, "parent_card_help.html", {
        "cards": cards, "topic": topic, "topics": [r["topic"] for r in conn.execute("SELECT DISTINCT topic FROM revision_cards ORDER BY topic")],
        "message": message, "is_parent": True,
    }, status_code=status_code)


@router.get("/card-help", response_class=HTMLResponse)
def card_help_page(request: Request, topic: str = "", conn=Depends(get_conn)):
    if not is_parent(request):
        return _login(request)
    return _help_page(request, conn, topic)


def _help_card(conn, card_id: str) -> dict:
    card = get_card(conn, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return card


@router.post("/card-help/{card_id}/edit", dependencies=[Depends(require_parent)])
def card_help_edit(request: Request, card_id: str, form=Depends(form_fields), conn=Depends(get_conn)):
    card = _help_card(conn, card_id)
    if not revise_help.save_edit(conn, card, form):
        return _help_page(request, conn, form.get("topic", ""),
                          "Why this matters, The marks are in and Common slip can't be empty. Nothing was saved.", 422)
    revise_help.resolve_flags(conn, card_id)
    return _redirect(f"/parent/card-help?topic={quote(form.get('topic', ''))}")


@router.post("/card-help/{card_id}/regenerate", dependencies=[Depends(require_parent)])
def card_help_regenerate(request: Request, card_id: str, form=Depends(form_fields), conn=Depends(get_conn)):
    """Write the help again. Help a parent has edited is kept, so it isn't replaced here."""
    card = _help_card(conn, card_id)
    topic = form.get("topic", "")
    before = revise_help.get(conn, card_id, card["version"])
    if before and before["edited_by_parent"]:
        return _help_page(request, conn, topic, "That help was edited by a parent, so it was kept. Edit it instead.")
    if revise_help.regenerate(conn, card) is None:
        return _help_page(request, conn, topic, "Couldn't write new help just now. Try again.", 503)
    revise_help.resolve_flags(conn, card_id)
    return _redirect(f"/parent/card-help?topic={quote(topic)}")


@router.post("/card-help/{card_id}/checked", dependencies=[Depends(require_parent)])
def card_help_checked(card_id: str, form=Depends(form_fields), conn=Depends(get_conn)):
    revise_help.resolve_flags(conn, card_id)
    return _redirect(f"/parent/card-help?topic={quote(form.get('topic', ''))}")


# ---- Settings: Revise behaviour and which topics are on -----------------------------------------

def _settings_page(request, conn, message="", problems=(), values=None, status_code=200):
    return templates.TemplateResponse(request, "parent_settings.html", {
        "s": values or app_settings.get_all(conn), "topics": topics_in_use(conn),
        "message": message, "problems": list(problems), "is_parent": True,
    }, status_code=status_code)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, conn=Depends(get_conn)):
    if not is_parent(request):
        return _login(request)
    return _settings_page(request, conn)


@router.post("/settings", dependencies=[Depends(require_parent)])
def save_settings(request: Request, form=Depends(form_fields), conn=Depends(get_conn)):
    values = app_settings.from_form(form)
    problems = app_settings.save(conn, values)
    if problems:
        return _settings_page(request, conn, problems=problems, values=values, status_code=422)
    return _settings_page(request, conn, "Settings saved. They apply from the next set.")


@router.post("/topics", dependencies=[Depends(require_parent)])
def save_topics(request: Request, form=Depends(form_fields), conn=Depends(get_conn)):
    """Each topic arrives as status:<topic id> = taught / not_taught / "" (not set)."""
    for name, value in form.items():
        if name.startswith("status:"):
            set_topic_status(conn, name[len("status:"):], value)
    return _settings_page(request, conn, "Topics saved. A topic set to 'not taught' is hidden in Practise and Revise.")
