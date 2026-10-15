"""Ingestion prompts (one per material type) and the JSON every page call returns.

The system prompt for a material type is identical for every page of every
file of that type (and subject, for exam papers), so it caches. Anything that
changes per page goes in the user message after the image.
"""

from app.ingest import cards, notes
from app.ingest.slides import slide_label, slide_page
from app.sources import NOTES_TYPES

COMMON = """You are reading one scanned page of a Singapore Lower Secondary {subject} assessment book or exam paper for a home tutoring app. Return only the JSON.

Page type:
- "cover" if the page has any box or line for a student's name, class or index number, anywhere on the page, even if it also has questions. For a cover page return no questions and no answers, never copy any names, numbers or handwriting, and set cover_has_other_content to true if the page also has questions or answers.
- "content" for a page of questions.
- "answer" for an answer key, marking scheme or worked-solution page. A worked example whose solution answers questions on this page or earlier pages is "answer", never "other".
- "blank" for an empty page.
- "sources" for a page of background text and sources (Source A, Source B, ...: texts, pictures, cartoons, maps, graphs, tables) that questions refer to, when the page has no questions of its own to ask. A sources page may still show the number and stem of the question that introduces the sources: put that in questions as usual.
- "other" for anything else (contents, instructions, notes without questions or solutions, adverts). Never "other" for a page of background text or sources.
page_type_reason: for "blank" or "other", a few words saying what the page is (for example "contents page", "notes on safety, no questions"); for "cover" write only "name box"; "" for "content", "answer" and "sources".

Orientation: rotation_needed is how many degrees clockwise the image must be turned so the printed text reads upright (0, 90, 180 or 270). If it is not 0, still give your best reading.

Copy printed text exactly. Ignore handwriting and any student answers written on the page.
Figures and tables: describe them in words inside context, with enough detail to answer without the picture where possible. Set figure to "needed_to_answer" when the question cannot be answered properly without seeing the figure, graph, diagram or table itself (the page image is then shown to the student and to the marker); "described" when your description is enough; "none" when there is no figure.
printed_page_number: the page number printed on the page, or "" if none.
chapter_heading: the chapter or unit number from a heading on this page, digits only, or "" if there is none.
section_marks: if this page states how many marks each question in a section carries (for example an instructions page saying "Section A: 20 multiple-choice questions, 1 mark each", or a section heading giving the total marks and the number of questions), list each section letter with the marks for one question; otherwise an empty list.
If anything is hard to read or you are unsure, set uncertain to true (on the page or on the item). Whenever uncertain is true, uncertain_reason must say briefly what is unclear, for example "last line of 2(b) cut off by the binding"; never leave it empty."""

QUESTION_RULES = """For questions, list one entry for each question part, such as 2(a) and 2(b).
- qnum: the question number exactly as printed on the page, including a section letter when the paper prints one ("B5", "C12"). Never number questions by counting entries. If an entry on this page shows only a part label, such as "(c)", and its question number is printed on an earlier page, give qnum "".
- part: the part label as printed, without brackets or spaces ("a", "b", "ci" for (c)(i)), or "" if the question has no parts. If a page starts with sub-parts such as "(i)" whose letter part, such as "(b)", is on an earlier page, include the letter: "bi".
- A part that only introduces its sub-parts (for example 5(b) followed by (i) to (v)) and has no marks of its own is a stem too: its text goes into context on each sub-part, and it is not an entry.
- A stem is not an entry. Text that introduces a question (a scenario, a figure or table description, an experiment set-up) and has no marks of its own goes into context on each of that question's parts. If the question has no parts, the stem and the question form one entry.
- context: the stem for the question, repeated for each of its parts, plus descriptions of any figures, tables or sources.
- question: the text of this part only.
- marks: the number in brackets after the part, such as [2] or (2); 0 if none is shown.
- command_word: the instruction word in lower case (state, name, describe, explain, suggest, compare, infer, calculate, ...).
- Multiple choice: set is_mcq to true and list the options with labels A, B, C, D.
- answer_format: "draw_or_label" only when the student must draw, sketch, plot, shade, mark or label something on a diagram, graph or picture; "table" when the answer is filling in or completing a table (the student types the missing entries); otherwise "text".
- If the top of the page continues a question from the previous page, add it with continues_from_previous_page set to true, using that question's number and part if you can tell them.
- source_refs: the labels of the sources this question refers to ("A", "C", "D"), or ["all"] for "all the sources" or "both sources"; [] if it refers to none. A stem that refers to sources gives the same source_refs to each of its parts.

Sources: on any page that shows background text or sources (Source A, Source B, ...), list each one in sources.
- label: the printed label ("A", "B", "1"), without the word Source; "Background" for a background or introduction text with no label.
- text: the printed words of the source, copied exactly, including its caption; "" if it is only a picture.
- picture_description: a written description of any picture, photograph, cartoon, map, graph or table in the source, with enough detail to answer questions from it (who or what is shown, labels, dates, any words inside it); "" if there is none.
- has_picture: true if the source is, or contains, a picture, cartoon, map, graph or table that the page image shows better than words can.
- provenance: the line saying where the source comes from (author, title, date, publisher), exactly as printed; "" if none."""

ANSWER_RULES = """For answers, list one entry for each question part answered.
- qnum: the question number exactly as printed, including a section letter when the key prints one ("B5", "C1"); "" if this page shows only a part label and the number is on an earlier page. Never number answers by counting, and never change a number to make it agree with the question paper.
- part: the part the answer is printed for, without brackets or spaces ("a", "ci"), "" if none.
- If one answer is printed for a parent part that has sub-parts (for example (c) covering (i) to (iii)), give the parent as part ("c") and set sub_part on each mark point to the sub-part it belongs to ("i", "ii", ...) when the answer labels them; otherwise sub_part is "".
- mode: "mcq" if the answer is a single option letter; "lorms" if the answer gives levels (L1, L2, ...) with mark ranges; otherwise "points".
- points mode: one mark_points entry per creditable point or step, each worth one mark. key_terms: the words printed in bold. alternatives: accepted alternatives (shown with "/", "or", "accept"). requires: the 1-based numbers of earlier points this point depends on (for example, an explanation that only counts if the choice before it was right), else empty.
- choose: N if the answer says "any N", else 0. rejects: wording the answer says to reject or not accept.
- lorms mode: each level with marks_min, marks_max, descriptor and the award rules text exactly as printed. Also use lorms mode when the key gives only a model or example answer to an essay or source-based question and no mark-by-mark list; then leave lorms empty and put the answer in exemplars. Never split a model answer into mark_points.
- exemplars: each model, example or sample answer printed in the key, with level (the level it is said to show, 0 if not stated) and text (the whole answer, copied exactly). Empty if there is none.
- mcq mode: correct_option is the letter; otherwise "".
- If the top of the page continues an answer from the previous page, add it with continues_from_previous_page set to true, using that answer's number and part if you can tell them. A level table (L3 and the rest of its levels) or a model answer that carries on at the top of a page with no question number is such a continuation: give qnum "" and part "".
- Pages of background text and sources (Source A, B, ...) are never answer pages."""

QUESTIONS = """This page is from a question section of an assessment book.
On question pages, fill questions. topic: "".
Worked examples: label a worked example's question with qnum "WE", or "WE" plus its number if the example is numbered ("WE2" for Worked Example 2), and the part as printed. Its own question goes in questions as usual.
If this page gives the solution to a worked example, put each solution in answers with exactly the same qnum and part as the worked example's question (for example qnum "WE", part "a" for WE(a)), unit "", and set page_type to "answer" if the page is mainly solutions. These solutions are the answer source for those questions. Solutions to ordinary numbered questions keep the question's printed number.

""" + QUESTION_RULES + "\n\n" + ANSWER_RULES

ANSWER_KEY = """This page is from an answer key. Fill answers; leave questions empty.
- unit: the chapter or unit number the answer belongs to, as digits only. Use the chapter heading on this page; if this page has none, use the chapter given in the message.

""" + ANSWER_RULES

EXAM_PAPER = """This page is from an exam paper. Its answer key or marking scheme is at the end of the same file.
On question pages, fill questions. topic: the id of the best-fitting topic from the topic list below.
On sources pages, fill sources (and questions, if the page also shows a question number and stem).
On answer pages, fill answers. unit: "".

""" + QUESTION_RULES + "\n\n" + ANSWER_RULES

TYPE_PROMPTS = {"questions": QUESTIONS, "answer_key": ANSWER_KEY, "exam_paper_with_key": EXAM_PAPER,
                "revision_tables": cards.PROMPT, **{t: notes.PROMPT for t in NOTES_TYPES}}


CARDS_COMMON = """You are reading one scanned page of a Singapore Lower Secondary {subject} revision guide for a home tutoring app. Return only the JSON.

Page type:
- "cover" if the page has any box or line for a student's name, class or index number, anywhere on the page. For a cover page return no cards, never copy any names, numbers or handwriting, and set cover_has_other_content to true if the page also has revision content.
- "content" for a page with a revision table (or the rest of one), and for any other page of revision content to learn, even with no question column: a timeline of dates and events, a headed list of key points, a summary table.
- "blank" for an empty page.
- "other" only for pages with nothing to learn from: the book's contents list, title pages, instructions on using the book, adverts. Do not use "answer".
page_type_reason: for "blank" or "other", a few words saying what the page is; for "cover" write only "name box"; "" for "content".

Orientation: rotation_needed is how many degrees clockwise the image must be turned so the printed text reads upright (0, 90, 180 or 270). If it is not 0, still give your best reading.

Copy printed text exactly. Ignore handwriting, highlighting and anything a student has written on the page.
printed_page_number: the page number printed on the page, or "" if none.
If anything is hard to read or you are unsure, set uncertain to true (on the page or on the card). Whenever uncertain is true, uncertain_reason must say briefly what is unclear; never leave it empty."""


def system_blocks(material_type: str, subject: str, topic_list=None) -> list[dict]:
    """System prompt as content blocks, the last one marked for caching."""
    common = (CARDS_COMMON if material_type == "revision_tables"
              else notes.COMMON if material_type in NOTES_TYPES else COMMON)
    text = common.format(subject=subject.title()) + "\n\n" + TYPE_PROMPTS[material_type]
    if (material_type == "exam_paper_with_key" or material_type in NOTES_TYPES) and topic_list:
        text += "\n\nTopic list (id: title):\n" + "\n".join(f"{t['id']}: {t['title']}" for t in topic_list)
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def page_message(page_no: int, page_count: int, last_unit: str, last_qnum: str, last_section: str = "") -> str:
    message = (f"Page {page_no} of {page_count}. "
               f"Chapter or unit on the previous page: {last_unit or 'unknown'}. "
               f"Last question number on the previous page: {last_qnum or 'none'}.")
    if last_section:
        message += f" Table heading on the previous page: {last_section}."
    return message


def notes_message(page_no: int, page_count: int, last_section: str = "", slide: bool = False) -> str:
    """The per-page message for school notes. For a slide handout, page_no is the slide number."""
    where = (f"Slide {slide_label(page_no)} (file page {slide_page(page_no)}) of {page_count} slides."
             if slide else f"Page {page_no} of {page_count}.")
    return where + f" Section heading before this one: {last_section or 'none'}."


def schema_for(material_type: str) -> dict:
    """The JSON a page of this material type must return."""
    if material_type in NOTES_TYPES:
        return notes.PAGE_SCHEMA
    return cards.PAGE_SCHEMA if material_type == "revision_tables" else PAGE_SCHEMA


# ---- Output schema (strict: every field required, nothing extra) ----

def _obj(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


_S = {"type": "string"}
_B = {"type": "boolean"}
_I = {"type": "integer"}
_SL = {"type": "array", "items": _S}

QUESTION_ITEM = _obj({
    "qnum": _S, "part": _S, "continues_from_previous_page": _B,
    "context": _S, "question": _S, "marks": _I, "command_word": _S,
    "figure": {"type": "string", "enum": ["none", "described", "needed_to_answer"]},
    "answer_format": {"type": "string", "enum": ["text", "table", "draw_or_label"]},
    "is_mcq": _B,
    "options": {"type": "array", "items": _obj({"label": _S, "text": _S})},
    "source_refs": _SL,
    "topic": _S, "uncertain": _B, "uncertain_reason": _S,
})

SOURCE_ITEM = _obj({
    "label": _S, "text": _S, "picture_description": _S, "has_picture": _B, "provenance": _S,
})

ANSWER_ITEM = _obj({
    "unit": _S, "qnum": _S, "part": _S, "continues_from_previous_page": _B,
    "mode": {"type": "string", "enum": ["points", "lorms", "mcq"]},
    "mark_points": {"type": "array", "items": _obj({
        "text": _S, "key_terms": _SL, "alternatives": _SL, "requires": {"type": "array", "items": _I},
        "sub_part": _S,
    })},
    "choose": _I, "rejects": _SL,
    "lorms": {"type": "array", "items": _obj({
        "level": _I, "marks_min": _I, "marks_max": _I, "descriptor": _S, "award_rules": _S,
    })},
    "exemplars": {"type": "array", "items": _obj({"level": _I, "text": _S})},
    "correct_option": _S, "uncertain": _B, "uncertain_reason": _S,
})

PAGE_SCHEMA = _obj({
    "page_type": {"type": "string", "enum": ["cover", "content", "answer", "sources", "blank", "other"]},
    "page_type_reason": _S,
    "rotation_needed": {"type": "integer", "enum": [0, 90, 180, 270]},
    "cover_has_other_content": _B,
    "printed_page_number": _S,
    "chapter_heading": _S,
    "section_marks": {"type": "array", "items": _obj({"section": _S, "marks_each": _I})},
    "sources": {"type": "array", "items": SOURCE_ITEM},
    "questions": {"type": "array", "items": QUESTION_ITEM},
    "answers": {"type": "array", "items": ANSWER_ITEM},
    "uncertain": _B,
    "uncertain_reason": _S,
})
