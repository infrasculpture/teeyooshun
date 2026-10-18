"""Generate practice questions from approved revision cards and stage them for review
(docs/GENERATION_SPEC.md). One API call per card, on MARKING_MODEL. Called by the command line
(app/generate/__main__.py) and by the Regenerate button on the review page."""

from app.config import get_settings
from app.db import connect, init_db
from app.generate import plan, prompts, store
from app.llm import LLMError, structured_call
from app.pricing import estimate_usd
from app.revision_cards import get_card

# Rough per-card estimate for --dry-run (spec section 10: 1-2k input tokens, mostly cached, 300-600 output).
EST_INPUT_TOKENS, EST_OUTPUT_TOKENS = 1500, 450


def _fmt_cost(usd) -> str:
    return "unknown" if usd is None else f"US${usd:.3f}"


def approved_cards(conn, subject: str, topic=None, level=None) -> list[dict]:
    """Approved revision cards (current version), in book order."""
    sql, args = "SELECT id FROM revision_cards WHERE subject = ?", [subject]
    if topic:
        sql, args = sql + " AND topic = ?", args + [topic]
    if level:
        sql, args = sql + " AND level = ?", args + [level]
    return [get_card(conn, r["id"]) for r in conn.execute(sql + " ORDER BY sort_key, id", args)]


def cards_to_generate(conn, subject: str, topic=None, level=None, limit=None):
    """(todo, skipped). todo: [{"card", "units", "slots"}] for cards with no generated question yet for
    their current version; skipped: [(card id, reason)] for cards that cannot be used."""
    items = store.card_items(conn)
    todo, skipped = [], []
    for card in approved_cards(conn, subject, topic, level):
        units = plan.card_units(card)
        reason = plan.skip_reason(card, units)
        if reason:
            skipped.append((card["id"], reason))
        elif not store.has_questions_for_version(items.get(card["id"], []), card["version"]):
            todo.append({"card": card, "units": units, "slots": plan.plan_slots(card, units)})
            if limit and len(todo) >= limit:
                break
    return todo, skipped


def build_record(card: dict, slot: int, fields: dict) -> dict:
    """The question record (Marking spec section 2) plus where it came from."""
    record = {
        "id": f"gen-{card['id']}-q{slot}", "subject": card["subject"], "level": card["level"],
        "topic": card["topic"], "source": "generated",
        "source_ref": f"Generated from card {card['id']} (version {card['version']})",
        "context": fields["context"], "question": fields["question"], "marks": fields["marks"],
        "command_word": fields["command_word"], "marking_mode": "points", "mark_points": fields["mark_points"],
    }
    if fields["choose"]:
        record["choose"] = fields["choose"]
    record.update({"full_marks_answer": fields["full_marks_answer"], "origin_card_id": card["id"],
                   "origin_card_version": card["version"]})
    return record


def generate_card(conn, card: dict, *, glossary: dict, settings, client=None, replace=False) -> dict:
    """Ask the model for the card's questions, check them, and stage what passes as pending.

    replace: a regenerate. The card's pending questions are set aside once the new reply arrives; approved
    questions stay, and a slot already approved for this card version is not asked for again.
    Returns {"staged", "dropped", "usage", "cost", "model"}; raises LLMError if the call fails (nothing changes)."""
    units = plan.card_units(card)
    slots = plan.plan_slots(card, units)
    approved_slots = {i["slot"] for i in store.card_items(conn).get(card["id"], [])
                      if i["status"] == "approved" and i["version"] == card["version"]}
    slots = [s for s in slots if s["slot"] not in approved_slots]
    if not slots:
        return {"staged": 0, "dropped": [], "usage": None, "cost": None, "model": ""}

    examples = prompts.style_examples(conn, card["subject"], card["topic"])
    system, content = prompts.build_request(card, card["subject"], glossary, examples, slots)
    data, info = structured_call(
        conn, model=settings.marking_model, effort=settings.marking_effort, system=system, content=content,
        schema=prompts.SCHEMA, purpose="generation", fallback=settings.refusal_fallback, client=client,
        max_tokens=4000)

    if replace:
        store.supersede_pending(conn, card["id"])
    file_id = store.generated_file_id(conn, card["subject"], card["level"])
    existing = store.existing_questions(conn, card["topic"], card["id"])
    wanted, seen, staged, dropped = {s["slot"]: s for s in slots}, set(), 0, []
    for item in data["questions"]:
        slot = wanted.get(item["slot"])
        if slot is None or slot["slot"] in seen:
            continue
        seen.add(slot["slot"])
        fields, warnings = plan.check_question(item, slot, units, glossary, card["subject"])
        if fields is None:
            dropped.append(f"q{slot['slot']}: " + "; ".join(warnings))
            continue
        record = build_record(card, slot["slot"], fields)
        if card["topic"] == "unassigned":
            warnings.append("Topic is unassigned")
        duplicate = plan.find_duplicate(record["question"], existing)
        if duplicate:
            warnings.append(f"{plan.POSSIBLE_DUPLICATE} {duplicate}")
        given_away = plan.answer_given_away(record["context"], record["question"], record["mark_points"])
        if given_away:
            warnings.append(f"{plan.GIVES_AWAY_FLAG}: {given_away}")
        existing.append((record["id"], plan.norm(record["question"])))
        store.stage_question(conn, file_id, record, warnings,
                             {"origin_card_id": card["id"], "origin_card_version": card["version"],
                              "slot": slot["slot"], "model": info["model"], "usage": info["usage"],
                              "api_call_id": info["api_call_id"]})
        staged += 1
    conn.commit()
    return {"staged": staged, "dropped": dropped, "usage": info["usage"], "model": info["model"],
            "cost": estimate_usd(info["model"], info["usage"])}


def regenerate_card(conn, card_id: str, client=None) -> dict:
    """The Regenerate button: a new call for one card. Raises LLMError, or ValueError for an unknown card."""
    card = get_card(conn, card_id)
    if card is None:
        raise ValueError(f"No approved card {card_id}")
    return generate_card(conn, card, glossary=plan.load_command_words(), settings=get_settings(), client=client,
                         replace=True)


def _dry_run(todo, skipped, settings, out) -> None:
    per_card = estimate_usd(settings.marking_model, {"input_tokens": EST_INPUT_TOKENS,
                                                     "output_tokens": EST_OUTPUT_TOKENS})
    for t in todo:
        mix = "; ".join(f"q{s['slot']} {s['kind']} "
                        + (f"{s['marks_min']}" if s["marks_min"] == s["marks_max"]
                           else f"{s['marks_min']}-{s['marks_max']}") + " mark(s)" for s in t["slots"])
        out(f"{t['card']['id']}  {t['card']['topic']}  {plan.point_count(t['units'])} point(s): {mix}")
    for card_id, reason in skipped:
        out(f"{card_id}: skipped, {reason}")
    total = None if per_card is None else per_card * len(todo)
    out(f"Dry run: {len(todo)} card(s) would be sent to {settings.marking_model}, {len(skipped)} skipped. "
        f"Estimated cost about {_fmt_cost(total)}. No API calls were made and nothing was changed.")


def run(subject: str, *, topic=None, level=None, limit=None, dry_run=False, client=None, out=print) -> int:
    """Generate for every approved card of the subject that has no generated question for its current version."""
    settings = get_settings()
    try:
        glossary = plan.load_command_words()
    except plan.CommandWordsError as e:
        out(f"command words problem: {e}")
        return 1
    if not dry_run and client is None and not settings.api_key_set:
        out("ANTHROPIC_API_KEY is not set in .env: nothing was generated.")
        return 1
    init_db()
    conn = connect()
    try:
        todo, skipped = cards_to_generate(conn, subject, topic, level, limit)
        if dry_run:
            _dry_run(todo, skipped, settings, out)
            return 0
        totals = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": 0}
        cost, staged, failed = 0.0, 0, 0
        for t in todo:
            card = t["card"]
            try:
                result = generate_card(conn, card, glossary=glossary, settings=settings, client=client)
            except LLMError as e:
                if e.fatal:
                    out(f"{card['id']}: {e}. Stopping: every call would fail the same way.")
                    return 1
                failed += 1
                out(f"{card['id']}: failed ({e.kind}: {e}). Run again to retry.")
                continue
            staged += result["staged"]
            for key in totals:
                totals[key] += (result["usage"] or {}).get(key, 0)
            cost += result["cost"] or 0.0
            out(f"{card['id']}: {result['staged']} staged, {len(result['dropped'])} dropped, "
                f"{(result['usage'] or {}).get('input_tokens', 0)} in / "
                f"{(result['usage'] or {}).get('output_tokens', 0)} out, {_fmt_cost(result['cost'])}")
            for d in result["dropped"]:
                out(f"  dropped {d}")
        out(f"Done: {staged} question(s) staged from {len(todo) - failed} card(s), {failed} failed, "
            f"{len(skipped)} card(s) skipped. {totals['input_tokens']} input "
            f"({totals['cache_read_input_tokens']} cached reads) / {totals['output_tokens']} output tokens, "
            f"about {_fmt_cost(cost)}. Review them at /review (Generated).")
        return 1 if failed else 0
    finally:
        conn.close()
