"""Ingestion prompts (one per material type) and the JSON every page call returns.

The system prompt for a material type is identical for every page of every
file of that type (and subject, for exam papers), so it caches. Anything that
changes per page goes in the user message after the image.
"""

COMMON = """You are reading one scanned page of a Singapore Lower Secondary {subject} assessment book or exam paper for a home tutoring app. Return only the JSON.

Page type:
- "cover" if the page has any box or line for a student's name, class or index number, anywhere on the page, even if it also has questions. For a cover page return no questions and no answers, never copy any names, numbers or handwriting, and set cover_has_other_content to true if the page also has questions or answers.
- "content" for a page of questions.
- "answer" for an answer key, marking scheme or worked-solution page. A worked example whose solution answers questions on this page or earlier pages is "answer", never "other".
- "blank" for an empty page.
- "other" for anything else (contents, instructions, notes without questions or solutions, adverts).
page_type_reason: for "blank" or "other", a few words saying what the page is (for example "contents page", "notes on safety, no questions"); for "cover" write only "name box"; "" for "content" and "answer".

Orientation: rotation_needed is how many degrees clockwise the image must be turned so the printed text reads upright (0, 90, 180 or 270). If it is not 0, still give your best reading.

Copy printed text exactly. Ignore handwriting and any student answers written on the page.
Figures and tables: describe them in words inside context, with enough detail to answer without the picture where possible. Set figure to "needed_to_answer" when the student must draw, label, complete or measure a figure, or read something from it that words cannot replace; "described" when your description is enough; "none" when there is no figure.
printed_page_number: the page number printed on the page, or "" if none.
chapter_heading: the chapter or unit number from a heading on this page, digits only, or "" if there is none.
If anything is hard to read or you are unsure, set uncertain to true with a short reason (on the page or on the item)."""

QUESTION_RULES = """For questions, list one entry for each question part, such as 2(a) and 2(b).
- qnum: the question number. part: the part label without brackets or spaces ("a", "b", "ciii"), or "" if the question has no parts.
- context: the shared stem for the question, repeated for each of its parts, plus descriptions of any figures, tables or sources.
- question: the text of this part only.
- marks: the number in brackets after the part, such as [2] or (2); 0 if none is shown.
- command_word: the instruction word in lower case (state, name, describe, explain, suggest, compare, infer, calculate, ...).
- Multiple choice: set is_mcq to true and list the options with labels A, B, C, D.
- If the top of the page continues a question from the previous page, add it with continues_from_previous_page set to true, using that question's number and part if you can tell them."""

ANSWER_RULES = """For answers, list one entry for each question part answered.
- qnum and part: the question the answer belongs to, part without brackets or spaces ("a", "ciii"), "" if none.
- mode: "mcq" if the answer is a single option letter; "lorms" if the answer gives levels (L1, L2, ...) with mark ranges; otherwise "points".
- points mode: one mark_points entry per creditable point or step, each worth one mark. key_terms: the words printed in bold. alternatives: accepted alternatives (shown with "/", "or", "accept"). requires: the 1-based numbers of earlier points this point depends on (for example, an explanation that only counts if the choice before it was right), else empty.
- choose: N if the answer says "any N", else 0. rejects: wording the answer says to reject or not accept.
- lorms mode: each level with marks_min, marks_max, descriptor and the award rules text exactly as printed.
- mcq mode: correct_option is the letter; otherwise "".
- If the top of the page continues an answer from the previous page, add it with continues_from_previous_page set to true, using that answer's number and part if you can tell them."""

QUESTIONS = """This page is from a question section of an assessment book.
On question pages, fill questions. topic: "".
Worked examples: if this page gives the solution to a worked example or answers questions on this page or earlier pages, put each solution in answers (unit: "") and set page_type to "answer" if the page is mainly solutions. A worked example's own question goes in questions as usual.

""" + QUESTION_RULES + "\n\n" + ANSWER_RULES

ANSWER_KEY = """This page is from an answer key. Fill answers; leave questions empty.
- unit: the chapter or unit number the answer belongs to, as digits only. Use the chapter heading on this page; if this page has none, use the chapter given in the message.

""" + ANSWER_RULES

EXAM_PAPER = """This page is from an exam paper. Its answer key or marking scheme is at the end of the same file.
On question pages, fill questions. topic: the id of the best-fitting topic from the topic list below.
On answer pages, fill answers. unit: "".

""" + QUESTION_RULES + "\n\n" + ANSWER_RULES

TYPE_PROMPTS = {"questions": QUESTIONS, "answer_key": ANSWER_KEY, "exam_paper_with_key": EXAM_PAPER}


def system_blocks(material_type: str, subject: str, topic_list=None) -> list[dict]:
    """System prompt as content blocks, the last one marked for caching."""
    text = COMMON.format(subject=subject.title()) + "\n\n" + TYPE_PROMPTS[material_type]
    if material_type == "exam_paper_with_key" and topic_list:
        text += "\n\nTopic list (id: title):\n" + "\n".join(f"{t['id']}: {t['title']}" for t in topic_list)
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def page_message(page_no: int, page_count: int, last_unit: str, last_qnum: str) -> str:
    return (f"Page {page_no} of {page_count}. "
            f"Chapter or unit on the previous page: {last_unit or 'unknown'}. "
            f"Last question number on the previous page: {last_qnum or 'none'}.")


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
    "is_mcq": _B,
    "options": {"type": "array", "items": _obj({"label": _S, "text": _S})},
    "topic": _S, "uncertain": _B, "uncertain_reason": _S,
})

ANSWER_ITEM = _obj({
    "unit": _S, "qnum": _S, "part": _S, "continues_from_previous_page": _B,
    "mode": {"type": "string", "enum": ["points", "lorms", "mcq"]},
    "mark_points": {"type": "array", "items": _obj({
        "text": _S, "key_terms": _SL, "alternatives": _SL, "requires": {"type": "array", "items": _I},
    })},
    "choose": _I, "rejects": _SL,
    "lorms": {"type": "array", "items": _obj({
        "level": _I, "marks_min": _I, "marks_max": _I, "descriptor": _S, "award_rules": _S,
    })},
    "correct_option": _S, "uncertain": _B, "uncertain_reason": _S,
})

PAGE_SCHEMA = _obj({
    "page_type": {"type": "string", "enum": ["cover", "content", "answer", "blank", "other"]},
    "page_type_reason": _S,
    "rotation_needed": {"type": "integer", "enum": [0, 90, 180, 270]},
    "cover_has_other_content": _B,
    "printed_page_number": _S,
    "chapter_heading": _S,
    "questions": {"type": "array", "items": QUESTION_ITEM},
    "answers": {"type": "array", "items": ANSWER_ITEM},
    "uncertain": _B,
    "uncertain_reason": _S,
})
