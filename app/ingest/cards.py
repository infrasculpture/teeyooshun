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

FORMATS = ("bullets", "list", "table", "grouped", "timeline", "image")
RECALL = ("typed_or_paper", "self_tick_only")
MAX_POINTS = 8
NEAR_MATCH = 0.85  # prompts this similar (but not equal) to an existing card's get a warning

UNSURE_CARD = "Model unsure about the card: "
SPLIT_WARNING = "Split from a long answer: check it"

PROMPT = """This page is from a revision guide. Its revision tables have a left column with a command word, question or instruction, and a right column with the answer. List one entry in cards for each table row, in page order.
table_rows: the number of table rows on this page, counting a row that continues from the previous page.
For each row:
- section: the heading above the table, or "" if the table continues from the previous page and no heading is printed on this page.
- command_word: the command word in the left column, in lower case (define, describe, explain, compare, locate, ...), or "" if there is none.
- prompt: the left column exactly as printed.
- continues_from_previous_page: true if the row at the top of this page is the rest of a row that started on the previous page; give prompt "" if the left column is not repeated.
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


def _merge_card(last: dict, c: dict) -> None:
    """The rest of a row that started on the previous page."""
    _extend(last.setdefault("points", []), c.get("points"))
    _extend(last.setdefault("groups", []), c.get("groups"))
    _extend(last.setdefault("events", []), c.get("events"))
    _extend(last.setdefault("examples", []), c.get("examples"))
    _extend(last.setdefault("table_key_terms", []), c.get("table_key_terms"))
    table, more = last.setdefault("table", {"headers": [], "rows": []}), c.get("table") or {}
    table["headers"] = table.get("headers") or more.get("headers") or []
    _extend(table.setdefault("rows", []), more.get("rows"))
    if c.get("notes") and c["notes"] not in (last.get("notes") or ""):
        last["notes"] = f"{last.get('notes') or ''} {c['notes']}".strip()
    if not (last.get("image") or {}).get("alt") and (c.get("image") or {}).get("alt"):
        last["image"] = c["image"]
    if c.get("uncertain"):
        last["uncertain"] = True
        reason = c.get("uncertain_reason") or ""
        if reason and reason not in (last.get("uncertain_reason") or ""):
            last["uncertain_reason"] = f"{last.get('uncertain_reason') or ''} {reason}".strip()
    if c["pages"][0] not in last["pages"]:
        last["pages"].append(c["pages"][0])


def _answer_signature(c: dict) -> str:
    return json.dumps({k: c.get(k) for k in ("format", "points", "groups", "table", "events", "image")}, sort_keys=True)


def stitch_cards(pages: list) -> list[dict]:
    """pages: [(page_no, printed_page, data)] in page order. Returns one item per table row.

    A row marked as continuing, or a row with no prompt at the top of a page, joins the row
    before it. A row with no section heading takes the last heading seen. Exact duplicates
    from shared boundary pages are dropped.
    """
    items, section, seen = [], "", {}
    for page_no, printed, data in pages:
        for index, raw in enumerate(data.get("cards") or []):
            c = json.loads(json.dumps(raw))  # a deep copy: merging must not change the stored page output
            c["pages"], c["printed_page"] = [page_no], printed
            c["prompt"] = (c.get("prompt") or "").strip()
            continues = c.get("continues_from_previous_page") or (index == 0 and not c["prompt"])
            if continues and items and (not c["prompt"] or norm_prompt(c["prompt"]) == items[-1]["prompt_key"]):
                _merge_card(items[-1], c)
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
        "id": card_id, "subject": cfg.subject, "level": cfg.level, "source": "revision_guide", "topic": topic,
        "section": item.get("section", ""), "source_ref": f"{cfg.title}, p.{item.get('printed_page') or '?'}",
        "command_word": (item.get("command_word") or "").lower(), "prompt": item.get("prompt", ""),
        "answer": answer,
    }
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
    if item.get("long_unsplit"):
        w.append(f"Long answer ({item['long_unsplit']} points) could not be split: split it by hand or approve as it is")
    if item.get("uncertain"):
        w.append(f"{UNSURE_CARD}{item.get('uncertain_reason') or 'no reason given'}")
    w += [f"Check: {p}" for p in validate_card(record)
          if not (p.startswith("'prompt'") or p == "the answer is empty")]
    return w
