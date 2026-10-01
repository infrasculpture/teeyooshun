"""Turn per-page model output into question items and key entries.

Plain functions, no database or API, so they can be tested with made-up data:
- stitching: a fragment marked continues_from_previous_page joins the last item;
- duplicates from shared boundary pages are dropped;
- IDs, source_ref and the question record (MARKING_SPEC / calibration format);
- review warnings (INGESTION_SPEC section 6).
"""

import json
import re

from app.importer import validate_question

UNASSIGNED = "unassigned"
FIGURE_RANK = {"none": 0, "described": 1, "needed_to_answer": 2}


def normalise_qnum(qnum) -> str:
    return re.sub(r"[^0-9a-z]", "", str(qnum or "").lower().lstrip("q"))


def normalise_part(part) -> str:
    return re.sub(r"[^0-9a-z]", "", str(part or "").lower())


def normalise_unit(unit) -> str:
    digits = re.findall(r"\d+", str(unit or ""))
    return f"{int(digits[0]):02d}" if digits else ""


def _norm_text(text) -> str:
    return " ".join(str(text or "").lower().split())


def _join_text(a: str, b: str) -> str:
    if not b or _norm_text(b) in _norm_text(a):
        return a
    return f"{a} {b}".strip() if a else b


# ---- Questions -------------------------------------------------------------------

def stitch_questions(pages: list) -> list[dict]:
    """pages: [(page_no, printed_page, data)] in page order. Returns question items."""
    items = []
    for page_no, printed, data in pages:
        for raw in data.get("questions", []):
            q = dict(raw)
            q["qnum"], q["part"] = normalise_qnum(q.get("qnum")), normalise_part(q.get("part"))
            q["pages"], q["printed_page"] = [page_no], printed
            if q.get("continues_from_previous_page") and items:
                last = items[-1]
                same_qnum = not q["qnum"] or q["qnum"] == last["qnum"]
                same_part = not q["part"] or q["part"] == last["part"]
                if same_qnum and same_part:
                    _merge_question(last, q)
                    continue
            items.append(q)
    return _dedupe_questions(items)


def _merge_question(last: dict, q: dict) -> None:
    last["context"] = _join_text(last.get("context", ""), q.get("context", ""))
    last["question"] = _join_text(last.get("question", ""), q.get("question", ""))
    last["marks"] = max(last.get("marks") or 0, q.get("marks") or 0)
    last["options"] = (last.get("options") or []) + [o for o in q.get("options") or [] if o not in (last.get("options") or [])]
    last["is_mcq"] = bool(last.get("is_mcq") or q.get("is_mcq"))
    if FIGURE_RANK.get(q.get("figure"), 0) > FIGURE_RANK.get(last.get("figure"), 0):
        last["figure"] = q["figure"]
    if q.get("uncertain"):
        last["uncertain"] = True
        last["uncertain_reason"] = _join_text(last.get("uncertain_reason", ""), q.get("uncertain_reason", ""))
    if q["pages"][0] not in last["pages"]:
        last["pages"].append(q["pages"][0])


def _dedupe_questions(items: list) -> list:
    kept, seen = [], {}
    for q in items:
        key = (q["qnum"], q["part"])
        if key in seen:
            first = seen[key]
            if _norm_text(first.get("question")) == _norm_text(q.get("question")):
                for p in q["pages"]:  # same question on a shared boundary page: drop it
                    if p not in first["pages"]:
                        first["pages"].append(p)
                continue
            q["duplicate_number"] = True
        else:
            seen[key] = q
        kept.append(q)
    return kept


# ---- Answer key entries ---------------------------------------------------------------

def stitch_answers(pages: list, default_unit: str = "") -> list[dict]:
    """Key entries with unit, qnum, part and the scheme fields, in page order.

    The unit comes from the entry, else the latest chapter heading seen, else default_unit.
    """
    entries, current_unit = [], normalise_unit(default_unit)
    for page_no, _printed, data in pages:
        heading = normalise_unit(data.get("chapter_heading"))
        if heading:
            current_unit = heading
        for raw in data.get("answers", []):
            a = dict(raw)
            a["unit"] = normalise_unit(a.get("unit")) or current_unit
            a["qnum"], a["part"] = normalise_qnum(a.get("qnum")), normalise_part(a.get("part"))
            a["pages"] = [page_no]
            if a.get("continues_from_previous_page") and entries:
                last = entries[-1]
                if (not a["qnum"] or a["qnum"] == last["qnum"]) and (not a["part"] or a["part"] == last["part"]):
                    _merge_answer(last, a)
                    continue
            if a["unit"]:
                current_unit = a["unit"]
            entries.append(a)
    return _dedupe_answers(entries)


def _merge_answer(last: dict, a: dict) -> None:
    for key in ("mark_points", "lorms", "rejects"):
        extra = [x for x in a.get(key) or [] if x not in (last.get(key) or [])]
        last[key] = (last.get(key) or []) + extra
    last["choose"] = max(last.get("choose") or 0, a.get("choose") or 0)
    last["correct_option"] = last.get("correct_option") or a.get("correct_option", "")
    if a.get("uncertain"):
        last["uncertain"] = True
        last["uncertain_reason"] = _join_text(last.get("uncertain_reason", ""), a.get("uncertain_reason", ""))
    if a["pages"][0] not in last["pages"]:
        last["pages"].append(a["pages"][0])


def _scheme_signature(a: dict) -> str:
    return json.dumps({k: a.get(k) for k in ("mode", "mark_points", "lorms", "correct_option")}, sort_keys=True)


def _dedupe_answers(entries: list) -> list:
    kept, seen = [], {}
    for a in entries:
        key = (a["unit"], a["qnum"], a["part"])
        if key in seen:
            if _scheme_signature(seen[key]) == _scheme_signature(a):
                continue
            a["duplicate_number"] = True
        else:
            seen[key] = a
        kept.append(a)
    return kept


# ---- Records ---------------------------------------------------------------------

def make_question_id(subject, level, slug, file_stem, qnum, part) -> str:
    raw = f"{subject}-{level}-{slug}-{file_stem}-q{qnum}{part}".lower()
    return re.sub(r"[^a-z0-9_\-]", "-", raw)


def scheme_fields(entry: dict) -> dict:
    """The mark-scheme part of a question record, from a key entry."""
    mode = entry.get("mode") or "points"
    if mode == "lorms":
        return {"marking_mode": "lorms", "lorms": [
            {k: lv[k] for k in ("level", "marks_min", "marks_max", "descriptor", "award_rules")}
            for lv in entry.get("lorms") or []]}
    if mode == "mcq":
        return {"marking_mode": "mcq", "correct_option": (entry.get("correct_option") or "").strip().upper()}
    points = []
    for i, mp in enumerate(entry.get("mark_points") or [], 1):
        point = {"id": f"MP{i}", "text": mp.get("text", "")}
        if mp.get("key_terms"):
            point["key_terms"] = mp["key_terms"]
        if mp.get("alternatives"):
            point["alternatives"] = mp["alternatives"]
        requires = [f"MP{r}" for r in mp.get("requires") or [] if isinstance(r, int) and 1 <= r < i]
        if requires:
            point["requires"] = requires
        points.append(point)
    fields = {"marking_mode": "points", "mark_points": points}
    if entry.get("choose"):
        fields["choose"] = entry["choose"]
    if entry.get("rejects"):
        fields["rejects"] = entry["rejects"]
    return fields


def build_question(item: dict, *, cfg, file_stem: str, file_topic, scheme, known_topics: set):
    """(record, warnings) for one stitched question item, with its matched key entry or None.

    file_topic: the topic from source.yaml units, or None to use the model's choice (exam papers).
    """
    warnings = []
    if file_topic is not None:
        topic = file_topic
    else:
        suggested = (item.get("topic") or "").strip()
        topic = suggested if suggested in known_topics else UNASSIGNED
        if suggested and topic == UNASSIGNED:
            warnings.append(f"The model suggested topic '{suggested}', which is not in topics.yaml")

    part = item["part"]
    record = {
        "id": make_question_id(cfg.subject, cfg.level, cfg.slug, file_stem, item["qnum"], part),
        "subject": cfg.subject, "level": cfg.level, "topic": topic, "source": cfg.key_reliability,
        "source_ref": f"{cfg.title}, p.{item.get('printed_page') or '?'}, Q{item['qnum']}" + (f"({part})" if part else ""),
        "context": item.get("context", ""), "question": item.get("question", ""),
        "marks": item.get("marks") or 0, "command_word": (item.get("command_word") or "").lower(),
        "figure": item.get("figure") or "none",
    }
    if scheme is not None:
        record.update(scheme_fields(scheme))
    elif item.get("is_mcq"):
        record.update({"marking_mode": "mcq", "correct_option": ""})
    else:
        record.update({"marking_mode": "points", "mark_points": []})
    if record["marking_mode"] == "mcq":
        record["options"] = [{"label": o.get("label", "").strip().upper(), "text": o.get("text", "")}
                             for o in item.get("options") or []]

    warnings += question_warnings(record, item, scheme, known_topics)
    return record, warnings


def question_warnings(record: dict, item: dict, scheme, known_topics: set) -> list[str]:
    w = []
    if not record.get("marks"):
        w.append("No marks shown for this question")
    if scheme is None:
        w.append("No matching mark scheme found in the answer key")
    if record["marking_mode"] == "points" and scheme is not None:
        n = len(record.get("mark_points") or [])
        if record.get("marks") and n < record["marks"] and not record.get("choose"):
            w.append(f"Fewer mark points ({n}) than marks ({record['marks']}) and no 'any N'")
    topic = record.get("topic")
    if topic == UNASSIGNED:
        w.append("Topic is unassigned")
    elif known_topics and topic not in known_topics:
        w.append(f"Topic '{topic}' is not in topics.yaml")
    if record.get("figure") == "needed_to_answer":
        w.append("Needs a figure to answer: will be left out of typed practice")
    if item.get("uncertain"):
        w.append(f"Model unsure about the question: {item.get('uncertain_reason') or 'no reason given'}")
    if scheme is not None and scheme.get("uncertain"):
        w.append(f"Model unsure about the answer: {scheme.get('uncertain_reason') or 'no reason given'}")
    if item.get("duplicate_number"):
        w.append("Another question in this file has the same number with different text")
    if scheme is not None and scheme.get("duplicate_number"):
        w.append("The answer key has two different answers for this question number")
    if scheme is not None and record.get("marks"):
        w += [f"Check: {p.split(': ', 1)[-1]}" for p in validate_question(record)]
    return w
