"""Calibration runner (MARKING_SPEC.md section 10).

    python -m app.calibrate data/calibration/calibration_set_v1.json
    python -m app.calibrate data/calibration/recall_set_v1.json

Imports the file's questions, marks every test answer, prints expected against
awarded marks with flags and a summary, and saves full results as JSON in
<file's folder>/results/. Test answers are not stored as attempts.

A file with "kind": "recall" is a recall-rubric set instead (cards and cases, see run_recall): each case is a
typed Revise recall of a made-up card, checked with RECALL_MODEL, and compared with the expected status of each
point, the box move and the kinds of extra statement. It runs in a temporary database, so nothing reaches the real one.
"""

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app import app_settings, pricing, revise
from app.config import get_settings
from app.db import connect, init_db, utc_now
from app.importer import QuestionFileError, import_questions, load_file, stored_record
from app.llm import LLMError
from app.marking.marker import mark_answer
from app.marking.rules import OTHER_CLASSES, level_for_marks
from app.revision_cards import get_card, import_cards

# Targets from spec section 10.
POINTS_EXACT_TARGET = 0.80
POINTS_MAX_OFF = 1
LORMS_WITHIN_MARK_TARGET = 0.80


def build_row(question: dict, answer: dict, result: dict) -> dict:
    expected = answer["expected_marks"]
    tolerance = answer.get("tolerance", 0)
    awarded = result["marks_awarded"]
    diff = None if awarded is None else awarded - expected
    row = {
        "case": f"{question['id']}/{answer['answer_id']}",
        "mode": question["marking_mode"],
        "expected": expected,
        "awarded": awarded,
        "diff": diff,
        "tolerance": tolerance,
        "within_tolerance": diff is not None and abs(diff) <= tolerance,
        "expected_level": None,
        "awarded_level": None,
        "valid_points": result["valid_points"],
        "counted_points": result["counted_points"],
        "confidence": result["confidence"],
        "flags": result["flags"],
        "error": result["error"],
        "model": result["model"],
        "note": answer.get("note", ""),
    }
    if question["marking_mode"] == "lorms":
        row["expected_level"] = level_for_marks(question["lorms"], expected)
        row["awarded_level"] = result["level_awarded"]
    return row


def summarise(rows: list, usage: dict) -> dict:
    marked = [r for r in rows if r["awarded"] is not None]
    summary = {"cases": len(rows), "marked": len(marked), "errors": len(rows) - len(marked), "usage": usage}

    points = [r for r in marked if r["mode"] == "points"]
    if points:
        exact = sum(r["diff"] == 0 for r in points)
        max_off = max(abs(r["diff"]) for r in points)
        summary["points"] = {
            "cases": len(points),
            "exact": exact,
            "exact_pct": round(100 * exact / len(points), 1),
            "within_tolerance": sum(r["within_tolerance"] for r in points),
            "max_off": max_off,
            "target_met": exact / len(points) >= POINTS_EXACT_TARGET and max_off <= POINTS_MAX_OFF,
        }

    lorms = [r for r in marked if r["mode"] == "lorms"]
    if lorms:
        within_level = sum(abs((r["awarded_level"] or 0) - r["expected_level"]) <= 1 for r in lorms)
        within_mark = sum(abs(r["diff"]) <= 1 for r in lorms)
        summary["lorms"] = {
            "cases": len(lorms),
            "within_one_level": within_level,
            "within_one_mark": within_mark,
            "within_one_mark_pct": round(100 * within_mark / len(lorms), 1),
            "within_tolerance": sum(r["within_tolerance"] for r in lorms),
            "target_met": within_level == len(lorms) and within_mark / len(lorms) >= LORMS_WITHIN_MARK_TARGET,
        }
    return summary


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "NO"
    return str(value)


def format_table(rows: list) -> str:
    headers = ["Case", "Mode", "Exp", "Got", "Diff", "Tol", "OK", "Level e/g", "Valid/counted", "Conf", "Flags"]
    lines = []
    for r in rows:
        diff = "-" if r["diff"] is None else f"{r['diff']:+d}"
        level = f"{r['expected_level']}/{_fmt(r['awarded_level'])}" if r["mode"] == "lorms" else ""
        vc = f"{r['valid_points']}/{r['counted_points']}" if r["valid_points"] is not None else ""
        flags = ",".join(r["flags"]) or ""
        if r["error"]:
            flags = f"{flags} ({r['error']})"
        lines.append([r["case"], r["mode"], _fmt(r["expected"]), _fmt(r["awarded"]), diff,
                      _fmt(r["tolerance"]), _fmt(r["within_tolerance"]), level, vc,
                      _fmt(r["confidence"]), flags])
    widths = [max([len(h)] + [len(line[i]) for line in lines]) for i, h in enumerate(headers)]
    out = ["  ".join(h.ljust(w) for h, w in zip(headers, widths)),
           "  ".join("-" * w for w in widths)]
    out += ["  ".join(c.ljust(w) for c, w in zip(line, widths)).rstrip() for line in lines]
    return "\n".join(out)


def format_differences(records: list) -> str:
    """Detail for every case whose awarded mark differs from the expected mark."""
    differing = [r for r in records if r["diff"] not in (None, 0)]
    if not differing:
        return "Marks that differed: none."
    out = [f"Marks that differed ({len(differing)}):"]
    for r in differing:
        detail = r["result"]["detail"]
        out.append("")
        out.append(f"{r['case']}  expected {r['expected']}, got {r['awarded']} ({r['diff']:+d})"
                   + (f"  note: {r['note']}" if r["note"] else ""))
        out.append(f"  Answer: {r['answer_text']}")
        if r["flags"]:
            out.append(f"  Flags: {', '.join(r['flags'])}")
        if r["mode"] == "points":
            for p in detail["points"]:
                mark = "+" if p["awarded"] else "-"
                why = f" [{p['not_awarded_because']}]" if p["not_awarded_because"] else ""
                quote = f' "{p["evidence_quote"]}"' if p["evidence_quote"] else ""
                out.append(f"  {mark} {p['id']} {p['text']}{why}:{quote} {p['reason']}".rstrip())
        else:
            out.append(f"  Level: expected {r['expected_level']}, got {r['awarded_level']} "
                       f"(model proposed level {detail['proposed_level']}, {detail['proposed_marks']} marks)")
            for f in detail["features"]:
                support = f'supported by "{f["support_quote"]}"' if f["supported"] else "unsupported"
                out.append(f'  * "{f["claim"]}" ({support})' + (f" [{f['note']}]" if f["note"] else ""))
            for f in detail["dropped_features"]:
                out.append(f'  x "{f.get("claim", "")}" [{f["dropped_because"]}]')
            if detail["why_not_higher"]:
                out.append(f"  Why not higher: {detail['why_not_higher']}")
        if detail.get("borderline_reason"):
            out.append(f"  Borderline: {detail['borderline_reason']}")
    return "\n".join(out)


def format_summary(summary: dict, models: list, effort: str) -> str:
    def verdict(ok):
        return "PASS" if ok else "FAIL"

    out = [f"Cases: {summary['cases']}  marked: {summary['marked']}  errors: {summary['errors']}"]
    if summary["errors"]:
        out.append("  Some cases failed to mark (see Flags). Targets below use the marked cases only.")
    p = summary.get("points")
    if p:
        out.append(
            f"Points mode: {p['cases']} cases, exact {p['exact']} ({p['exact_pct']}%), "
            f"within tolerance {p['within_tolerance']}, largest miss {p['max_off']} mark(s)"
        )
        out.append(f"  Target (exact on >=80%, never more than 1 off): {verdict(p['target_met'])}")
    lo = summary.get("lorms")
    if lo:
        out.append(
            f"LORMS mode: {lo['cases']} cases, within one level {lo['within_one_level']}, "
            f"within one mark {lo['within_one_mark']} ({lo['within_one_mark_pct']}%), "
            f"within tolerance {lo['within_tolerance']}"
        )
        out.append(f"  Target (all within one level, >=80% within one mark): {verdict(lo['target_met'])}")
    u = summary["usage"]
    out.append(
        f"Tokens: input {u['input_tokens']}, output {u['output_tokens']}, "
        f"cache write {u['cache_creation_input_tokens']}, cache read {u['cache_read_input_tokens']}"
    )
    out.append(f"Models: {', '.join(models) or '-'}  effort: {effort or 'not sent'}")
    return "\n".join(out)


# ---- Recall sets (the recall rubric) -------------------------------------------------------------------

def is_recall_file(path) -> bool:
    """A recall calibration file says so: "kind": "recall"."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("kind") == "recall"


def check_recall_file(path, data) -> list:
    """Every problem in a recall file (empty if it is fine)."""
    problems = []
    cards, cases = data.get("cards"), data.get("cases")
    if not isinstance(cards, list) or not cards:
        return [f"{path}: needs a non-empty 'cards' list"]
    if not isinstance(cases, list) or not cases:
        return [f"{path}: needs a non-empty 'cases' list"]
    units = {}
    for card in cards:
        if not isinstance(card, dict) or not card.get("id") or not isinstance(card.get("answer"), dict):
            problems.append("every card needs an id and an answer")
            continue
        units[card["id"]] = {u["id"] for u in revise.check_units(card)}
    seen = set()
    for case in cases:
        name = case.get("id", "?") if isinstance(case, dict) else "?"
        if not isinstance(case, dict) or not case.get("id") or not str(case.get("answer", "")).strip():
            problems.append(f"case {name}: needs an id and an answer")
            continue
        if case["id"] in seen:
            problems.append(f"case {name}: duplicate id")
        seen.add(case["id"])
        if case.get("card") not in units:
            problems.append(f"case {name}: unknown card {case.get('card')!r}")
            continue
        expect = case.get("expect") or {}
        for pid, want in (expect.get("points") or {}).items():
            wanted = want if isinstance(want, list) else [want]
            if pid not in units[case["card"]] or not set(wanted) <= {"covered", "partial", "missed"}:
                problems.append(f"case {name}: bad expectation for point {pid!r}")
        moves = expect.get("box_move")
        if moves is not None and not set(moves if isinstance(moves, list) else [moves]) <= {"up", "stay", "down"}:
            problems.append(f"case {name}: box_move must be up, stay or down")
        for key in ("extras_include", "extras_exclude"):
            if not set(expect.get(key) or []) <= set(OTHER_CLASSES):
                problems.append(f"case {name}: {key} may only name {', '.join(OTHER_CLASSES)}")
    return problems


def _accepted(value) -> list:
    return value if isinstance(value, list) else [value]


def judge_recall(case: dict, check: dict, settings: dict) -> dict:
    """One case's row: what the model said, and whether each expectation held."""
    detail = check["result"]["detail"]
    got_points = {p["id"]: p["status"] for p in detail["points"]}
    score = detail["score"]
    total = len(detail["points"])
    move = revise.box_move(score, total, settings)
    classes = sorted({o["classification"] for o in detail["other_points"]})
    expect = case.get("expect") or {}
    problems = []
    for pid, want in (expect.get("points") or {}).items():
        if got_points.get(pid) not in _accepted(want):
            problems.append(f"{pid}: expected {'/'.join(_accepted(want))}, got {got_points.get(pid)}")
    if expect.get("box_move") is not None and move not in _accepted(expect["box_move"]):
        problems.append(f"box move: expected {'/'.join(_accepted(expect['box_move']))}, got {move}")
    for kind in expect.get("extras_include") or []:
        if kind not in classes:
            problems.append(f"expected an extra statement classed {kind}, found {classes or 'none'}")
    for kind in expect.get("extras_exclude") or []:
        if kind in classes:
            problems.append(f"found an extra statement classed {kind}, which should not appear")
    incorrect = detail["classification_counts"]["incorrect"]   # wrong statements, and points she stated backwards
    if incorrect < expect.get("incorrect_min", 0):
        problems.append(f"expected at least {expect['incorrect_min']} factually wrong statement(s), found {incorrect}")
    return {"case": case["id"], "card": case["card"], "note": case.get("note", ""), "ok": not problems,
            "problems": problems, "points": got_points, "score": score, "total": total, "box_move": move,
            "extras": classes, "answer": case["answer"], "one_to_add": detail.get("one_to_add", ""),
            "flags": check["result"]["flags"], "model": check["result"]["model"], "detail": detail}


def format_recall_table(rows: list) -> str:
    headers = ["Case", "Ok", "Points", "Score", "Box", "Extras", "Note"]
    lines = []
    for r in rows:
        points = " ".join(f"{pid}={status[0].upper()}" for pid, status in r["points"].items())
        lines.append([r["case"], "yes" if r["ok"] else "NO", points, f"{r['score']}/{r['total']}", r["box_move"],
                      ",".join(r["extras"]) or "-", r["note"][:48]])
    widths = [max([len(h)] + [len(line[i]) for line in lines]) for i, h in enumerate(headers)]
    out = ["  ".join(h.ljust(w) for h, w in zip(headers, widths)), "  ".join("-" * w for w in widths)]
    out += ["  ".join(c.ljust(w) for c, w in zip(line, widths)).rstrip() for line in lines]
    return "\n".join(out)


def format_recall_failures(rows: list) -> str:
    failed = [r for r in rows if not r["ok"]]
    if not failed:
        return "Cases that differed: none."
    out = [f"Cases that differed ({len(failed)}):"]
    for r in failed:
        out += ["", f"{r['case']}  {r['note']}".rstrip(), f"  Answer: {r['answer']}"]
        out += [f"  - {p}" for p in r["problems"]]
        for p in r["detail"]["points"]:
            quote = f' "{p["evidence_quote"]}"' if p["evidence_quote"] else ""
            out.append(f"  {p['id']} {p['status']}{quote}: {p['reason']}".rstrip())
        for o in r["detail"]["other_points"]:
            out.append(f"  extra [{o['classification']}] \"{o['quote']}\": {o['reason']}".rstrip())
        if r["one_to_add"]:
            out.append(f"  One to add: {r['one_to_add']}")
    return "\n".join(out)


def run_recall(path: Path, data: dict, only=None) -> int:
    """Run a recall set: import its made-up cards into a temporary database, check every case with the real
    recall model, and print and save the results."""
    settings = get_settings()
    problems = check_recall_file(path, data)
    if problems:
        print(f"{path}: {len(problems)} problem(s):\n" + "\n".join(f"  - {p}" for p in problems), file=sys.stderr)
        return 1
    cases = [c for c in data["cases"] if not only or c["id"] == only]
    if not cases:
        print("No cases to check.", file=sys.stderr)
        return 1
    live = app_settings.DEFAULTS   # the box thresholds a new install uses (the parent's saved ones are not read)

    rows, usage, stopped = [], {"input_tokens": 0, "output_tokens": 0,
                                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}, None
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "recall_calibration.db"
        init_db(db_path)
        conn = connect(db_path)
        try:
            import_cards(conn, data["cards"], {c["id"]: f"calibration:{i:04d}" for i, c in enumerate(data["cards"])})
            for i, case in enumerate(cases, 1):
                print(f"[{i}/{len(cases)}] {case['id']} ... ", end="", flush=True)
                try:
                    check = revise.typed_check(conn, get_card(conn, case["card"]), case["answer"])
                except revise.CheckFailed as e:   # also a fatal error (bad key, no credit): it would repeat
                    print("stopped")
                    stopped = f"Stopped at {case['id']}: {e}"
                    break
                for k, v in (check["result"]["usage"] or {}).items():
                    usage[k] = usage.get(k, 0) + v
                row = judge_recall(case, check, live)
                rows.append(row)
                print("ok" if row["ok"] else "DIFFERS")
        finally:
            conn.close()

    if stopped:
        print(f"\n{stopped}\nThis error would repeat for later cases too "
              "(check the API key, model names and effort settings in .env).", file=sys.stderr)
        if not rows:
            return 2
    passed = sum(r["ok"] for r in rows)
    models = sorted({r["model"] for r in rows if r["model"]})
    cost = pricing.estimate_usd(models[0], usage) if len(models) == 1 else None
    print()
    print(format_recall_table(rows))
    print()
    print(format_recall_failures(rows))
    print()
    print(f"Cases: {len(cases)}  checked: {len(rows)}  as expected: {passed}  differed: {len(rows) - passed}")
    print(f"Tokens: input {usage['input_tokens']}, output {usage['output_tokens']}, "
          f"cache write {usage['cache_creation_input_tokens']}, cache read {usage['cache_read_input_tokens']}"
          + (f"  (about ${cost:.2f})" if cost is not None else ""))
    print(f"Models: {', '.join(models) or '-'}  effort: {settings.recall_effort or 'not sent'}  "
          f"box moves: up at {live['recall_up_pct']}%, down under {live['recall_down_pct']}%")

    results_dir = path.parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / f"{path.stem}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    out_path.write_text(json.dumps({
        "calibration_set": data.get("calibration_set"), "file": str(path),
        "run": {"finished_at": utc_now(), "recall_model": settings.recall_model, "recall_effort": settings.recall_effort,
                "stopped": stopped},
        "summary": {"cases": len(cases), "checked": len(rows), "as_expected": passed, "usage": usage},
        "cases": rows,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull results saved to {out_path}")
    return 2 if stopped else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Mark every test answer in a calibration file.")
    parser.add_argument("file", help="calibration JSON file, e.g. data/calibration/calibration_set_v1.json "
                                     "or data/calibration/recall_set_v1.json")
    parser.add_argument("--only", metavar="ID", help="mark only this question's answers (a recall set: this case)")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.api_key_set:
        print("ANTHROPIC_API_KEY is not set in .env. Nothing was marked.", file=sys.stderr)
        return 2

    path = Path(args.file)
    if is_recall_file(path):
        return run_recall(path, json.loads(path.read_text(encoding="utf-8")), args.only)
    try:
        data = load_file(path)
    except QuestionFileError as e:
        print(e, file=sys.stderr)
        return 1

    init_db()
    conn = connect()
    started = utc_now()
    try:
        counts = import_questions(conn, data["questions"])
        print(f"Imported questions: {counts['added']} added, {counts['updated']} updated, "
              f"{counts['unchanged']} unchanged")

        questions = [q for q in data["questions"] if not args.only or q["id"] == args.only]
        cases = [(q, a) for q in questions for a in q.get("answers", [])]
        if not cases:
            print("No test answers to mark.", file=sys.stderr)
            return 1

        rows, records = [], []
        usage = {"input_tokens": 0, "output_tokens": 0,
                 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        stopped = None
        for i, (q, a) in enumerate(cases, 1):
            case = f"{q['id']}/{a['answer_id']}"
            print(f"[{i}/{len(cases)}] {case} ... ", end="", flush=True)
            try:
                result = mark_answer(conn, stored_record(q), a["text"], purpose="calibration")
            except LLMError as e:
                print("stopped")
                stopped = f"Stopped at {case}: {e}"
                break
            if result["usage"]:
                for k in usage:
                    usage[k] += result["usage"][k]
            row = build_row(q, a, result)
            rows.append(row)
            records.append({**row, "answer_text": a["text"], "result": result})
            print("failed" if result["marks_awarded"] is None
                  else f"{result['marks_awarded']}/{result['max_marks']} (expected {a['expected_marks']})")
    finally:
        conn.close()

    if stopped:
        print(f"\n{stopped}\nThis error would repeat for later cases too "
              "(check the API key, model names and effort settings in .env).", file=sys.stderr)
        if not rows:
            return 2

    summary = summarise(rows, usage)
    models = sorted({r["model"] for r in rows if r["model"]})
    print()
    print(format_table(rows))
    print()
    print(format_differences(records))
    print()
    print(format_summary(summary, models,
                         f"marking {settings.marking_effort or 'not sent'}, light {settings.light_effort or 'not sent'}"))

    results_dir = path.parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = results_dir / f"{path.stem}-{stamp}.json"
    out_path.write_text(json.dumps({
        "calibration_set": data.get("calibration_set"),
        "file": str(path),
        "run": {
            "started_at": started, "finished_at": utc_now(),
            "marking_model": settings.marking_model, "light_model": settings.light_model,
            "marking_effort": settings.marking_effort, "light_effort": settings.light_effort,
            "refusal_fallback": settings.refusal_fallback,
            "stopped": stopped,
        },
        "summary": summary,
        "cases": records,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull results saved to {out_path}")
    return 2 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
