"""Shared web helpers: templates, database connection per request, form reading."""

import time
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.db import connect
from app.practice import subject_label, topic_label

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.filters["topic_label"] = topic_label
templates.env.filters["subject_label"] = subject_label


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


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def now_seconds() -> int:
    return int(time.time())
