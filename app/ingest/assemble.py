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
from app.ingest.cards import UNSURE_CARD

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


def split_qnum(qnum: str):
    """(section, number): 'b5' -> ('b', 5); '12' -> ('', 12). (None, None) for worked examples
    and anything that isn't an optional section letter followed by a number."""
    m = re.match(r"^([a-z]*)(\d+)$", qnum or "")
    if not m or qnum.startswith("we"):
        return None, None
    return m.group(1), int(m.group(2))


def show_qnum(qnum: str) -> str:
    """'5' -> 'Q5'; 'b5' -> 'B5' (section letter printed on the paper); 'we2' -> 'WE2'."""
    return f"Q{qnum}" if qnum.isdigit() or not qnum else qnum.upper()


def part_label(qnum: str, part: str) -> str:
    return show_qnum(qnum) + (f"({part})" if part else "")


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
    """Fill in a question number or parent part that is printed on an earlier page.

    Sets entry["number_inherited"] when either was taken from the entry before.
    """
    if last is None:
        return
    if not entry["qnum"] and last["qnum"]:
        entry["qnum"], entry["number_inherited"] = last["qnum"], True
    elif entry["qnum"].isdigit():
        # The same question continuing without its section letter: B5(c) is not Q5(c).
        section, number = split_qnum(last["qnum"])
        if section and number == int(entry["qnum"]):
            entry["qnum"], entry["number_inherited"] = last["qnum"], True
    if entry["qnum"] == last["qnum"] and _sub_part_of_last(entry["part"], last["part"]):
        entry["part"], entry["number_inherited"] = last["part"][0] + entry["part"], True


def stitch_questions(pages: list) -> list[dict]:
    """pages: [(page_no, printed_page, data)] in page order. Returns question items.

    number_inherited is set on items whose question number (or parent part) is printed
    on an earlier page: inherited here, or filled in by the model at the top of a page
    that continues the previous page's question. A part with no number takes the number
    of the latest numbered item, so a stem on one page (even a sources page) gives its
    number and text to the parts that follow on the next page.

    Page-level doubts are not copied onto items: an item only carries a reason the
    model gave for that item. Page doubts are listed in the summary instead.

    Sources (Source A, B ...) that questions refer to are added to their context
    (see attach_sources).
    """
    sets, set_at = source_sets(pages)
    items = []
    for page_no, printed, data in pages:
        carried = items[-1]["qnum"] if items else ""  # the question running over from the previous page
        carrying = bool(carried)
        for raw in data.get("questions", []):
            q = dict(raw)
            q["qnum"], q["part"] = normalise_qnum(q.get("qnum")), normalise_part(q.get("part"))
            q["pages"], q["printed_page"] = [page_no], printed
            q["first_page"], q["source_set"] = page_no, set_at.get(page_no)
            if q.get("continues_from_previous_page") and items:
                last = items[-1]
                same_qnum = not q["qnum"] or q["qnum"] == last["qnum"]
                same_part = not q["part"] or q["part"] == last["part"]
                if same_qnum and same_part:
                    _merge_question(last, q)
                    continue
            _inherit_labels(q, next((i for i in reversed(items) if i["qnum"]), None))
            if carrying and q["qnum"] == carried:
                q["number_inherited"] = True
            else:
                carrying = False  # a new question number has started on this page
            items.append(q)
    for q in items:
        if not q["qnum"]:
            q["no_number"] = True  # kept, never dropped silently; flagged on the review page
    items = _dedupe_questions(_fold_stems(items))
    attach_sources(items, sets)
    return items


# ---- Sources (History and Geography source-based questions) ------------------------------

BACKGROUND = "BACKGROUND"
_LABEL = r"[A-Z0-9](?![A-Za-z0-9])"
SOURCE_REF = re.compile(rf"\b[Ss]ources?\s+({_LABEL}(?:\s*(?:,|&|\band\b|\bto\b|–|-)\s*{_LABEL})*)")
ALL_SOURCES = re.compile(r"\b(?:all (?:of )?the sources|all sources|both sources)\b", re.IGNORECASE)


def normalise_source_label(label) -> str:
    """'Source A', 'a.' and 'A' are all 'A'; 'Background' is 'BACKGROUND'."""
    text = re.sub(r"(?i)^\s*sources?\b", "", str(label or ""))
    return re.sub(r"[^0-9A-Za-z]", "", text).upper()


def source_labels_in(text) -> tuple:
    """(labels, every): the source labels a piece of question text refers to, and whether it
    refers to all of them. 'Sources C and D' -> ({'C', 'D'}, False); 'Sources A to C' -> A, B, C."""
    text = str(text or "")
    labels = set()
    for m in SOURCE_REF.finditer(text):
        group = m.group(1)
        found = re.findall(r"[A-Z0-9]", group)
        if len(found) == 2 and re.search(r"\bto\b|[–-]", group) and ord(found[0]) <= ord(found[1]):
            found = [chr(c) for c in range(ord(found[0]), ord(found[1]) + 1)]
        labels.update(found)
    return labels, bool(ALL_SOURCES.search(text))


def source_sets(pages: list) -> tuple:
    """([set, ...], {page_no: index of the set in force on that page, or None}).

    A set is the sources one question (or group of questions) refers to: {"index", "first_page",
    "by_label": {label: source}}. A page of sources starts a new set when a question has been asked
    since the last sources page, or when a label repeats. Sources spread over consecutive pages
    stay in one set. Works from the stored readings, so no API calls.
    """
    sets, at, current, asked = [], {}, None, True
    for page_no, _printed, data in pages:
        found = [dict(s, page=page_no, label=normalise_source_label(s.get("label")),
                      has_picture=bool(s.get("has_picture") or (s.get("picture_description") or "").strip()))
                 for s in data.get("sources") or []
                 if (s.get("text") or "").strip() or (s.get("picture_description") or "").strip()]
        if found:
            if current is None or asked or {s["label"] for s in found} & set(current["by_label"]):
                current = {"index": len(sets), "first_page": page_no, "by_label": {}}
                sets.append(current)
                asked = False
            for s in found:
                current["by_label"].setdefault(s["label"], s)
        if data.get("questions"):
            asked = True
        at[page_no] = current["index"] if current else None
    return sets, at


def format_source(s: dict) -> str:
    """'Source A: text [Picture: description] (provenance)'."""
    head = "Background" if s["label"] == BACKGROUND else f"Source {s['label']}"
    parts = [(s.get("text") or "").strip()]
    if (s.get("picture_description") or "").strip():
        parts.append(f"[Picture: {s['picture_description'].strip()}]")
    if (s.get("provenance") or "").strip():
        parts.append(f"({s['provenance'].strip()})")
    return f"{head}: " + " ".join(p for p in parts if p)


def attach_sources(items: list, sets: list) -> None:
    """Put the sources each question refers to into its context (in label order, with any
    background text first). Picture sources also add their page to the item's pages, so the
    page image is shown with the question and sent to the marker, and make the figure
    'needed_to_answer'. Labels with no source found are listed in item['missing_sources'].

    A question's references come from its own wording and its stem's (stems are folded into
    context before this runs) plus the model's source_refs. They are looked up in the set of
    sources in force on the question's page, then in the next set (a stem can come before its
    sources). Nothing is added when a question refers to no source.
    """
    if not items:
        return
    for item in items:
        text = f"{item.get('context', '')} {item.get('question', '')}"
        labels, every = source_labels_in(text)
        for ref in item.get("source_refs") or []:
            if str(ref).strip().lower() in ("all", "all sources"):
                every = True
            elif normalise_source_label(ref):
                labels.add(normalise_source_label(ref))
        if not labels and not every:
            continue
        home = sets[item["source_set"]] if item.get("source_set") is not None else None
        ahead = next((s for s in sets if s["first_page"] > item.get("first_page", 0)), None)
        found, missing = {}, []
        for label in sorted(labels):
            source = next((s["by_label"][label] for s in (home, ahead) if s and label in s["by_label"]), None)
            if source:
                found[label] = source
            else:
                missing.append(label)
        if every:
            chosen = home or ahead
            if chosen:
                found.update({k: v for k, v in chosen["by_label"].items() if k != BACKGROUND})
            else:
                missing.append("all")
        if found:
            background = next((s["by_label"][BACKGROUND] for s in (home, ahead) if s and BACKGROUND in s["by_label"]), None)
            block = "\n".join(format_source(s) for s in ([background] if background else []) + [found[k] for k in sorted(found)])
            if _norm_text(block) not in _norm_text(item.get("context", "")):
                item["context"] = f"{item.get('context', '')}\n\n{block}".strip()
            item["sources_used"] = sorted(found)
            for s in found.values():
                if s.get("has_picture"):
                    if FIGURE_RANK["needed_to_answer"] > FIGURE_RANK.get(item.get("figure"), 0):
                        item["figure"] = "needed_to_answer"
                    if s["page"] not in item["pages"]:
                        item["pages"] = sorted(item["pages"] + [s["page"]])
        if missing:
            item["missing_sources"] = missing


def worked_example_label(items: list):
    """The label for unnumbered answers in a questions file: the file's worked example
    ('we2'), or 'we' if it has several; None if the file has no worked examples."""
    labels = sorted({i["qnum"] for i in items if i["qnum"].startswith("we")})
    if not labels:
        return None
    return labels[0] if len(labels) == 1 else "we"


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

def stitch_answers(pages: list, default_unit: str = "", unnumbered_label=None, answer_sections=()) -> list[dict]:
    """Key entries with unit, qnum, part and the scheme fields, in page order.

    The unit comes from the entry, else the latest chapter heading seen, else default_unit.
    unnumbered_label: in a questions file with worked examples, an answer with no printed
    question number belongs to the worked example and gets this label (e.g. 'we').
    answer_sections: [(first file page, last file page, unit)] from source.yaml; every answer on
    those pages gets that unit, whatever the model read from the entry or the heading.
    """
    entries, current_unit = [], normalise_unit(default_unit)
    for page_no, _printed, data in pages:
        forced = next((unit for first, last, unit in answer_sections if first <= page_no <= last), "")
        heading = normalise_unit(data.get("chapter_heading"))
        if forced:
            current_unit = forced
        elif heading:
            current_unit = heading
        for position, raw in enumerate(data.get("answers", [])):
            a = dict(raw)
            a["unit"] = forced or normalise_unit(a.get("unit")) or current_unit
            a["qnum"], a["part"] = normalise_qnum(a.get("qnum")), normalise_part(a.get("part"))
            a["pages"] = [page_no]
            if "exemplars" in a:
                a["exemplars"] = normalise_exemplars(a["exemplars"])
            if entries:
                last = entries[-1]
                if a.get("continues_from_previous_page"):
                    if (not a["qnum"] or a["qnum"] == last["qnum"]) and (not a["part"] or a["part"] == last["part"]):
                        _merge_answer(last, a)
                        continue
                elif position == 0 and _continues_level_table(last, a, page_no):
                    _merge_answer(last, a)  # the rest of a level table or model answer, whatever the model said
                    continue
            last = entries[-1] if entries else None
            if not a["qnum"] and unnumbered_label:
                a["qnum"], a["worked_example"] = unnumbered_label, True
            _inherit_labels(a, last if last is not None and last["unit"] == a["unit"] else None)
            if a["unit"]:
                current_unit = a["unit"]
            entries.append(a)
    return _dedupe_answers(entries)


_EXEMPLAR_LEVEL = re.compile(r"^\s*Level\s+(\d+)\s*[:\-–]\s*", re.IGNORECASE)


def normalise_exemplars(items) -> list:
    """Model answers as [{"level", "text"}]. The model gives plain strings, "Level 3: ..." when the key says
    which level the answer shows (level 0 = not stated); {"level", "text"} entries pass through."""
    out = []
    for e in items or []:
        if isinstance(e, dict):
            out.append({"level": e.get("level") or 0, "text": e.get("text") or ""})
        else:
            m = _EXEMPLAR_LEVEL.match(str(e))
            out.append({"level": int(m.group(1)) if m else 0, "text": str(e)[m.end():] if m else str(e)})
    return [e for e in out if e["text"].strip()]


def _is_level_answer(a: dict) -> bool:
    """A level-of-response answer: a level table, or only a model answer (no mark points)."""
    return a.get("mode") == "lorms" or bool(a.get("exemplars") and not a.get("mark_points"))


def _continues_level_table(last: dict, a: dict, page_no: int) -> bool:
    """Does the first answer on a page carry on the previous answer's level table or model answer?

    True when both are level answers on consecutive answer pages and the new one has no question
    number (or repeats the last one's number and part). One question then has one set of levels.
    """
    if not (_is_level_answer(last) and _is_level_answer(a)) or page_no <= last["pages"][-1]:
        return False
    if a["unit"] != last["unit"]:
        return False
    if not a["qnum"] and not a["part"]:
        return True
    return a["qnum"] == last["qnum"] and a["part"] == last["part"]


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


def _sections(entries: list) -> dict:
    """{section: [(qnum, top-level part letters)]} in order of first appearance.

    entries: [(qnum, part)]. Worked examples and unnumbered entries are left out.
    'bi' and 'bii' both count as part 'b', so a key that answers (b) as a whole has
    the same structure as a paper that splits (b) into (i) and (ii).
    """
    order, parts = {}, {}
    for qnum, part in entries:
        section, number = split_qnum(qnum)
        if number is None:
            continue
        order.setdefault(section, [])
        if qnum not in parts:
            parts[qnum] = set()
            order[section].append(qnum)
        parts[qnum].add(part[:1])
    return {section: [(qnum, frozenset(parts[qnum])) for qnum in qnums] for section, qnums in order.items()}


def align_sections(questions: list, answers: list) -> dict:
    """{question number: answer-key number} where a section is numbered differently in the two.

    Only when, within a section, the paper and the key have the same number of questions
    with the same part structure in the same order (paper C12, C13; key C1, C2).
    Otherwise nothing is aligned and the questions stay unmatched.
    """
    key_sections = _sections(answers)
    mapping = {}
    for section, q_seq in _sections(questions).items():
        k_seq = key_sections.get(section)
        if not k_seq or {q for q, _ in q_seq} == {k for k, _ in k_seq}:
            continue  # nothing in the key for this section, or the numbers already agree
        if len(q_seq) != len(k_seq) or any(qp != kp for (_, qp), (_, kp) in zip(q_seq, k_seq)):
            continue
        mapping.update({q: k for (q, _), (k, _) in zip(q_seq, k_seq) if q != k})
    return mapping


def numbering_problems(questions: list, answers: list, mapping=None) -> list[str]:
    """Numbering check for one chapter or paper, section by section (A, B, C).

    questions, answers: [(qnum, part)] from the question items and the answer key.
    mapping: {question number: key number} for questions matched under another number.
    Flags gaps in question numbers within a section, duplicates, gaps in part letters,
    items with no number, and numbers the answer key has that no question has.
    """
    mapping = mapping or {}
    problems = []
    unnumbered = sum(1 for qn, _ in questions if not qn)
    if unnumbered:
        problems.append(f"{unnumbered} item(s) have no question number")
    for (qn, part), n in sorted(Counter(questions).items()):
        if qn and n > 1:
            problems.append(f"{part_label(qn, part)} appears {n} times")
    by_section = {}
    for qn, _ in questions:
        section, number = split_qnum(qn)
        if number is not None:
            by_section.setdefault(section, set()).add(number)
    for section, numbers in sorted(by_section.items()):
        nums = sorted(numbers)
        where = f"Section {section.upper()}: question" if section else "Question"
        for low, high in zip(nums, nums[1:]):
            if high - low > 1:
                missing = ", ".join(str(n) for n in range(low + 1, high))
                problems.append(f"{where} numbers jump from {low} to {high} ({missing} missing)")
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
                problems.append(f"{show_qnum(qn)} has parts {', '.join(sorted(letters))} but no {', '.join(missing)}")
    # Compare with the key using the key's own numbers for questions matched under another number.
    question_nums = {mapping.get(qn, qn) for qn, _ in questions if qn}
    for qn in sorted({qn for qn, _ in answers if qn} - question_nums, key=lambda x: (len(x), x)):
        problems.append(f"The answer key has {show_qnum(qn)} but no question {show_qnum(qn).lstrip('Q')} was found")
    return problems


def _merge_levels(old: list, new: list) -> list:
    """Level tables joined: a level seen again (cut by the page break) has its text joined;
    new levels are added. Sorted by level number."""
    levels = [dict(lv) for lv in old]
    for lv in new:
        same = next((x for x in levels if x.get("level") == lv.get("level")), None)
        if same is None:
            levels.append(dict(lv))
            continue
        for key in ("descriptor", "award_rules"):
            same[key] = _join_text(same.get(key, ""), lv.get(key, ""))
        if not same.get("marks_max") and lv.get("marks_max"):
            same["marks_min"], same["marks_max"] = lv.get("marks_min", 0), lv["marks_max"]
    return sorted(levels, key=lambda lv: lv.get("level") or 0)


def _merge_exemplars(old: list, new: list, continuing: bool) -> list:
    """Model answers joined. continuing: the new page only carries the rest of the last model answer."""
    out = [dict(e) for e in old]
    for i, e in enumerate(new):
        if continuing and i == 0 and out:
            out[-1]["text"] = _join_text(out[-1].get("text", ""), e.get("text", ""))
        elif e not in out:
            out.append(dict(e))
    return out


def _merge_answer(last: dict, a: dict) -> None:
    for key in ("mark_points", "rejects"):
        extra = [x for x in a.get(key) or [] if x not in (last.get(key) or [])]
        last[key] = (last.get(key) or []) + extra
    if a.get("lorms") or a.get("exemplars") or last.get("lorms") or last.get("exemplars"):
        last["lorms"] = _merge_levels(last.get("lorms") or [], a.get("lorms") or [])
        last["exemplars"] = _merge_exemplars(last.get("exemplars") or [], a.get("exemplars") or [],
                                             continuing=not a.get("lorms"))
        if a.get("mode") == "lorms" and last.get("mode") == "points" and not last.get("mark_points"):
            last["mode"] = "lorms"
    last["choose"] = max(last.get("choose") or 0, a.get("choose") or 0)
    last["correct_option"] = last.get("correct_option") or a.get("correct_option", "")
    if a.get("uncertain"):
        last["uncertain"] = True
        last["uncertain_reason"] = _join_text(last.get("uncertain_reason", ""), a.get("uncertain_reason", ""))
    if a["pages"][0] not in last["pages"]:
        last["pages"].append(a["pages"][0])


def _scheme_signature(a: dict) -> str:
    return json.dumps({k: a.get(k) for k in ("mode", "mark_points", "lorms", "exemplars", "correct_option")},
                      sort_keys=True)


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
    if mode == "lorms" or (entry.get("exemplars") and not entry.get("mark_points") and mode != "mcq"):
        # Levels as printed. A model or example answer is an exemplar of its level (the top level
        # when none is stated); it is never split into mark points.
        levels = [{k: lv.get(k, "" if k in ("descriptor", "award_rules") else 0)
                   for k in ("level", "marks_min", "marks_max", "descriptor", "award_rules")}
                  for lv in sorted(entry.get("lorms") or [], key=lambda lv: lv.get("level") or 0)]
        fields = {"marking_mode": "lorms", "lorms": levels}
        for e in entry.get("exemplars") or []:
            text = (e.get("text") or "").strip()
            if not text:
                continue
            target = next((lv for lv in levels if lv["level"] == e.get("level")), levels[-1] if levels else None)
            if target is not None:
                target.setdefault("exemplars", []).append(text)
            else:  # a model answer with no level table: kept on the record, flagged for hand-entering the levels
                fields.setdefault("exemplars", []).append(text)
        return fields
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


ANSWER_FORMATS = ("text", "table", "draw_or_label")
_DRAW_WORDING = re.compile(
    r"\b(draw|sketch|label|shade|plot|construct|trace|mark (on|in)|"
    r"complete the (ray |circuit |bar |line )?(diagram|drawing|graph|chart|circuit|figure))\b", re.IGNORECASE)
_TABLE_WORDING = re.compile(r"\b(complete|fill in|fill up)\b[^.?]*\btable\b", re.IGNORECASE)


def answer_format_of(item: dict) -> tuple[str, bool]:
    """(answer format, guessed). The model reports it; older stored readings don't have it,
    so it is guessed from the question's wording."""
    reported = item.get("answer_format")
    if reported in ANSWER_FORMATS:
        return reported, False
    text = item.get("question") or ""
    if _DRAW_WORDING.search(text):
        return "draw_or_label", True
    if _TABLE_WORDING.search(text):
        return "table", True
    return "text", False


def is_worked_example(qnum: str) -> bool:
    return qnum.startswith("we")


def build_question(item: dict, *, cfg, file_stem: str, file_topic, scheme, known_topics: set, images=None,
                   mcq_marks=None):
    """(record, warnings) for one stitched question item, with its matched key entry or None.

    file_topic: the topic from source.yaml units, or None to use the model's choice (exam papers).
    images: [{"page", "display", "model"}] for the question pages that may be shown: every
    page of this question number, never answer pages. Not stored for worked examples.
    mcq_marks: {section: marks per question} read from the instructions page; an MCQ with
    no printed marks takes its section's value, else 1 (no warning).
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
        "question_label": (item["qnum"].upper() + (f"({part})" if part else "")) if is_worked_example(item["qnum"])
        else part_label(item["qnum"], part),
    }
    record["answer_format"], guessed = answer_format_of(item)
    if guessed:
        warnings.append(f"Answer format guessed as '{record['answer_format']}' from the wording: check it")
    if is_worked_example(item["qnum"]):
        record["worked_example"] = True  # left out of practice: its page shows the solution
    elif images:
        record["page_images"] = images
    if scheme is not None:
        record.update(scheme_fields(scheme))
    elif item.get("is_mcq"):
        record.update({"marking_mode": "mcq", "correct_option": ""})
    else:
        record.update({"marking_mode": "points", "mark_points": []})
    if record["marking_mode"] == "lorms" and not record["marks"] and record.get("lorms"):
        # No marks printed on the question: the most it can earn is the top of the highest level.
        record["marks"] = max(lv.get("marks_max") or 0 for lv in record["lorms"])
    if record["marking_mode"] == "mcq":
        record["options"] = [{"label": o.get("label", "").strip().upper(), "text": o.get("text", "")}
                             for o in item.get("options") or []]
        if not record["marks"]:
            # MCQs rarely print marks on each question: use the section's stated marks, else 1.
            section = split_qnum(item["qnum"])[0] or ""
            record["marks"] = (mcq_marks or {}).get(section) or 1

    warnings += question_warnings(record, item, scheme, known_topics)
    return record, warnings


UNSURE_QUESTION = "Model unsure about the question: "
UNSURE_ANSWER = "Model unsure about the answer: "
UNSURE_PREFIXES = (UNSURE_QUESTION, UNSURE_ANSWER, UNSURE_CARD)


def _number_doubt_resolved(item: dict, scheme) -> bool:
    """The item's number is printed on an earlier page (number_inherited, set by stitching)
    and it found its answer in the key: the model's doubt about it is settled."""
    return bool(item.get("number_inherited") and scheme is not None)


def unsure_reason(warning: str):
    """(which, reason) for a "Model unsure" warning, else None."""
    for prefix in UNSURE_PREFIXES:
        if warning.startswith(prefix):
            return prefix.rstrip(": ").replace("Model unsure about the ", ""), warning[len(prefix):]
    return None


def _source_name(label: str) -> str:
    return "the sources" if label == "all" else "the background text" if label == BACKGROUND else f"Source {label}"


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
    if record.get("worked_example"):
        w.append("Worked example: left out of practice (its page shows the solution)")
    elif record.get("answer_format") == "draw_or_label":
        w.append("Drawing or labelling question: left out of typed practice")
    elif record.get("figure") == "needed_to_answer" and not record.get("page_images"):
        w.append("Depends on a figure but has no page image to show")
    # Only reasons the model gave for this item; page-level doubts stay on the page (see the summary).
    if item.get("uncertain") and not _number_doubt_resolved(item, scheme):
        w.append(f"{UNSURE_QUESTION}{item.get('uncertain_reason') or 'no reason given'}")
    if scheme is not None and scheme.get("uncertain"):
        w.append(f"{UNSURE_ANSWER}{scheme.get('uncertain_reason') or 'no reason given'}")
    if item.get("duplicate_number"):
        w.append("Another question in this file has the same number with different text")
    if scheme is not None and scheme.get("duplicate_number"):
        w.append("The answer key has two different answers for this question number")
    if item.get("no_number"):
        w.append("No question number could be found for this item: check which question it belongs to")
    if item.get("missing_sources"):
        w.append(f"Refers to {', '.join(_source_name(m) for m in item['missing_sources'])} but no text for "
                 "it was found: its sources page may need to be read again")
    if record.get("marking_mode") == "lorms" and not record.get("lorms") and scheme is not None:
        w.append("Only a model answer was found, with no level table: add the levels by hand")
    if scheme is not None and record.get("marks"):
        w += [f"Check: {p.split(': ', 1)[-1]}" for p in validate_question(record)]
    return w


TWO_ANSWERS = "More than one answer was found for this question; the first was used: check it"
TWO_ANSWERS_WORKED = ("More than one answer was found for this question; the worked solution in the same file "
                      "was used: check it")


def matched_by_position(qnum: str, key_qnum: str) -> str:
    return (f"Matched by position: {show_qnum(qnum)} in the paper is {show_qnum(key_qnum)} in the answer key: "
            f"confirm it")


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
    ("Worked example:", "Worked example (not in practice)"),
    ("Drawing or labelling", "Drawing or labelling (not in practice)"),
    ("Depends on a figure but", "Figure needed, no page image"),
    ("Answer format guessed", "Answer format guessed"),
    (UNSURE_QUESTION, "Model unsure (question)"),
    (UNSURE_ANSWER, "Model unsure (answer)"),
    # Revision cards (texts in cards.py and staging.py)
    (UNSURE_CARD, "Model unsure (card)"),
    ("Card has no prompt", "Card without a prompt"),
    ("Card has no answer point", "Card without an answer"),
    ("Image card has no page image", "Image card without a page image"),
    ("Joined with a row from", "Joined across pages, left column dropped (check)"),
    ("Joined with a continuation", "Joined across pages in a different format (check)"),
    ("Split from a long answer", "Split from a long answer (check)"),
    ("Long answer", "Long answer, not split"),
    ("Prompt is close to", "Near-match to an existing card"),
    # Cards derived from school notes (texts in cards.py)
    ("Has blanks:", "Page has blanks (content may be missing)"),
    ("Possible duplicate of approved card", "Possible duplicate of an approved card"),
    ("Topic is uncertain", "Topic uncertain"),
    ("Derived card from a new source", "Derived card: check (new source)"),
    ("same number with different text", "Duplicate question number"),
    ("two different answers", "Duplicate answer in key"),
    ("No question number could be found", "No question number"),
    ("its sources page may need", "Source text not found"),
    ("Only a model answer was found", "Model answer without levels"),
    ("More than one answer was found", "Two answer sources"),
    ("Matched by position", "Numbering matched by position (confirm)"),
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
