"""The student's Sec 1 / Sec 2 / Both chips in Revise and Practice ("Choose my own").

Her choice lives only in a browser session cookie, one per mode. Opening the mode from the nav or the home screen
(a visit with no query string) clears it, so she always starts at the parent's Study level; tapping a chip sets it
for the rest of that visit. It never touches the parent's setting. Each real switch is logged for the parent's
session log page (the mode_level_log table). Today's session never reads this: it always uses the parent's setting.
"""

from datetime import datetime, timezone
from urllib.parse import quote

from app.db import utc_now
from app.session_plan import LOCAL_TZ, STAMP
from app.topics import LEVEL_LABELS, STUDY_LEVELS, parent_study_level

MODES = ("revise", "practice")
MODE_LABELS = {"revise": "Revise", "practice": "Practice"}


def _cookie(mode: str) -> str:
    return f"mode_level_{mode}"


def current(request, conn, mode: str) -> str:
    """The level she is using in this mode right now: her chip choice this visit, else the parent's setting."""
    value = request.cookies.get(_cookie(mode), "")
    return value if value in STUDY_LEVELS else parent_study_level(conn)


def resolve(request, conn, mode: str, lv: str = "") -> tuple:
    """For a mode's entry page: (level, cookie action). The action is ("set", level), ("clear", None) or None."""
    before = current(request, conn, mode)
    if lv in STUDY_LEVELS:
        if lv != before:
            log_switch(conn, mode, lv)
        return lv, ("set", lv)
    if not request.query_params:   # a fresh visit (nav link, home screen): back to the parent's level
        return parent_study_level(conn), ("clear", None)
    return before, None


def apply(response, mode: str, action) -> None:
    """Carry out the cookie action from resolve() on a response."""
    if not action:
        return
    kind, value = action
    if kind == "set":
        response.set_cookie(_cookie(mode), value, path="/", httponly=True, samesite="lax")   # a session cookie
    else:
        response.delete_cookie(_cookie(mode), path="/")


def chips(level: str, base: str, subject: str = "") -> list[dict]:
    """The chip row: Sec 1 · Sec 2 · Both, each a link back to this page with that level chosen."""
    query = f"subject={quote(subject)}&" if subject else ""
    return [{"level": lv, "label": LEVEL_LABELS[lv], "active": lv == level, "href": f"{base}?{query}lv={lv}"}
            for lv in STUDY_LEVELS]


def log_switch(conn, mode: str, level: str) -> None:
    conn.execute("INSERT INTO mode_level_log (at, mode, level) VALUES (?, ?, ?)", (utc_now(), mode, level))
    conn.commit()


def recent_switches(conn, limit: int = 100) -> list[dict]:
    """Her latest switches, newest first: local time, mode, level chosen."""
    out = []
    for r in conn.execute("SELECT at, mode, level FROM mode_level_log ORDER BY id DESC LIMIT ?", (limit,)):
        when = datetime.strptime(r["at"], STAMP).replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)
        out.append({"when": when.strftime("%a %d %b %Y, %H:%M"), "mode": MODE_LABELS.get(r["mode"], r["mode"]),
                    "level": LEVEL_LABELS.get(r["level"], r["level"])})
    return out
