"""Page images of questions: where they live and which may be shown to the student.

A question record lists its pages under "page_images" ([{"page", "display", "model"}],
paths relative to the data directory). Only pages classed as question pages are ever
listed or served; answer pages and worked-example pages are never shown to the student.
"""

import json
import re
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


def _from_staging(conn, question_id: str) -> list:
    """Page images for an ingested question whose own record lists none (it was approved before page images
    existed, and approved items are never rebuilt). Uses the source page references kept on its staged
    items: every page holding any part of the same question number, question pages only."""
    item = conn.execute("SELECT file_id, qnum FROM staged_items WHERE kind = 'question' AND item_id = ? "
                        "AND status != 'superseded' ORDER BY status = 'approved' DESC, id DESC LIMIT 1",
                        (question_id,)).fetchone()
    if item is None or not item["qnum"]:
        return []
    refs = set()
    for r in conn.execute("SELECT page_refs_json FROM staged_items WHERE kind = 'question' AND file_id = ? "
                          "AND qnum = ? AND status != 'superseded'", (item["file_id"], item["qnum"])):
        refs.update(p for p in json.loads(r["page_refs_json"]) if isinstance(p, int))
    from app.ingest.staging import showable_pages  # not at the top: the ingest package is heavy
    shown = showable_pages(conn, item["file_id"])
    return [shown[p] for p in sorted(refs) if p in shown]


def _candidates(conn, question: dict) -> list:
    if question.get("worked_example"):
        return []
    own = question_images(question)
    if own:
        return own
    if question.get("origin_card_id"):
        from app.revision_cards import get_card
        card = get_card(conn, question["origin_card_id"])
        return question_images(card) if card else []
    if question.get("source") == "generated":
        return []
    return _from_staging(conn, question["id"])


def _file_for(conn, image: dict):
    """The file for one page image, or None unless it is a real file for a page classed as a question
    page with no solutions on it, whatever the record says."""
    rel = image["display"]
    row = conn.execute("SELECT page_type, raw_json FROM pages WHERE display_path = ? OR render_path = ?",
                       (rel, rel)).fetchone()
    if row is None or row["page_type"] not in ("content", "image_only", "sources"):
        return None
    if row["raw_json"] and json.loads(row["raw_json"]).get("answers"):
        return None
    return resolve_render(rel)


def shown_images(conn, question: dict) -> list:
    """The page images to show with a question, whatever its figure flag. Question pages only, and only
    those that can really be served (so there is never a broken link).

    Its own record's pages; for a generated question, the page of the card it was made from; for an
    ingested question with none on record, the pages found from its source page references.
    Empty when there is no image to show (the page then shows nothing at all).
    """
    return [i for i in _candidates(conn, question) if _file_for(conn, i) is not None]


FIGURE_WORDS = re.compile(r"\b(figures?|fig|photos?|photographs?|diagrams?|maps?|graphs?|tables?|sources?)\b", re.IGNORECASE)


def mentions_figure(question: dict) -> bool:
    """True if the question or its context refers to a figure, photo, diagram, map, graph, table or source."""
    return bool(FIGURE_WORDS.search(f"{question.get('question', '')} {question.get('context', '')}"))


def student_image(conn, question: dict, index: int):
    """The file for the question's page image number `index` (the same numbering as shown_images), or None.

    Checked again at serve time: the path must belong to a page classed as a
    question page with no solutions on it, whatever the record says.
    """
    images = shown_images(conn, question)
    return _file_for(conn, images[index]) if 0 <= index < len(images) else None
