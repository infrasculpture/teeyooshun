"""Page images of questions: where they live and which may be shown to the student.

A question record lists its pages under "page_images" ([{"page", "display", "model"}],
paths relative to the data directory). Only pages classed as question pages are ever
listed or served; answer pages and worked-example pages are never shown to the student.
"""

import json
from pathlib import Path

from app.config import get_settings

MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def resolve_render(rel_path):
    """The file for a stored render path, or None unless it is a real file inside data/renders."""
    if not rel_path:
        return None
    data_dir = Path(get_settings().data_dir)
    renders = (data_dir / "renders").resolve()
    path = (data_dir / rel_path).resolve()
    if not path.is_relative_to(renders) or not path.is_file():
        return None
    return path


def media_type(path) -> str:
    return MEDIA_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")


def question_images(question: dict) -> list:
    """The question's page images that may be shown. None for worked examples."""
    if question.get("worked_example"):
        return []
    return [i for i in question.get("page_images") or [] if isinstance(i, dict) and i.get("display")]


def student_image(conn, question: dict, index: int):
    """The file for the question's page image number `index`, or None.

    Checked again at serve time: the path must belong to a page classed as a
    question page with no solutions on it, whatever the record says.
    """
    images = question_images(question)
    if not 0 <= index < len(images):
        return None
    rel = images[index]["display"]
    row = conn.execute("SELECT page_type, raw_json FROM pages WHERE display_path = ? OR render_path = ?",
                       (rel, rel)).fetchone()
    if row is None or row["page_type"] not in ("content", "image_only"):
        return None
    if row["raw_json"] and json.loads(row["raw_json"]).get("answers"):
        return None
    return resolve_render(rel)
