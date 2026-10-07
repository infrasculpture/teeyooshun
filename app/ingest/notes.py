"""School notes and teaching texts -> derived revision cards and reference passages
(INGESTION_STAGE_C_SPEC.md). Plain functions, no database or API.

Cards from notes are staged through the same stitching, splitting, card record and warning code as
revision tables (cards.py). This module holds only what is different: the page prompt and schema,
the filter that drops names, and turning each page class into card items, passages and outcomes.
"""

import re

from app.ingest import cards

PAGE_CLASSES = ["cover", "contents", "learning_outcomes", "glossary", "content", "activity", "questions",
                "image_only", "blank", "other"]
READ_CLASSES = ("content", "glossary", "learning_outcomes", "image_only")  # pages whose output is used
SKIP_CLASSES = ("contents", "activity", "questions", "blank", "other")  # read, then logged as skipped
SLIDE_CARD_LIMIT = 3  # a card may draw on at most this many consecutive slides

COMMON = """You are reading one printed page, or one slide, from Singapore Lower Secondary {subject} school notes or a teaching text, for a home tutoring app. Return only the JSON.

Page type (page_type):
- "cover": any page with a box or line for a student's name, class or index number, anywhere on it. For a cover return no cards or other content, never copy any names, numbers or handwriting, and set cover_has_other_content to true if the page also has notes content.
- "contents": the contents list.
- "learning_outcomes": a page or box listing what the student should be able to do or know by the end of the chapter.
- "glossary": a list of terms, each with a printed description.
- "content": explanations, definitions, tables, processes, formulae, properties, causes and effects, labelled diagrams with text.
- "activity": activities, quick checks, exercises, things for the student to do or fill in.
- "questions": a page that is mainly questions.
- "image_only": a page or slide whose teachable content is a picture (a labelled diagram, apparatus, a table shown as an image) with little or no text.
- "blank": an empty page.
- "other": title pages, adverts, instructions on using the book, decorative pages.
page_type_reason: for "blank", "other", "contents", "activity" or "questions", a few words saying what the page is; for "cover" write only "name box"; "" otherwise.

Orientation: rotation_needed is how many degrees clockwise the image must be turned so the printed text reads upright (0, 90, 180 or 270). If it is not 0, still give your best reading.

Printed content only. Ignore all handwriting: fill-ins, margin notes, ticks, circles, highlighting, answers written on the page. Never put anything handwritten in any field. Never copy a name, class or index number, wherever it appears on the page. Ignore QR codes, URLs, pointers such as "read textbook pages ...", and decorative images.
has_blanks: true if any printed sentence on this page has a blank or gap for the student to fill in. Do not use such a sentence in any card point or passage.
printed_page_number: the page number printed on the page, or "" if none (always "" for a slide).
If anything is hard to read or you are unsure, set uncertain to true (on the page or on the card). Whenever uncertain is true, uncertain_reason must say briefly what is unclear; never leave it empty."""

PROMPT = """Fill only the fields for this page type and leave the others empty.

learning_outcomes pages: outcomes has one entry per printed outcome, text exactly as printed, with topic.
glossary pages: glossary has one entry per printed term: term as printed and description exactly as printed. Do not write cards for a glossary page.
activity and questions pages: return nothing else. Questions found in notes are never used.
content and image_only pages (and slides): write cards and passages.

Slides: if this slide is a section title with three or fewer bullets of sub-headings (for example "Element of weather: Temperature"), set is_divider to true, put the title in section, and return no cards and no passages. Otherwise is_divider is false and section is "".

Cards. You write each card from the printed content. Use the printed wording for the points and key terms; add no fact that is not on the page. Skip any sentence with a blank to fill in. Each card has:
- section: the printed heading above it, or "" if none is printed.
- prompt: a short exam-style question or instruction that the card answers, for example "Define weather." or "State the formula for speed."
- command_word: the command word an exam would use for it, in lower case (define, state, describe, explain, compare, ...).
- format and the matching answer fields, as follows. Fill only the fields for the chosen format.
  - A definition: "bullets" with one point. A set of properties, causes or effects: "bullets" (or "grouped" under sub-headings). A process or method: "list" (numbered steps). A printed table: "table", with each printed row as one row of cells (for example differences between weather and climate, solution versus suspension). A skills table such as how to describe a climograph: "table" with command word "describe". Formulae: "bullets", each formula one point, command word "state". Dated events: "timeline". A picture that carries the answer: "image".
  - points (bullets, list): one entry per point; key_terms are the words printed in bold or as key terms in that point; sub are indented points beneath it.
  - groups (grouped): each sub-heading with its points.
  - table (table): headers and rows of cells; table_key_terms are the key terms in the table.
  - events (timeline): each date with its text and key_terms.
  - image (image): alt is a written description of the picture with every printed label; box is where it sits on the page as [left, top, right, bottom] in parts per thousand of the width and height, or [] if you cannot tell.
- examples: the text of each printed example block, one entry each. notes: the text of any printed remark box, or "".
- Keep each card to one idea and to about 8 points or fewer; make a separate card for each separate idea.
- continues_from_previous_page: true only if the first card on this page or slide carries on the idea of the last card on the previous one (then prompt is "", command_word is "", and you give only the content printed here). Use it only for an idea that runs over at most three consecutive pages or slides. continues_on_next_page: true if the last card is cut off and carries on over the page.
- topic: the id of the best-fitting topic from the topic list, if one is given below, else "". topic_uncertain: true if you are not sure of it.
- image_only pages and slides: one card with format "image" if the picture carries teachable content. If it does not, return no cards and say why in page_type_reason.

Passages (content pages and slides only): passages holds the printed text of the page as plain text in reading order, a paragraph, or a table's or diagram's printed words, in each entry, with topic. Never include handwriting."""


def _obj(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


_S, _B = {"type": "string"}, {"type": "boolean"}

CARD_ITEM = _obj({**cards.CARD_ITEM["properties"], "topic": _S, "topic_uncertain": _B})

PAGE_SCHEMA = _obj({
    "page_type": {"type": "string", "enum": PAGE_CLASSES},
    "page_type_reason": _S,
    "rotation_needed": {"type": "integer", "enum": [0, 90, 180, 270]},
    "cover_has_other_content": _B,
    "printed_page_number": _S,
    "has_blanks": _B,
    "is_divider": _B,
    "section": _S,
    "cards": {"type": "array", "items": CARD_ITEM},
    "glossary": {"type": "array", "items": _obj({"term": _S, "description": _S, "topic": _S, "topic_uncertain": _B})},
    "outcomes": {"type": "array", "items": _obj({"text": _S, "topic": _S})},
    "passages": {"type": "array", "items": _obj({"text": _S, "topic": _S})},
    "uncertain": _B,
    "uncertain_reason": _S,
})


# ---- Names ----------------------------------------------------------------------------

# A line that is a name, class or index-number field: "Name: ...", "Class ____", "Index No. 12".
# "Name the process" or "Classification" are not matched.
# "Name the process", "Class 1 lever" and a table cell that just says "Class" are not matched.
_NAME_LINE = re.compile(
    r"^\W*(?:(?:(?:student\s+)?name|class)\s*(?::|_{2,})"
    r"|index\s*(?:no|number|num)\b\.?\s*(?::|_{2,}|\d|$))", re.IGNORECASE)


def drop_name_lines(text) -> str:
    """The text without any line that looks like a name, class or index-number field."""
    text = str(text or "")
    lines = text.splitlines()
    if not any(_NAME_LINE.match(line) for line in lines):
        return text
    return "\n".join(line for line in lines if not _NAME_LINE.match(line)).strip()


def _emptied(before, after) -> bool:
    if isinstance(before, str):
        return bool(before.strip()) and not after.strip()
    if isinstance(before, dict) and "text" in before:
        return bool(str(before["text"]).strip()) and not str(after["text"]).strip()
    return False


def scrub(value, keep_slots=False):
    """A copy of model output with name lines removed from every text. An entry whose text is
    emptied by that is removed, except table cells, which stay as empty cells so columns hold."""
    if isinstance(value, str):
        return drop_name_lines(value)
    if isinstance(value, list):
        out = []
        for v in value:
            cleaned = scrub(v, keep_slots)
            if not keep_slots and _emptied(v, cleaned):
                continue
            out.append(cleaned)
        return out
    if isinstance(value, dict):
        return {k: scrub(v, k in ("headers", "rows") or keep_slots) for k, v in value.items()}
    return value


def scrub_page(data: dict) -> dict:
    return scrub(data)


# ---- Page classes -> card items, passages and outcomes ------------------------------------

def _blank_item() -> dict:
    return {"section": "", "command_word": "", "prompt": "", "continues_from_previous_page": False,
            "continues_on_next_page": False, "format": "bullets", "points": [], "groups": [],
            "table": {"headers": [], "rows": []}, "table_key_terms": [], "events": [],
            "image": {"alt": "", "box": []}, "examples": [], "notes": "", "uncertain": False,
            "uncertain_reason": "", "topic": "", "topic_uncertain": False}


def glossary_card(entry: dict) -> dict:
    """One Define card for a printed glossary term: the printed description is the one point."""
    card = _blank_item()
    term = (entry.get("term") or "").strip()
    card.update(section="Glossary", command_word="define", prompt=f"Define {term}.",
                points=[{"text": entry["description"].strip(), "key_terms": [term], "sub": []}],
                topic=entry.get("topic") or "", topic_uncertain=bool(entry.get("topic_uncertain")))
    return card


def notes_pages(pages: list, origin: str, slide_file: bool = False) -> list:
    """[(page_no, printed_page, data)] as read -> the same list with only `cards`, ready for
    cards.stitch_cards. Each card is marked derived, with its origin, the page's has_blanks, and
    (for a slide handout) its slide number. A divider slide sets the section of the cards after it.
    """
    out, section = [], ""
    for page_no, printed, raw in pages:
        data = scrub_page(raw)
        items, blanks = [], bool(data.get("has_blanks"))
        kind = data.get("page_type")
        if data.get("is_divider"):
            section = (data.get("section") or "").strip() or section
        elif kind == "glossary":
            for entry in data.get("glossary") or []:
                if (entry.get("term") or "").strip() and (entry.get("description") or "").strip():
                    items.append(glossary_card(entry))
                else:
                    blanks = True  # a term with no printed description: nothing to make a card from
        elif kind in ("content", "image_only"):
            items = list(data.get("cards") or [])
        staged = []
        for raw_card in items:
            c = dict(raw_card, derived=True, origin=origin, has_blanks=blanks)
            if slide_file:
                c["slide"] = page_no
            if not (c.get("section") or "").strip():
                c["section"] = section
            staged.append(c)
        out.append((page_no, printed, {"cards": staged}))
    return out


def page_extras(pages: list):
    """(outcomes, passages) from read pages: lists of {page_no, printed, text, topic, topic_uncertain}.
    Passages come from content pages and slides only (not dividers)."""
    outcomes, passages = [], []
    for page_no, printed, raw in pages:
        data = scrub_page(raw)
        kind = data.get("page_type")
        if kind == "learning_outcomes":
            outcomes += [dict(e, page_no=page_no, printed=printed) for e in data.get("outcomes") or []
                         if (e.get("text") or "").strip()]
        elif kind == "content" and not data.get("is_divider"):
            passages += [dict(e, page_no=page_no, printed=printed) for e in data.get("passages") or []
                         if (e.get("text") or "").strip()]
    return outcomes, passages
