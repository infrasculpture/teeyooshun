"""Revision tables -> revision cards (INGESTION_SPEC.md section 5; docs/card_format_example.json).

One card per table row. Plain functions, no database or API:
- stitching: a row that continues onto the next page joins the card before it;
- splitting: an answer of more than MAX_POINTS points is split along its own structure
  (groups, or top-level points with sub-points); a long flat list is kept whole with a warning;
- the card record, its checks and its review warnings;
- stable IDs: <subject>-<level>-<slug>-<file>-p<page>-<hash8 of the normalised prompt>.
"""

import difflib
import hashlib
import json
import re

from app.ingest.slides import slide_label, slide_page

FORMATS = ("bullets", "list", "table", "grouped", "timeline", "image")
RECALL = ("typed_or_paper", "self_tick_only")
MAX_POINTS = 8
NEAR_MATCH = 0.85  # prompts this similar (but not equal) to an existing card's get a warning

UNSURE_CARD = "Model unsure about the card: "
SPLIT_WARNING = "Split from a long answer: check it"
# Cards derived from school notes (INGESTION_STAGE_C_SPEC section 7)
HAS_BLANKS = "Has blanks: the page has printed sentences with a gap to fill in, so some content may be missing"
TOPIC_UNCERTAIN = "Topic is uncertain: the model was not sure the topic is right"
DUP_PROMPT = 0.8  # prompts this similar mark a likely duplicate of an approved card
DUP_POINTS = 0.6  # so do answers whose points overlap this much (shared points / all points)


def possible_duplicate_warning(card_id: str) -> str:
    return f"Possible duplicate of approved card {card_id}: check it against that card"


def changed_card_warning(version: int) -> str:
    return (f"Changed since approval: this card is already approved (version {version}). "
            f"Approving makes version {version + 1}; rejecting keeps version {version}")


def content_key(record: dict) -> str:
    """The card's own content as text, for telling whether a re-read changed it. Warnings, sources,
    page images, the topic and other metadata are left out, as is the picture's file path."""
    answer = dict(record.get("answer") or {})
    if isinstance(answer.get("image"), dict):
        answer["image"] = {k: v for k, v in answer["image"].items() if k != "page_render"}
    content = {"prompt": (record.get("prompt") or "").strip(), "command_word": record.get("command_word") or "",
               "answer": answer, "examples": record.get("examples") or [], "notes": record.get("notes") or ""}
    return json.dumps(content, sort_keys=True, ensure_ascii=False)

PROMPT = """This page is from a revision guide. Its revision tables have a left column with a command word, question or instruction, and a right column with the answer. List one entry in cards for each table row, in page order.
A page of revision content with no question column is still revision content, never "other": for example a page headed "Timeline of important events" that lists dates and events, or a headed list of key points. Return it as one card: prompt is the page or table heading exactly as printed, section is the same heading, command_word is "", and format is "timeline" for date and event pairs, otherwise "list" or "bullets". Count it as one table row.
table_rows: the number of table rows on this page, counting a row that continues from the previous page.
For each row:
- section: the heading above the table, or "" if the table continues from the previous page and no heading is printed on this page.
- command_word: the command word in the left column, in lower case (define, describe, explain, compare, locate, ...), or "" if there is none.
- prompt: the left column exactly as printed.
- continues_from_previous_page: true only if the first entry on this page is the rest of the last row on the previous page: its left column is empty or not printed again on this page (an answer table, timeline or list that carries on overleaf). Then give prompt "" and command_word "", and give only the content printed on this page; for a table, leave headers empty unless the header row is printed again. A new row that starts at the top of the page, with its own left column, is false, even when the revision table as a whole runs on from the previous page. Never write a placeholder such as "(blank)" or "(continued)" as a prompt.
- continues_on_next_page: true if the last row on this page is cut off at the bottom and carries on overleaf; false if it is complete.
- format: "bullets" (bulleted points), "list" (numbered or ordered steps), "table" (the answer is itself a table), "grouped" (points under sub-headings), "timeline" (dated events in order), "image" (a map, diagram or picture is the answer).
- Fill only the fields for the chosen format and leave the others empty:
  - points (bullets, list): one entry per point. key_terms: the words printed in bold in that point. sub: the indented points beneath it, each with its own key_terms.
  - groups (grouped): each sub-heading with its points.
  - table (table): headers and rows of cells; table_key_terms: the words printed in bold anywhere in the table.
  - events (timeline): each date with its text and key_terms.
  - image (image): alt is a written description of what the map, diagram or picture shows, with any labels. box is where it sits on the page as [left, top, right, bottom] in parts per thousand of the page width and height, or [] if you cannot tell.
- examples: the text of each "For example" block in the row, one entry each.
- notes: the text of any "Remark" box in the row, or ""."""


def _obj(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


_S, _B, _I = {"type": "string"}, {"type": "boolean"}, {"type": "integer"}
_SL = {"type": "array", "items": _S}
_SUB = _obj({"text": _S, "key_terms": _SL})
_POINT = _obj({"text": _S, "key_terms": _SL, "sub": {"type": "array", "items": _SUB}})

CARD_ITEM = _obj({
    "section": _S, "command_word": _S, "prompt": _S, "continues_from_previous_page": _B,
    "continues_on_next_page": _B,
    "format": {"type": "string", "enum": list(FORMATS)},
    "points": {"type": "array", "items": _POINT},
    "groups": {"type": "array", "items": _obj({"heading": _S, "points": {"type": "array", "items": _POINT}})},
    "table": _obj({"headers": _SL, "rows": {"type": "array", "items": _SL}}),
    "table_key_terms": _SL,
    "events": {"type": "array", "items": _obj({"date": _S, "text": _S, "key_terms": _SL})},
    "image": _obj({"alt": _S, "box": {"type": "array", "items": _I}}),
    "examples": _SL, "notes": _S, "uncertain": _B, "uncertain_reason": _S,
})

PAGE_SCHEMA = _obj({
    "page_type": {"type": "string", "enum": ["cover", "content", "answer", "blank", "other"]},
    "page_type_reason": _S,
    "rotation_needed": {"type": "integer", "enum": [0, 90, 180, 270]},
    "cover_has_other_content": _B,
    "printed_page_number": _S,
    "table_rows": _I,
    "cards": {"type": "array", "items": CARD_ITEM},
    "uncertain": _B,
    "uncertain_reason": _S,
})


# ---- Text helpers ----------------------------------------------------------------

def norm_prompt(text) -> str:
    """Lowercase, punctuation and extra spaces removed: the key a card is recognised by."""
    return " ".join(re.sub(r"[^0-9a-z]+", " ", str(text or "").lower()).split())


def prompt_hash(prompt_key: str) -> str:
    return hashlib.sha256(prompt_key.encode("utf-8")).hexdigest()[:8]


def make_card_id(cfg, file_stem: str, page: int, prompt_key: str) -> str:
    raw = f"{cfg.subject}-{cfg.level}-{cfg.slug}-{file_stem}".lower()
    return re.sub(r"[^a-z0-9_\-]", "-", raw) + f"-p{page}-{prompt_hash(prompt_key)}"


def near_match(prompt_key: str, existing_keys):
    """The existing prompt key closest to this one if it is close but not equal, else None."""
    best, best_ratio = None, NEAR_MATCH
    for key in existing_keys:
        if key and key != prompt_key:
            ratio = difflib.SequenceMatcher(None, prompt_key, key).ratio()
            if ratio >= best_ratio:
                best, best_ratio = key, ratio
    return best


# ---- Stitching ---------------------------------------------------------------------

def _extend(target: list, extra: list) -> None:
    target.extend(x for x in extra or [] if x not in target)


def _plain(text) -> str:
    return " ".join(str(text or "").lower().split())


def _has_content(c: dict) -> bool:
    return bool(c.get("points") or c.get("groups") or c.get("events")
                or (c.get("table") or {}).get("rows") or (c.get("image") or {}).get("alt"))


# A left column that is not a real prompt: it only says the row carries on, or it is a
# placeholder the model wrote for an empty cell. Treated as no prompt.
_NOT_A_PROMPT = re.compile(
    r"^[\W_]*("
    r"((table|row|answer)\s+)?(continued|cont'?d?|con't|continuation)(\s+from\s+(the\s+)?(previous|last)\s+page)?"
    r"|blank|empty|none|nil|n\s*/?\s*a|null"
    r"|no\s+(prompt|question|heading|text|left\s+column)"
    r"|not\s+(given|shown|printed|repeated|applicable)"
    r"|(left\s+column\s+)?(is\s+)?(blank|empty)"
    r")[\W_]*$", re.IGNORECASE)


def clean_prompt(prompt) -> str:
    text = str(prompt or "").strip()
    return "" if (not norm_prompt(text) or _NOT_A_PROMPT.match(text)) else text


def page_ref(page_no, printed=None) -> str:
    """'file page 19 (printed p.18)': the page counted from 1 in the PDF, as used in card IDs,
    with the book's own printed page number where it is known."""
    return f"file page {page_no}" + (f" (printed p.{printed})" if printed else "")


# A doubt that a join settles: about the row being a continuation, or about its prompt being blank.
_PROMPT_WORDS = r"(prompt|left[\s-]*(hand\s+)?column|heading|command\s+word|question\s+column)"
_JOIN_DOUBT = re.compile(
    r"continu|carr(y|ies|ied|ying)\s+(on|over)|(previous|preceding|prior|next|following)\s+page|overleaf"
    r"|spans?\s+(two|2|multiple|several)\s+pages|(split|runs?|running)\s+(across|over)\s+(two\s+)?pages"
    # "prompt is blank", "left column not repeated", "no prompt", "missing heading" ...
    rf"|{_PROMPT_WORDS}[^.;]*\b(blank|empty|missing|absent|not\s+(given|shown|printed|repeated))\b"
    rf"|\b(blank|empty|missing|no)\s+{_PROMPT_WORDS}", re.IGNORECASE)


def other_doubts(reason) -> str:
    """The parts of a doubt that are not about being a continuation or having a blank prompt.

    The reason is split into sentences and clauses; those a join settles are dropped. A doubt
    about the content itself ("bottom row is faint", "a cell is missing") is kept.
    """
    parts = [p.strip() for p in re.split(r"\s*;\s*|(?<=[.!?])\s+", str(reason or "")) if p.strip()]
    return " ".join(p for p in parts if not _JOIN_DOUBT.search(p)).strip()


def _columns(c: dict) -> int:
    table = c.get("table") or {}
    return len(table.get("headers") or []) or len((table.get("rows") or [[]])[0])


def join_decision(last, c: dict, first_on_page: bool):
    """(merge, reason, note) for card c coming after card `last`.

    For the first card on a page (the previous card is from an earlier page):
    1. no prompt -> it is the rest of the previous card. It stays separate only if the model
       says both that the previous card was complete and that this is not a continuation;
    2. the model marked it as continuing from the previous page -> joined, whatever its left
       column says;
    3. a table with no command word and the same columns as the table before it -> joined.
    A note is returned when a non-empty left column is dropped by the join, for a review warning.
    """
    if last is None:
        return False, "first card in the file", ""
    flagged = c.get("continues_from_previous_page")
    same_prompt = bool(c["prompt"]) and norm_prompt(c["prompt"]) == last["prompt_key"]
    dropped = "" if (not c["prompt"] or same_prompt) else c["prompt"]
    if not first_on_page:
        if flagged and (not c["prompt"] or same_prompt):
            return True, "marked as continuing, with no prompt or the same prompt", ""
        return False, "not the first card on its page", ""
    says_complete_and_new = last.get("continues_on_next_page") is False and flagged is False
    if not c["prompt"]:
        if says_complete_and_new:
            return False, "no prompt, but the model says the previous card was complete and this is not a continuation", ""
        return True, "no prompt at the top of the page", ""
    if flagged:
        return True, "the model marked it as continuing from the previous page", dropped
    if (c.get("format") == "table" == last.get("format") and not (c.get("command_word") or "").strip()
            and _columns(c) and _columns(c) == _columns(last) and not says_complete_and_new):
        return True, "a table with no command word and the same columns as the table before it", dropped
    return False, "it has its own prompt and is not marked as continuing", ""


def _merge_card(last: dict, c: dict) -> None:
    """The rest of a row that started on the previous page."""
    _extend(last.setdefault("points", []), c.get("points"))
    _extend(last.setdefault("groups", []), c.get("groups"))
    _extend(last.setdefault("events", []), c.get("events"))
    _extend(last.setdefault("examples", []), c.get("examples"))
    _extend(last.setdefault("table_key_terms", []), c.get("table_key_terms"))
    # Table rows go into the same table.
    table, more = last.setdefault("table", {"headers": [], "rows": []}), c.get("table") or {}
    more_headers, more_rows = more.get("headers") or [], list(more.get("rows") or [])
    if not table.get("headers"):
        table["headers"] = more_headers
    elif more_headers and [_plain(h) for h in more_headers] != [_plain(h) for h in table["headers"]]:
        # No header row is printed on the continuation page, so what came back as
        # "headers" is really the first data row: keep it as a row.
        more_rows.insert(0, more_headers)
    _extend(table.setdefault("rows", []), more_rows)
    for flag in ("has_blanks", "topic_uncertain"):  # notes cards: a doubt about either part stands
        if c.get(flag):
            last[flag] = True
    if c.get("format") and last.get("format") and c["format"] != last["format"] and _has_content(c):
        last["joined_other_format"] = c["format"]
    last["continues_on_next_page"] = c.get("continues_on_next_page")
    if c.get("notes") and c["notes"] not in (last.get("notes") or ""):
        last["notes"] = f"{last.get('notes') or ''} {c['notes']}".strip()
    if not (last.get("image") or {}).get("alt") and (c.get("image") or {}).get("alt"):
        last["image"] = c["image"]
    # Doubts: the join itself settles "is this a continuation?" and "the prompt is blank", for both
    # parts. Any other doubt about the merged part is kept, marked with the page it came from.
    if last.get("uncertain") and last.get("uncertain_reason"):
        kept = other_doubts(last["uncertain_reason"])
        last["uncertain"], last["uncertain_reason"] = bool(kept), kept
    if c.get("uncertain"):
        kept = other_doubts(c.get("uncertain_reason") or "no reason given")
        if kept:
            note = f"{kept} (merged from {page_ref(c['pages'][0], c.get('printed_page'))})"
            last["uncertain"] = True
            if note not in (last.get("uncertain_reason") or ""):
                last["uncertain_reason"] = f"{last.get('uncertain_reason') or ''} {note}".strip()
    if c["pages"][0] not in last["pages"]:
        last["pages"].append(c["pages"][0])
        last.setdefault("printed_pages", {})[str(c["pages"][0])] = c.get("printed_page") or ""


def _answer_signature(c: dict) -> str:
    return json.dumps({k: c.get(k) for k in ("format", "points", "groups", "table", "events", "image")}, sort_keys=True)


def stitch_cards(pages: list, log=None, max_pages=None) -> list[dict]:
    """pages: [(page_no, printed_page, data)] in page order. Returns one item per table row.

    The first card on each page is joined to the last card of the previous page when it is a
    continuation (see join_decision): its points, groups and events are added, and its table
    rows go into the same table. A row with no section heading takes the last heading seen.
    Exact duplicates from shared boundary pages are dropped.

    log: a list to receive one entry per page's first card, saying what was stored for it and
    why it was or wasn't joined (shown in the summary as "Page joins").
    max_pages: a card never joins more than this many pages (slide handouts: three slides).
    """
    items, section, seen = [], "", {}
    for page_no, printed, data in pages:
        for index, raw in enumerate(data.get("cards") or []):
            c = json.loads(json.dumps(raw))  # a deep copy: merging must not change the stored page output
            c["pages"], c["printed_page"] = [page_no], printed
            as_read = str(c.get("prompt") or "")
            c["prompt"] = clean_prompt(as_read)
            last = items[-1] if items else None
            first_on_page = index == 0 and last is not None and page_no not in last["pages"]
            merge, reason, dropped = join_decision(last, c, first_on_page)
            if merge and max_pages and len(last["pages"]) >= max_pages:
                merge, reason, dropped = False, f"the card already spans {max_pages} pages", ""
            if log is not None and index == 0 and last is not None:
                log.append({
                    "page": page_no, "printed": printed, "prompt_as_read": as_read, "command_word": c.get("command_word") or "",
                    "format": c.get("format"), "marked_continuing": c.get("continues_from_previous_page"),
                    "previous_cut_off": last.get("continues_on_next_page"), "previous_prompt": last.get("prompt", ""),
                    "previous_format": last.get("format"), "joined": merge, "reason": reason,
                })
            if merge:
                _merge_card(last, c)
                if dropped:
                    last.setdefault("joined_prompts", []).append([dropped, page_ref(page_no, printed)])
                continue
            c["section"] = (c.get("section") or "").strip() or section
            section = c["section"]
            c["prompt_key"] = norm_prompt(c["prompt"])
            key = (norm_prompt(c["section"]), c["prompt_key"], _answer_signature(c))
            if c["prompt_key"] and key in seen:  # the same row on a shared boundary page
                if page_no not in seen[key]["pages"]:
                    seen[key]["pages"].append(page_no)
                continue
            seen[key] = c
            items.append(c)
    return items


# ---- Splitting ---------------------------------------------------------------------

def _count(points: list) -> int:
    return sum(1 + len(p.get("sub") or []) for p in points or [])


def count_points(c: dict) -> int:
    """Lines of answer for a bulleted, listed or grouped card (0 for other formats)."""
    if c.get("format") in ("bullets", "list"):
        return _count(c.get("points"))
    if c.get("format") == "grouped":
        return sum(_count(g.get("points")) for g in c.get("groups") or [])
    return 0


def split_long(c: dict) -> list[dict]:
    """[c], or its sub-cards when the answer is long and has structure of its own to split on.

    Grouped answers split by group; bulleted answers split by top-level point when points have
    sub-points. A long flat list is never cut at an arbitrary place: it is kept whole and marked.
    """
    n = count_points(c)
    if n <= MAX_POINTS:
        return [c]
    if c["format"] == "grouped" and len(c.get("groups") or []) > 1:
        pieces = [(g.get("heading") or "", g.get("points") or []) for g in c["groups"]]
    elif c["format"] in ("bullets", "list") and len(c.get("points") or []) > 1 \
            and any(p.get("sub") for p in c["points"]):
        pieces = [("", [p]) for p in c["points"]]
    else:
        return [dict(c, long_unsplit=n)]
    subs = []
    for i, (heading, points) in enumerate(pieces):
        label = heading or f"part {i + 1} of {len(pieces)}"
        subs.append(dict(c, format="bullets" if c["format"] == "grouped" else c["format"],
                         prompt=f"{c['prompt']} ({label})", points=points, groups=[],
                         split_from=c["prompt"], split_letter=chr(ord("a") + i) if i < 26 else f"z{i}"))
    return subs


# ---- Records ---------------------------------------------------------------------

def _points(points: list) -> list:
    out = []
    for p in points or []:
        point = {"text": p.get("text", ""), "key_terms": p.get("key_terms") or []}
        sub = [{"text": s.get("text", ""), "key_terms": s.get("key_terms") or []} for s in p.get("sub") or []]
        if sub:
            point["sub"] = sub
        out.append(point)
    return out


def source_ref(cfg, printed, slide=None) -> str:
    """'Title, p.18' for a page; 'Title, p.2, slide s05' for a slide (file page, then slide number)."""
    if slide:
        return f"{cfg.title}, p.{slide_page(slide)}, slide {slide_label(slide)}"
    return f"{cfg.title}, p.{printed or '?'}"


def build_card(item: dict, *, cfg, card_id: str, topic: str, images=None, known_topics=None):
    """(record, warnings) for one card item, in the shape of docs/card_format_example.json.

    images: [{"page", "display", "model"}] for the card's pages (shown on the review page, and
    the picture itself for image cards).
    """
    fmt = item.get("format") if item.get("format") in FORMATS else "bullets"
    if fmt in ("bullets", "list"):
        answer = {"format": fmt, "points": _points(item.get("points"))}
    elif fmt == "grouped":
        answer = {"format": fmt, "groups": [{"heading": g.get("heading", ""), "points": _points(g.get("points"))}
                                            for g in item.get("groups") or []]}
    elif fmt == "table":
        table = item.get("table") or {}
        answer = {"format": fmt, "table": {"headers": table.get("headers") or [], "rows": table.get("rows") or []},
                  "key_terms": item.get("table_key_terms") or []}
    elif fmt == "timeline":
        answer = {"format": fmt, "events": [{"date": e.get("date", ""), "text": e.get("text", ""),
                                             "key_terms": e.get("key_terms") or []} for e in item.get("events") or []]}
    else:
        image = item.get("image") or {}
        picture = {}
        if images:
            picture["page_render"] = images[0]["display"]
        box = image.get("box") or []
        if len(box) == 4 and all(isinstance(v, int) and 0 <= v <= 1000 for v in box):
            picture["bbox"] = box  # parts per thousand of the page width and height
        answer = {"format": fmt, "image": picture, "alt": image.get("alt", "")}

    record = {
        "id": card_id, "subject": cfg.subject, "level": cfg.level, "source": item.get("origin") or "revision_guide",
        "topic": topic, "section": item.get("section", ""),
        "source_ref": source_ref(cfg, item.get("printed_page"), item.get("slide")),
        "command_word": (item.get("command_word") or "").lower(), "prompt": item.get("prompt", ""),
        "answer": answer,
    }
    if item.get("derived"):  # written from printed notes, not copied from a revision table
        record["derived"] = True
        record["origin"] = item.get("origin", "")
    if item.get("examples"):
        record["examples"] = item["examples"]
    if item.get("notes"):
        record["notes"] = item["notes"]
    record["recall"] = "self_tick_only" if fmt == "image" else "typed_or_paper"
    if item.get("split_from"):
        record["split_from"] = item["split_from"]
    if images:
        record["page_images"] = images
    return record, card_warnings(record, item, known_topics or set())


def answer_size(record: dict) -> int:
    """How many answer items the card has (points, rows, events, or 1 for a described image)."""
    answer = record.get("answer") or {}
    fmt = answer.get("format")
    if fmt in ("bullets", "list"):
        return len(answer.get("points") or [])
    if fmt == "grouped":
        return sum(len(g.get("points") or []) for g in answer.get("groups") or [])
    if fmt == "table":
        return len((answer.get("table") or {}).get("rows") or [])
    if fmt == "timeline":
        return len(answer.get("events") or [])
    if fmt == "image":
        return 1 if (answer.get("alt") or (answer.get("image") or {}).get("page_render")) else 0
    return 0


def point_texts(record: dict) -> set:
    """The normalised text of each point, row or event in a card's answer."""
    answer = record.get("answer") or {}
    texts = []

    def add_points(points):
        for p in points or []:
            texts.append(p.get("text", ""))
            texts.extend(s.get("text", "") for s in p.get("sub") or [])

    add_points(answer.get("points"))
    for g in answer.get("groups") or []:
        add_points(g.get("points"))
    texts += [" ".join(map(str, row)) for row in (answer.get("table") or {}).get("rows") or []]
    texts += [f"{e.get('date', '')} {e.get('text', '')}" for e in answer.get("events") or []]
    return {t for t in (norm_prompt(x) for x in texts) if t}


def find_duplicate(record: dict, approved) -> str:
    """The id of the approved card that closely overlaps this one, or "" (simple text similarity, no API).

    approved: [(card id, card record)] in the same topic. A card overlaps when its prompt is very
    similar, or when most of its points are the same as the other card's. The closest match wins.
    """
    key, points = norm_prompt(record.get("prompt")), point_texts(record)
    best, best_score = "", 0.0
    for card_id, other in approved:
        if card_id == record.get("id"):
            continue
        score = 0.0
        other_key = norm_prompt(other.get("prompt"))
        if key and other_key:
            ratio = difflib.SequenceMatcher(None, key, other_key).ratio()
            score = ratio if ratio >= DUP_PROMPT else 0.0
        other_points = point_texts(other)
        if points and other_points:
            overlap = len(points & other_points) / len(points | other_points)
            if overlap >= DUP_POINTS:
                score = max(score, overlap)
        if score > best_score:
            best, best_score = card_id, score
    return best


def validate_card(record) -> list[str]:
    """Problems that stop a card being approved (empty if it's fine)."""
    if not isinstance(record, dict):
        return ["the card is not a JSON object"]
    problems = []
    for key in ("id", "subject", "level", "topic", "prompt"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            problems.append(f"'{key}' must be a non-empty string")
    answer = record.get("answer")
    if not isinstance(answer, dict) or answer.get("format") not in FORMATS:
        problems.append(f"'answer.format' must be one of {list(FORMATS)}")
    elif answer_size(record) == 0:
        problems.append("the answer is empty")
    if record.get("recall") not in RECALL:
        problems.append(f"'recall' must be one of {list(RECALL)}")
    if "examples" in record and not (isinstance(record["examples"], list)
                                     and all(isinstance(e, str) for e in record["examples"])):
        problems.append("'examples' must be a list of strings")
    if "notes" in record and not isinstance(record["notes"], str):
        problems.append("'notes' must be a string")
    return problems


def card_warnings(record: dict, item: dict, known_topics: set) -> list[str]:
    w = []
    if not (record.get("prompt") or "").strip():
        w.append("Card has no prompt")
    if answer_size(record) == 0:
        w.append("Card has no answer point")
    topic = record.get("topic")
    if topic == "unassigned":
        w.append("Topic is unassigned")
    elif known_topics and topic not in known_topics:
        w.append(f"Topic '{topic}' is not in topics.yaml")
    if (record.get("answer") or {}).get("format") == "image" and not (record["answer"].get("image") or {}).get("page_render"):
        w.append("Image card has no page image")
    if item.get("split_from"):
        w.append(SPLIT_WARNING)
    for dropped, where in item.get("joined_prompts") or []:
        w.append(f"Joined with a row from {where} whose left column read '{dropped}': "
                 f"check it belongs to this card")
    if item.get("joined_other_format"):
        w.append(f"Joined with a continuation the model read as '{item['joined_other_format']}': "
                 f"check nothing from the next page is missing")
    if item.get("long_unsplit"):
        w.append(f"Long answer ({item['long_unsplit']} points) could not be split: split it by hand or approve as it is")
    if item.get("uncertain"):
        w.append(f"{UNSURE_CARD}{item.get('uncertain_reason') or 'no reason given'}")
    if item.get("has_blanks"):
        w.append(HAS_BLANKS)
    if item.get("topic_uncertain"):
        w.append(TOPIC_UNCERTAIN)
    if record.get("possible_duplicate"):
        w.append(possible_duplicate_warning(record["possible_duplicate"]))
    w += [f"Check: {p}" for p in validate_card(record)
          if not (p.startswith("'prompt'") or p == "the answer is empty")]
    return w
