"""Calibration runner (MARKING_SPEC.md section 10).

    python -m app.calibrate data/calibration/calibration_set_v1.json

Imports the file's questions, marks every test answer, prints expected against
awarded marks with flags and a summary, and saves full results as JSON in
<file's folder>/results/. Test answers are not stored as attempts.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.db import connect, init_db, utc_now
from app.importer import QuestionFileError, import_questions, load_file, stored_record
from app.llm import LLMError
from app.marking.marker import mark_answer
from app.marking.rules import level_for_marks

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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Mark every test answer in a calibration file.")
    parser.add_argument("file", help="calibration JSON file, e.g. data/calibration/calibration_set_v1.json")
    parser.add_argument("--only", metavar="QUESTION_ID", help="mark only this question's answers")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.api_key_set:
        print("ANTHROPIC_API_KEY is not set in .env. Nothing was marked.", file=sys.stderr)
        return 2

    path = Path(args.file)
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
