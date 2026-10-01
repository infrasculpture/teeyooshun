"""Turn per-page model output into question items and key entries.

Plain functions, no database or API, so they can be tested with made-up data:
- stitching: a fragment marked continues_from_previous_page joins the last item;
- an item with a part label but no question number takes the last printed number, and a bare
  sub-part label such as "(ii)" takes the last letter part ("bii");
- a stem (no marks of its own) becomes context on each of its parts or sub-parts;
- worked-example labels ("Worked Example 2", "Example 2", "WE2") are made the same;
- items only carry doubts the model gave for them, never the page's;
- duplicates from shared boundary pages are dropped;
- the numbering check (gaps, duplicates, numbers the answer key has but the questions don't);
- IDs, source_ref and the question record (MARKING_SPEC / calibration format);
- review warnings (INGESTION_SPEC section 6).
"""

import json
import re
from collections import Counter

from app.importer import validate_question

UNASSIGNED = "unassigned"
FIGURE_RANK = {"none": 0, "described": 1, "needed_to_answer": 2}
ROMAN = re.compile(r"^(?=[ivx])x{0,3}(ix|iv|v?i{0,3})$")


WORKED_PREFIXES = ("workedexample", "example", "we")


def normalise_qnum(qnum) -> str:
    """'Q2.' -> '2'. Worked examples: 'Worked Example 2', 'Example 2' and 'WE2' -> 'we2'; 'WE' -> 'we'."""
    s = re.sub(r"[^0-9a-z]", "", str(qnum or "").lower())
    for prefix in WORKED_PREFIXES:
        rest = s[len(prefix):]
        if s.startswith(prefix) and (rest == "" or rest.isdigit()):
            return "we" + rest
    return s.lstrip("q")


def normalise_part(part) -> str:
    """'(c)(i)', 'c(i)', 'c (i)' and 'ci' are all 'ci'."""
    return re.sub(r"[^0-9a-z]", "", str(part or "").lower())


def parent_parts(part: str) -> list[tuple[str, str]]:
    """The (parent, sub-part) split of a part label: a single letter then a roman numeral or digits.

    'cii' -> [('c', 'ii')]; 'b2' -> [('b', '2')]; 'c' -> []. Used only when there is no exact match.
    """
    if len(part) < 2 or not part[0].isalpha():
        return []
    sub = part[1:]
    return [(part[0], sub)] if ROMAN.match(sub) or sub.isdigit() else []


def part_label(qnum: str, part: str) -> str:
    return f"Q{qnum}" + (f"({part})" if part else "")


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

def _sub_part_of_last(part: str, last_part: str) -> bool:
    """True if a bare roman label such as 'ii' is a sub-part of the last lettered part ('b' or 'bi').

    Not when it is simply the next letter: '(i)' straight after '(h)' is part i, not h(i).
    """
    if not part or not ROMAN.match(part) or not last_part or not last_part[0].isalpha():
        return False
    if ROMAN.match(last_part):  # top-level roman parts (i), (ii), (iii): not sub-parts
        return False
    return not (len(last_part) == 1 and len(part) == 1 and ord(part) == ord(last_part) + 1)


def _inherit_labels(entry: dict, last) -> None:
    """Fill in a question number or parent part that is printed on an earlier page."""
    if last is None:
        return
    if not entry["qnum"] and last["qnum"]:
        entry["qnum"], entry["qnum_inherited"] = last["qnum"], True
    if entry["qnum"] == last["qnum"] and _sub_part_of_last(entry["part"], last["part"]):
        entry["part"], entry["part_inherited"] = last["part"][0] + entry["part"], True


def stitch_questions(pages: list) -> list[dict]:
    """pages: [(page_no, printed_page, data)] in page order. Returns question items.

    Page-level doubts are not copied onto items: an item only carries a reason the
    model gave for that item. Page doubts are listed in the summary instead.
    """
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
            _inherit_labels(q, items[-1] if items else None)
            items.append(q)
    return _dedupe_questions(_fold_stems(items))


def _is_child(parent: str, child: str) -> bool:
    """Is part `child` inside part `parent`? '' contains every part; 'b' contains 'bi', 'bii', ..."""
    if child == parent:
        return False
    if parent == "":
        return child != ""
    return parent_parts(child) == [(parent, child[len(parent):])]


def _fold_stems(items: list) -> list:
    """A stem (no marks) introducing a question's parts, or a part's sub-parts, is not an item:
    its text becomes context on each of them. Q5 -> Q5(a), Q5(b); Q5(b) -> Q5(b)(i) ... (v).

    The stem's own doubts are not copied: they were about the stem, not the parts.
    """
    stems = [s for s in items if not (s.get("marks") or 0)
             and any(o is not s and o["qnum"] == s["qnum"] and _is_child(s["part"], o["part"]) for o in items)]
    # Innermost stems first, so an outer stem lands in front of an inner one.
    for stem in sorted(stems, key=lambda s: -len(s["part"])):
        stem_text = _join_text(stem.get("context", ""), stem.get("question", ""))
        for o in items:
            if o is stem or o["qnum"] != stem["qnum"] or not _is_child(stem["part"], o["part"]):
                continue
            context = o.get("context", "")
            if stem_text and _norm_text(stem_text) not in _norm_text(context):
                o["context"] = f"{stem_text} {context}".strip()
            if FIGURE_RANK.get(stem.get("figure"), 0) > FIGURE_RANK.get(o.get("figure"), 0):
                o["figure"] = stem["figure"]
            for p in reversed(stem["pages"]):
                if p not in o["pages"]:
                    o["pages"].insert(0, p)
    return [i for i in items if not any(i is s for s in stems)]


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
            last = entries[-1] if entries else None
            _inherit_labels(a, last if last is not None and last["unit"] == a["unit"] else None)
            if a["unit"]:
                current_unit = a["unit"]
            entries.append(a)
    return _dedupe_answers(entries)


def split_for_sub_part(entry: dict, sub: str):
    """(scheme, how) for a sub-part from an answer given for its parent part.

    how: "split" when the answer labels its sub-parts (only this sub-part's points kept),
    "missing" when it labels them but has none for this one (scheme None),
    "whole" when it doesn't label them (the whole answer is used).
    """
    points = entry.get("mark_points") or []
    labelled = [mp for mp in points if normalise_part(mp.get("sub_part"))]
    if entry.get("mode", "points") != "points" or not labelled:
        return entry, "whole"
    mine = [dict(mp, requires=[]) for mp in points if normalise_part(mp.get("sub_part")) == sub]
    if not mine:
        return None, "missing"
    return dict(entry, mark_points=mine, choose=0), "split"


def numbering_problems(questions: list, answers: list) -> list[str]:
    """Numbering check for one chapter or paper.

    questions, answers: [(qnum, part)] from the question items and the answer key.
    Flags gaps in question numbers, duplicates, gaps in part letters, items with
    no number, and numbers the answer key has that no question has.
    """
    problems = []
    unnumbered = sum(1 for qn, _ in questions if not qn)
    if unnumbered:
        problems.append(f"{unnumbered} item(s) have no question number")
    for (qn, part), n in sorted(Counter(questions).items()):
        if qn and n > 1:
            problems.append(f"{part_label(qn, part)} appears {n} times")
    nums = sorted({int(qn) for qn, _ in questions if qn.isdigit()})
    for low, high in zip(nums, nums[1:]):
        if high - low > 1:
            missing = ", ".join(str(n) for n in range(low + 1, high))
            problems.append(f"Question numbers jump from {low} to {high} ({missing} missing)")
    parts_by_q = {}
    for qn, part in questions:
        if qn and part:
            parts_by_q.setdefault(qn, set()).add(part[0])
    for qn, letters in sorted(parts_by_q.items()):
        letters = {c for c in letters if c.isalpha()}
        if letters:
            expected = {chr(c) for c in range(ord("a"), ord(max(letters)) + 1)}
            missing = sorted(expected - letters)
            if missing:
                problems.append(f"Q{qn} has parts {', '.join(sorted(letters))} but no {', '.join(missing)}")
    question_nums = {qn for qn, _ in questions if qn}
    for qn in sorted({qn for qn, _ in answers if qn} - question_nums, key=lambda x: (len(x), x)):
        problems.append(f"The answer key has Q{qn} but no question {qn} was found")
    return problems


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


UNSURE_QUESTION = "Model unsure about the question: "
UNSURE_ANSWER = "Model unsure about the answer: "
UNSURE_PREFIXES = (UNSURE_QUESTION, UNSURE_ANSWER)
# A doubt about the question number itself, e.g. "no question number shown on this page".
NUMBER_DOUBT = re.compile(r"\bnumber(s|ed|ing)?\b|\bnumbering\b|\bq\s*no\b|\blabel", re.IGNORECASE)


def _number_doubt_resolved(item: dict, scheme) -> bool:
    """The model was unsure because the number isn't printed here, but the inherited
    number found its answer in the key: that doubt is settled."""
    return bool(item.get("qnum_inherited") and scheme is not None
                and NUMBER_DOUBT.search(item.get("uncertain_reason") or ""))


def unsure_reason(warning: str):
    """(which, reason) for a "Model unsure" warning, else None."""
    for prefix in UNSURE_PREFIXES:
        if warning.startswith(prefix):
            return prefix.rstrip(": ").replace("Model unsure about the ", ""), warning[len(prefix):]
    return None


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
    # Only reasons the model gave for this item; page-level doubts stay on the page (see the summary).
    if item.get("uncertain") and not _number_doubt_resolved(item, scheme):
        w.append(f"{UNSURE_QUESTION}{item.get('uncertain_reason') or 'no reason given'}")
    if scheme is not None and scheme.get("uncertain"):
        w.append(f"{UNSURE_ANSWER}{scheme.get('uncertain_reason') or 'no reason given'}")
    if item.get("duplicate_number"):
        w.append("Another question in this file has the same number with different text")
    if scheme is not None and scheme.get("duplicate_number"):
        w.append("The answer key has two different answers for this question number")
    if scheme is not None and record.get("marks"):
        w += [f"Check: {p.split(': ', 1)[-1]}" for p in validate_question(record)]
    return w


TWO_ANSWERS = "More than one answer was found for this question; the first was used: check it"
TWO_ANSWERS_WORKED = ("More than one answer was found for this question; the worked solution in the same file "
                      "was used: check it")


def parent_attached(qnum: str, parent: str) -> str:
    return (f"The answer is given for {part_label(qnum, parent)} as a whole and was attached to each "
            f"sub-part: check it")


def parent_missing(qnum: str, parent: str, sub: str) -> str:
    return f"The answer for {part_label(qnum, parent)} labels its sub-parts but has none for ({sub})"


# Warning type for the run summary: (text the warning starts with or contains, label).
# Keep in step with the warning texts above and in staging.py. More specific texts first.
WARNING_TYPES = [
    ("No marks shown", "No marks shown"),
    ("No matching mark scheme", "No mark scheme"),
    ("Fewer mark points", "Fewer mark points than marks"),
    ("Topic is unassigned", "Topic unassigned"),
    ("not in topics.yaml", "Topic not in topics.yaml"),
    ("Needs a figure", "Needs a figure"),
    (UNSURE_QUESTION, "Model unsure (question)"),
    (UNSURE_ANSWER, "Model unsure (answer)"),
    ("same number with different text", "Duplicate question number"),
    ("two different answers", "Duplicate answer in key"),
    ("More than one answer was found", "Two answer sources"),
    ("as a whole and was attached", "Parent-part answer attached to sub-parts"),
    ("labels its sub-parts but has none", "Sub-part missing from answer"),
    ("Duplicate ID", "Duplicate ID"),
    ("Check:", "Record check failed"),
]


def warning_type(warning: str) -> str:
    for text, label in WARNING_TYPES:
        if warning.startswith(text) or text in warning:
            return label
    return "Other"
