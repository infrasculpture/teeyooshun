"""Shared web helpers: templates, database connection per request, form reading."""

import logging
import time
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app import rewards
from app import rewards_config
from app import session_track
from app.db import connect
from app.feedback import highlight
from app.marking.tutor import render_bold
from app.practice import subject_label, topic_label

log = logging.getLogger("uvicorn.error")
ASSET_VERSION = "5b-2"   # change when theme.css or the static art changes, so browsers fetch the new file
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.filters["topic_label"] = topic_label
templates.env.filters["subject_label"] = subject_label
# Text with its key terms highlighted (HTML-escaped first), for revision cards.
templates.env.filters["mark_terms"] = lambda text, terms=None: highlight(text or "", terms or [])
# Tutor explanation text: **bold** only, everything else escaped.
templates.env.filters["md_bold"] = render_bold


def _cosmetics() -> dict:
    """Her equipped frame and scene and the companion picture, for base.html. Never breaks a page."""
    try:
        conn = connect()
        try:
            return rewards.cosmetics(conn)
        finally:
            conn.close()
    except Exception:
        log.exception("Rewards: couldn't read cosmetics")
        return {"frame": "", "scene": "", "companion": rewards_config.COMPANION_DEFAULT_IMAGE,
                "companion_name": rewards_config.COMPANION_NAME}


def _pending_requests() -> int:
    """How many real-world reward requests wait for a parent (the badge on the parent link). Never breaks a page."""
    try:
        conn = connect()
        try:
            return rewards.pending_count(conn)
        finally:
            conn.close()
    except Exception:
        log.exception("Rewards: couldn't count pending requests")
        return 0


templates.env.globals["pending_requests"] = _pending_requests
templates.env.globals["cosmetics"] = _cosmetics
templates.env.globals["asset_version"] = ASSET_VERSION


def get_conn():
    """Route dependency: one database connection per request."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


async def form_fields(request: Request) -> dict:
    """Route dependency: the posted form as {name: value}.

    Plain HTML forms and htmx both post URL-encoded data, which the standard
    library reads; this avoids adding the python-multipart package.
    """
    body = await request.body()
    parsed = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
    return {name: values[-1] for name, values in parsed.items()}


async def form_lists(request: Request) -> dict:
    """Route dependency: the posted form as {name: [values]}, for groups of checkboxes."""
    body = await request.body()
    return parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)


def track_activity(request: Request, conn, session_id) -> int:
    """She is active on a session: log it (a gap of more than 3 minutes is idle time) and let base.html load the
    page's activity pinger. Returns the length of the idle gap in seconds, 0 if there was none."""
    request.state.track_sid = session_id or ""
    request.state.idle = session_track.touch(conn, session_id) if session_id else 0
    return request.state.idle


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def now_seconds() -> int:
    return int(time.time())
