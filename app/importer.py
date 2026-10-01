"""Load question JSON files (calibration format) into the questions table.

File format: see docs/calibration_format_example.json. A file is checked in
full before anything is written; one bad question rejects the whole file.

Command line:
    python -m app.importer data/questions/           (every *.json in the folder)
    python -m app.importer data/calibration/set.json
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from app.db import connect, init_db, utc_now

SOURCES = {"assessment_book", "publisher_paper", "school", "generated"}
MODES = {"points", "lorms", "mcq"}
FIGURES = {"none", "described", "needed_to_answer"}
COUNT_BASES = {"supported", "any"}


class QuestionFileError(ValueError):
    """A question file that can't be imported, with every problem found."""

    def __init__(self, path, problems):
        self.problems = problems
        lines = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"{path}: {len(problems)} problem(s):\n{lines}")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_question(q) -> list[str]:
    """Return a list of problems with one question record (empty if it's fine)."""
    if not isinstance(q, dict):
        return ["a question entry is not a JSON object"]

    qid = q["id"] if _nonempty_str(q.get("id")) else "<no id>"
    problems = []

    def err(msg):
        problems.append(f"{qid}: {msg}")

    for key in ("id", "subject", "level", "topic", "source", "question", "marking_mode"):
        if not _nonempty_str(q.get(key)):
            err(f"'{key}' must be a non-empty string")
    for key in ("source_ref", "context", "command_word"):
        if key in q and not isinstance(q[key], str):
            err(f"'{key}' must be a string")
    if _nonempty_str(q.get("source")) and q["source"] not in SOURCES:
        err(f"'source' must be one of {sorted(SOURCES)}")

    marks = q.get("marks")
    if not _is_int(marks) or marks < 1:
        err("'marks' must be a whole number of 1 or more")
        marks = None

    mode = q.get("marking_mode")
    if _nonempty_str(mode) and mode not in MODES:
        err("'marking_mode' must be 'points', 'lorms' or 'mcq'")
    elif mode == "points":
        _validate_points(q, marks, err)
    elif mode == "lorms":
        _validate_lorms(q, marks, err)
    elif mode == "mcq":
        _validate_mcq(q, err)

    if "figure" in q and q["figure"] not in FIGURES:
        err(f"'figure' must be one of {sorted(FIGURES)}")
    if "rejects" in q and not _is_str_list(q["rejects"]):
        err("'rejects' must be a list of strings")
    if "answers" in q:
        _validate_answers(q["answers"], marks, err)
    return problems


def _validate_points(q, marks, err):
    mps = q.get("mark_points")
    if not isinstance(mps, list) or not mps:
        err("points mode needs a non-empty 'mark_points' list")
        return
    ids = []
    for i, mp in enumerate(mps, 1):
        if not isinstance(mp, dict):
            err(f"mark point {i} is not a JSON object")
            continue
        mid = mp.get("id")
        if not _nonempty_str(mid):
            err(f"mark point {i} needs an 'id'")
            continue
        if mid in ids:
            err(f"duplicate mark point id '{mid}'")
        ids.append(mid)
        if not _nonempty_str(mp.get("text")):
            err(f"mark point {mid} needs 'text'")
        for key in ("key_terms", "alternatives", "requires"):
            if key in mp and not _is_str_list(mp[key]):
                err(f"mark point {mid} '{key}' must be a list of strings")
    for mp in mps:
        if isinstance(mp, dict) and _is_str_list(mp.get("requires", [])):
            for req in mp.get("requires", []):
                if req == mp.get("id"):
                    err(f"mark point {req} requires itself")
                elif req not in ids:
                    err(f"mark point {mp.get('id')} requires unknown mark point '{req}'")
    if marks is not None and len(ids) < marks:
        err(f"worth {marks} marks but has only {len(ids)} mark point(s)")
    if "choose" in q:
        choose = q["choose"]
        if not _is_int(choose) or not 1 <= choose <= len(ids):
            err("'choose' must be a whole number from 1 to the number of mark points")


def _validate_mcq(q, err):
    options = q.get("options")
    if not isinstance(options, list) or len(options) < 2:
        err("mcq mode needs an 'options' list with at least two options")
        return
    labels = []
    for i, opt in enumerate(options, 1):
        if not isinstance(opt, dict) or not _nonempty_str(opt.get("label")) or not isinstance(opt.get("text"), str):
            err(f"option {i} needs a 'label' and 'text'")
            continue
        if opt["label"] in labels:
            err(f"duplicate option label '{opt['label']}'")
        labels.append(opt["label"])
    if q.get("correct_option") not in labels:
        err("'correct_option' must be one of the option labels")


def _validate_lorms(q, marks, err):
    levels = q.get("lorms")
    if not isinstance(levels, list) or not levels:
        err("lorms mode needs a non-empty 'lorms' list of levels")
        return
    ranges = []
    for i, lv in enumerate(levels, 1):
        if not isinstance(lv, dict):
            err(f"LORMS entry {i} is not a JSON object")
            continue
        n = lv.get("level")
        if not _is_int(n) or n < 1:
            err(f"LORMS entry {i} needs a whole-number 'level' of 1 or more")
            continue
        lo, hi = lv.get("marks_min"), lv.get("marks_max")
        if not (_is_int(lo) and _is_int(hi) and 0 <= lo <= hi):
            err(f"level {n} needs whole numbers 'marks_min' <= 'marks_max'")
            continue
        if not _nonempty_str(lv.get("descriptor")):
            err(f"level {n} needs a 'descriptor'")
        if "award_rules" in lv and not isinstance(lv["award_rules"], str):
            err(f"level {n} 'award_rules' must be a string")
        if "exemplars" in lv and not isinstance(lv["exemplars"], list):
            err(f"level {n} 'exemplars' must be a list")
        if "count" in lv:
            _validate_count(lv["count"], n, lo, hi, err)
        ranges.append((n, lo, hi))

    if len(ranges) != len(levels):
        return  # already reported
    ranges.sort()
    numbers = [r[0] for r in ranges]
    if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        err("LORMS levels must be numbered consecutively with no repeats")
        return
    for (a, _, a_hi), (b, b_lo, _) in zip(ranges, ranges[1:]):
        if b_lo <= a_hi:
            err(f"level {a} and level {b} mark ranges overlap")
        elif b_lo != a_hi + 1:
            err(f"gap between level {a} (max {a_hi}) and level {b} (min {b_lo})")
    if marks is not None and ranges[-1][2] != marks:
        err(f"top level's marks_max ({ranges[-1][2]}) must equal 'marks' ({marks})")


def _validate_count(count, n, lo, hi, err):
    if not isinstance(count, dict):
        err(f"level {n} 'count' must be a JSON object")
        return
    if count.get("basis") not in COUNT_BASES:
        err(f"level {n} count 'basis' must be \"supported\" or \"any\"")
    table = count.get("marks_by_count")
    if not isinstance(table, dict) or not table:
        err(f"level {n} count 'marks_by_count' must be a non-empty object")
        return
    for key, value in table.items():
        if not (key.isdigit() and int(key) >= 1):
            err(f"level {n} count key '{key}' must be a whole number of 1 or more")
        if not _is_int(value) or not lo <= value <= hi:
            err(f"level {n} count value for '{key}' must be a whole number from {lo} to {hi}")


def _validate_answers(answers, marks, err):
    if not isinstance(answers, list):
        err("'answers' must be a list")
        return
    seen = set()
    for i, a in enumerate(answers, 1):
        if not isinstance(a, dict):
            err(f"answer {i} is not a JSON object")
            continue
        aid = a.get("answer_id")
        if not _nonempty_str(aid):
            err(f"answer {i} needs an 'answer_id'")
            aid = f"#{i}"
        elif aid in seen:
            err(f"duplicate answer_id '{aid}'")
        seen.add(aid)
        if not isinstance(a.get("text"), str):
            err(f"answer {aid} needs 'text'")
        expected = a.get("expected_marks")
        if not _is_int(expected) or expected < 0 or (marks is not None and expected > marks):
            err(f"answer {aid} 'expected_marks' must be a whole number from 0 to the question's marks")
        if "tolerance" in a and (not _is_int(a["tolerance"]) or a["tolerance"] < 0):
            err(f"answer {aid} 'tolerance' must be a whole number of 0 or more")


def load_file(path) -> dict:
    """Read and check a question file. Raises QuestionFileError listing every problem."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise QuestionFileError(path, ["file not found"]) from None
    except json.JSONDecodeError as e:
        raise QuestionFileError(path, [f"not valid JSON: {e}"]) from None

    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise QuestionFileError(path, ["top level must be an object with a 'questions' list"])

    problems = []
    seen = set()
    for q in data["questions"]:
        problems.extend(validate_question(q))
        qid = q.get("id") if isinstance(q, dict) else None
        if isinstance(qid, str):
            if qid in seen:
                problems.append(f"{qid}: question id appears more than once in this file")
            seen.add(qid)
    if problems:
        raise QuestionFileError(path, problems)
    return data


def stored_record(q: dict) -> dict:
    """The question as stored: everything except the calibration test answers."""
    return {k: v for k, v in q.items() if k != "answers"}


def import_questions(conn, questions) -> dict:
    """Insert new questions and update changed ones, matched by id. Returns counts.

    A changed question gets the next version number; earlier versions are kept
    in question_versions so past attempts still show what she answered.
    """
    counts = {"added": 0, "updated": 0, "unchanged": 0}
    now = utc_now()
    for q in questions:
        record_json = json.dumps(stored_record(q), sort_keys=True, ensure_ascii=False)
        content_hash = hashlib.sha256(record_json.encode("utf-8")).hexdigest()
        row = conn.execute("SELECT content_hash, version FROM questions WHERE id = ?", (q["id"],)).fetchone()
        values = (
            q["subject"], q["level"], q["topic"], q["source"], q.get("source_ref", ""),
            q["marking_mode"], q["marks"], record_json, content_hash, q.get("figure", "none"),
        )
        if row is None:
            version = 1
            conn.execute(
                """INSERT INTO questions
                   (subject, level, topic, source, source_ref, marking_mode, marks,
                    record_json, content_hash, figure, id, version, imported_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values + (q["id"], version, now, now),
            )
            counts["added"] += 1
        elif row["content_hash"] != content_hash:
            version = row["version"] + 1
            conn.execute(
                """UPDATE questions
                   SET subject = ?, level = ?, topic = ?, source = ?, source_ref = ?,
                       marking_mode = ?, marks = ?, record_json = ?, content_hash = ?,
                       figure = ?, version = ?, updated_at = ?
                   WHERE id = ?""",
                values + (version, now, q["id"]),
            )
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1
            continue
        conn.execute(
            """INSERT INTO question_versions (question_id, version, record_json, content_hash, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (q["id"], version, record_json, content_hash, now),
        )
    conn.commit()
    return counts


def get_question(conn, question_id: str):
    """The current version of a question, with its version number under "version"."""
    row = conn.execute("SELECT record_json, version FROM questions WHERE id = ?", (question_id,)).fetchone()
    if not row:
        return None
    return {**json.loads(row["record_json"]), "version": row["version"]}


def get_question_version(conn, question_id: str, version: int):
    """A question exactly as it was at a given version."""
    row = conn.execute(
        "SELECT record_json FROM question_versions WHERE question_id = ? AND version = ?",
        (question_id, version),
    ).fetchone()
    return {**json.loads(row["record_json"]), "version": version} if row else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Import question JSON files into the database.")
    parser.add_argument("paths", nargs="+", help="JSON files, or folders of *.json files")
    args = parser.parse_args(argv)

    files = []
    for p in map(Path, args.paths):
        if p.is_dir():
            files.extend(sorted(p.glob("*.json")))
        else:
            files.append(p)

    init_db()
    conn = connect()
    status = 0
    try:
        for f in files:
            try:
                data = load_file(f)
            except QuestionFileError as e:
                print(e, file=sys.stderr)
                status = 1
                continue
            c = import_questions(conn, data["questions"])
            print(f"{f}: {c['added']} added, {c['updated']} updated, {c['unchanged']} unchanged")
    finally:
        conn.close()
    return status


if __name__ == "__main__":
    sys.exit(main())
