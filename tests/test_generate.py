"""Question generation from approved cards (docs/GENERATION_SPEC.md): the mix, the code-side checks,
staging, the review page, regenerate, stale flags, and confidence. Made-up cards, a fake model reply,
no API calls."""

import copy
import json

import pytest

from app import app_settings
from app.generate import plan, run as generate_run, store
from app.importer import import_questions
from app.ingest import staging, summary
from app.ingest.reassemble import main as reassemble_main
from app.marking import marker, rules
from app.revision_cards import import_cards
from conftest import ONE_MARK_QUESTION, PARENT_PIN
from revise_helpers import CLIMATE, COMPARE, EVENTS, FLOODS, MAP, RAIN, add_cards, make_card, point

GLOSSARY = plan.load_command_words()
INFO = {"api_call_id": None, "model": "claude-opus-5-5",
        "usage": {"input_tokens": 1500, "output_tokens": 450, "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": 0}}

RAIN_P1 = "Water evaporates from the sea."
RAIN_P2 = "Water vapour cools and condenses into clouds."
RAIN_P3 = "Droplets join and fall as rain."
CLIMATE_DEF = "Climate is the average weather of a place over a long period."


def mp(text, terms=()):
    return {"text": text, "key_terms": list(terms)}


def q(slot, word, marks, question, points, context="", full="A full answer."):
    return {"slot": slot, "command_word": word, "context": context, "question": question, "marks": marks,
            "mark_points": points, "full_marks_answer": full}


RAIN_REPLY = {"questions": [
    q(1, "name", 1, "Name the process that turns sea water into water vapour.", [mp(RAIN_P1, ["evaporates"])]),
    q(2, "explain", 2, "Explain how clouds produce rain.",
      [mp(RAIN_P1, ["evaporates"]), mp(RAIN_P2, ["condenses"]), mp(RAIN_P3, ["droplets"])]),
]}
CLIMATE_REPLY = {"questions": [
    q(1, "define", 1, "Define the term climate.", [mp(CLIMATE_DEF, ["average weather"])]),
    q(2, "describe", 2, "Describe the climate of a named place you have studied.",
      [mp(CLIMATE_DEF, ["average weather"]), mp("Singapore is hot and wet all year.")]),
]}
OTHER_CARD = make_card("card-other", "Define weather.", {"format": "bullets", "points": [
    point("Weather is the state of the air at one time and place.", ["state of the air"])]},
    topic="geo-other")
OTHER_REPLY = {"questions": [q(1, "define", 1, "Define the term weather.",
                               [mp("Weather is the state of the air at one time and place.", ["state of the air"])])]}


@pytest.fixture
def api(monkeypatch):
    """Replace the model call. api.replies = {text found in the card prompt: reply}; api.calls = request texts."""
    class Api:
        pass

    a = Api()
    a.replies, a.calls = {}, []

    def fake(conn, **kwargs):
        text = kwargs["content"][1]["text"]
        a.calls.append(text)
        for needle, reply in a.replies.items():
            if f'"prompt": "{needle}"' in text:
                return copy.deepcopy(reply), INFO
        raise AssertionError("unexpected card in request: " + text[:120])

    monkeypatch.setattr("app.generate.run.structured_call", fake)
    return a


def generate(subject="geography", **kwargs):
    lines = []
    code = generate_run.run(subject, client=object(), out=lines.append, **kwargs)
    return code, lines


def staged(conn, status=None) -> list:
    sql = "SELECT * FROM staged_items WHERE kind = 'question'" + (f" AND status = '{status}'" if status else "")
    return conn.execute(sql + " ORDER BY id").fetchall()


def login(client):
    client.post("/parent/login", data={"pin": PARENT_PIN, "next": "/"}, follow_redirects=False)


def big_card(n=5, **extra):
    return make_card("card-big", "Describe the water cycle.", {"format": "bullets", "points": [
        point(f"Step {i} happens in the water cycle.", [f"step{i}"]) for i in range(1, n + 1)]}, **extra)


# ---- Command words come from docs/command_words.yaml ----------------------------------------------

def test_the_command_word_list_is_read_from_the_yaml_file():
    assert GLOSSARY["state"]["marks"] == (1, 2) and "history" in GLOSSARY["state"]["use"]
    assert GLOSSARY["distinguish"]["use"] == ["science"]


# ---- Mix by card size ---------------------------------------------------------------------------

def kinds(card):
    return [(s["kind"], s["marks_min"], s["marks_max"]) for s in plan.plan_slots(card, plan.card_units(card))]


def test_mix_by_card_size():
    one = make_card("c1", "Define x.", {"format": "bullets", "points": [point("X is a thing.")]})
    two = make_card("c2", "State two.", {"format": "bullets", "points": [point("One."), point("Two.")]})
    assert kinds(one) == [("recall", 1, 1)]
    assert kinds(two) == [("recall", 1, 1), ("multi", 2, 2)]
    assert kinds(RAIN) == [("recall", 1, 1), ("multi", 2, 3)]  # sub-points are not counted as points
    assert kinds(big_card(4)) == [("recall", 1, 1), ("multi", 3, 4)]
    assert kinds(big_card(8)) == [("recall", 1, 1), ("multi", 3, 4)]


def test_an_examples_block_adds_a_using_an_example_question_up_to_the_cap_of_three():
    assert kinds(CLIMATE) == [("recall", 1, 1), ("example", 2, 3)]
    with_examples = big_card(5, examples=["A made-up example."])
    slots = kinds(with_examples)
    assert slots == [("recall", 1, 1), ("multi", 3, 4), ("example", 2, 3)] and len(slots) <= plan.MAX_PER_CARD


def test_a_grouped_card_counts_each_heading_as_a_point():
    units = plan.card_units(FLOODS)
    assert plan.point_count(units) == 2  # Damage, Health
    assert [t for t in plan.card_units(FLOODS) if not t["top"]]  # the points under the headings are still usable
    assert kinds(FLOODS) == [("recall", 1, 1), ("multi", 2, 2)]


def test_history_questions_stay_within_three_marks():
    history = big_card(5, subject="history")
    assert kinds(history) == [("recall", 1, 1), ("multi", 3, 3)]


def test_image_and_table_cards_and_cards_without_points_are_skipped():
    for card in (MAP, COMPARE):
        assert plan.skip_reason(card, plan.card_units(card))
    empty = make_card("c0", "Define x.", {"format": "bullets", "points": []})
    assert plan.skip_reason(empty, plan.card_units(empty)) == "no usable point"
    assert plan.skip_reason(RAIN, plan.card_units(RAIN)) == ""
    assert plan.skip_reason(EVENTS, plan.card_units(EVENTS)) == ""


# ---- Code-side checks -----------------------------------------------------------------------------

def check(item, card=RAIN, slot=None, subject="geography"):
    units = plan.card_units(card)
    slot = slot or plan.plan_slots(card, units)[1]
    return plan.check_question(item, slot, units, GLOSSARY, subject)


def test_a_mark_point_that_is_not_on_the_card_is_dropped_and_the_rest_kept():
    item = q(2, "explain", 2, "Explain how rain forms.",
             [mp(RAIN_P1), mp(RAIN_P2), mp("The moon pulls the clouds towards the ground.")])
    fields, warnings = check(item)
    assert [p["text"] for p in fields["mark_points"]] == [RAIN_P1, RAIN_P2]
    assert [p["id"] for p in fields["mark_points"]] == ["MP1", "MP2"]
    assert any("not on the card" in w and "moon" in w for w in warnings)
    assert fields["choose"] == 0


def test_a_question_whose_marks_exceed_its_remaining_points_is_dropped():
    item = q(2, "explain", 3, "Explain how rain forms.", [mp(RAIN_P1), mp("The moon makes the rain fall.")])
    fields, warnings = check(item)
    assert fields is None
    assert any("only 1 mark point" in w for w in warnings)
    fields, warnings = check(q(2, "explain", 2, "Explain.", [mp("Nothing here is on the card at all.")]))
    assert fields is None and any("none of its mark points" in w for w in warnings)


def test_any_n_is_set_when_the_card_has_more_points_than_marks():
    fields, _ = check(q(2, "explain", 2, "Explain how rain forms.", [mp(RAIN_P1), mp(RAIN_P2), mp(RAIN_P3)]))
    assert fields["marks"] == 2 and fields["choose"] == 2
    fields, _ = check(q(2, "explain", 3, "Explain how rain forms.", [mp(RAIN_P1), mp(RAIN_P2), mp(RAIN_P3)]))
    assert fields["choose"] == 0  # marks equal the points: no 'any N'
    big = big_card(5)
    points = [mp(f"Step {i} happens in the water cycle.", [f"step{i}"]) for i in range(1, 6)]
    fields, _ = check(q(2, "describe", 3, "Describe the water cycle.", points), card=big)
    assert fields["choose"] == 3 and len(fields["mark_points"]) == 5


def test_key_terms_come_from_the_card():
    fields, _ = check(q(2, "explain", 2, "Explain.", [mp(RAIN_P1, ["evaporates", "invented term"]), mp(RAIN_P2, [])]))
    assert fields["mark_points"][0]["key_terms"] == ["evaporates"]
    assert fields["mark_points"][1]["key_terms"] == ["condenses"]  # none given: the card point's own


@pytest.mark.parametrize("word, marks, subject, expected", [
    ("explain", 2, "geography", None),
    ("explain", 1, "geography", "suits 2-4 marks, not 1"),
    ("state", 4, "geography", "suits 1-2 marks, not 4"),
    ("outline", 2, "history", "not used for history"),
    ("calculate", 2, "geography", "not in the command word list"),
])
def test_command_word_must_suit_the_marks_using_the_yaml_ranges(word, marks, subject, expected):
    card = big_card(5, subject=subject)
    points = [mp(f"Step {i} happens in the water cycle.") for i in range(1, 6)]
    slot = {"slot": 2, "kind": "multi", "marks_min": 1, "marks_max": 4}
    _, warnings = plan.check_question(q(2, word, marks, "Do the thing.", points), slot, plan.card_units(card),
                                      GLOSSARY, subject)
    if expected is None:
        assert warnings == []
    else:
        assert any(expected in w for w in warnings), warnings


def test_marks_outside_the_planned_range_are_noted():
    points = [mp(f"Step {i} happens in the water cycle.") for i in range(1, 6)]
    _, warnings = check(q(2, "explain", 2, "Explain.", points), card=big_card(5))  # the slot plans 3-4 marks
    assert any("differ from the 3-4 planned" in w for w in warnings)
    fields, warnings = check(q(2, "explain", 4, "Explain.", [mp(RAIN_P1), mp(RAIN_P2), mp(RAIN_P3)]))
    assert fields is None  # 4 marks but only 3 points on the card: dropped, not just noted


def test_near_duplicates_and_answer_giveaways_are_found():
    existing = [("q-old", plan.norm("Explain how clouds produce rain."))]
    assert plan.find_duplicate("Explain how clouds produce rain?", existing) == "q-old"
    assert plan.find_duplicate("Name the largest ocean on Earth.", existing) == ""
    points = [{"text": RAIN_P1, "key_terms": []}]
    assert plan.answer_given_away("", "Why does water evaporate from the sea? Water evaporates from the sea.",
                                  points) == RAIN_P1
    assert plan.answer_given_away("", "Name the process that turns sea water into water vapour.", points) == ""
    assert plan.answer_given_away("", "Define climate.", [{"text": "Climate", "key_terms": []}]) == ""  # short point


# ---- Dry run ----------------------------------------------------------------------------------------

def test_dry_run_lists_the_plan_and_makes_no_calls_and_no_changes(bank, monkeypatch):
    add_cards(bank, [CLIMATE, RAIN, MAP, COMPARE])

    def no_calls(*a, **k):
        raise AssertionError("dry run called the API")

    monkeypatch.setattr("app.generate.run.structured_call", no_calls)
    lines = []
    code = generate_run.run("geography", dry_run=True, out=lines.append)  # no client, no API key needed
    text = "\n".join(lines)
    assert code == 0
    assert "card-rain" in text and "q1 recall 1 mark(s)" in text and "q2 multi 2-3 mark(s)" in text
    assert "card-map: skipped, image card" in text and "card-compare: skipped, table card" in text
    assert "2 card(s) would be sent" in text and "No API calls were made" in text
    assert staged(bank) == [] and bank.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0


# ---- Generating and staging ------------------------------------------------------------------------

def test_questions_are_staged_pending_with_stable_ids_and_origin(bank, api):
    add_cards(bank, [RAIN, MAP])
    api.replies = {"Explain how rain forms.": RAIN_REPLY}
    code, lines = generate()
    assert code == 0 and len(api.calls) == 1  # the image card is not sent
    rows = staged(bank)
    assert [r["item_id"] for r in rows] == ["gen-card-rain-q1", "gen-card-rain-q2"]
    assert {r["status"] for r in rows} == {"pending"}
    assert all(json.loads(r["warnings_json"]) == [] for r in rows)
    record = json.loads(rows[1]["record_json"])
    assert record["source"] == "generated" and record["marking_mode"] == "points" and record["choose"] == 2
    assert record["origin_card_id"] == "card-rain" and record["origin_card_version"] == 1
    assert record["topic"] == "geo-intro" and record["full_marks_answer"] == "A full answer."
    assert any("Done: 2 question(s) staged" in line for line in lines)
    # Nothing reaches her until approved.
    assert bank.execute("SELECT COUNT(*) FROM questions WHERE source = 'generated'").fetchone()[0] == 0


def test_running_again_does_not_duplicate_and_makes_no_call(bank, api):
    add_cards(bank, [RAIN])
    api.replies = {"Explain how rain forms.": RAIN_REPLY}
    generate()
    generate()
    assert len(api.calls) == 1 and len(staged(bank)) == 2


def test_a_rejected_question_still_counts_as_done_for_its_card_version(bank, api):
    add_cards(bank, [RAIN])
    api.replies = {"Explain how rain forms.": RAIN_REPLY}
    generate()
    for row in staged(bank):
        staging.reject(bank, row["id"])
    generate()
    assert len(api.calls) == 1  # only the Regenerate button asks again


def test_topic_level_and_limit_options(bank, api):
    add_cards(bank, [CLIMATE, RAIN, OTHER_CARD])
    api.replies = {"Define climate.": CLIMATE_REPLY, "Explain how rain forms.": RAIN_REPLY,
                   "Define weather.": OTHER_REPLY}
    generate(topic="geo-other")
    assert len(api.calls) == 1 and "Define weather." in api.calls[0]
    generate(limit=1)
    assert len(api.calls) == 2 and "Define climate." in api.calls[1]
    generate(level="sec2")
    assert len(api.calls) == 2


def test_a_dropped_question_is_reported_and_not_staged(bank, api):
    add_cards(bank, [RAIN])
    bad = copy.deepcopy(RAIN_REPLY)
    bad["questions"][1]["marks"] = 3
    bad["questions"][1]["mark_points"] = bad["questions"][1]["mark_points"][:1] + [mp("The moon pulls the clouds.")]
    api.replies = {"Explain how rain forms.": bad}
    _, lines = generate()
    assert [r["item_id"] for r in staged(bank)] == ["gen-card-rain-q1"]
    assert any(line.startswith("  dropped q2:") for line in lines)


def test_warnings_are_stored_for_duplicates_and_giveaways(bank, api):
    add_cards(bank, [RAIN])
    leaky = copy.deepcopy(RAIN_REPLY)
    leaky["questions"][0]["question"] = "Why does water evaporates from the sea? Water evaporates from the sea."
    api.replies = {"Explain how rain forms.": leaky}
    import_questions(bank, [{"id": "old-q", "subject": "geography", "level": "sec1", "topic": "geo-intro",
                             "source": "school", "question": "Explain how clouds produce rain!", "marks": 2,
                             "marking_mode": "points", "mark_points": [{"id": "MP1", "text": "one"},
                                                                       {"id": "MP2", "text": "two"}]}])
    generate()
    warnings = {r["item_id"]: json.loads(r["warnings_json"]) for r in staged(bank)}
    assert any(w.startswith(plan.GIVES_AWAY_FLAG) for w in warnings["gen-card-rain-q1"])
    assert any(w == f"{plan.POSSIBLE_DUPLICATE} old-q" for w in warnings["gen-card-rain-q2"])


# ---- Review page: approve, origin card, stale, regenerate ------------------------------------------------

def generated_file(conn) -> int:
    return conn.execute("SELECT id FROM source_files WHERE material_type = 'generated'").fetchone()["id"]


def test_review_page_shows_the_generated_filter_the_question_and_the_origin_card(client, bank, api):
    add_cards(bank, [RAIN])
    api.replies = {"Explain how rain forms.": RAIN_REPLY}
    generate()
    login(client)
    assert "Generated questions (geography, sec1)" in client.get("/review?filter=generated").text
    item = staged(bank)[1]
    html = client.get(f"/review/items/{item['id']}").text
    assert "Explain how clouds produce rain." in html and "Full-marks answer" in html and "A full answer." in html
    assert "Made from card card-rain" in html and RAIN_P1 in html  # the origin card, beside the question
    assert "Regenerate this card" in html and "Stale" not in html


def test_generated_questions_need_the_pin_to_regenerate_or_bulk_approve(client, bank, api):
    add_cards(bank, [RAIN])
    api.replies = {"Explain how rain forms.": RAIN_REPLY}
    generate()
    item, file_id = staged(bank)[0], generated_file(bank)
    assert client.post(f"/review/items/{item['id']}/regenerate").status_code == 403
    assert client.post(f"/review/files/{file_id}/approve-clean-topic", data={"topic": "geo-intro"}).status_code == 403


def test_approving_writes_the_question_to_the_bank_as_generated(client, bank, api):
    add_cards(bank, [RAIN])
    api.replies = {"Explain how rain forms.": RAIN_REPLY}
    generate()
    login(client)
    item = staged(bank)[1]
    assert client.post(f"/review/items/{item['id']}/approve", follow_redirects=False).status_code == 303
    row = bank.execute("SELECT * FROM questions WHERE id = 'gen-card-rain-q2'").fetchone()
    assert row["source"] == "generated" and row["marks"] == 2 and row["topic"] == "geo-intro"


def test_approve_all_without_warnings_for_a_topic(client, bank, api):
    add_cards(bank, [RAIN, OTHER_CARD])
    leaky = copy.deepcopy(RAIN_REPLY)
    leaky["questions"][0]["command_word"] = "explain"  # 1 mark with 'explain': a warning on q1
    api.replies = {"Explain how rain forms.": leaky, "Define weather.": OTHER_REPLY}
    generate()
    login(client)
    r = client.post(f"/review/files/{generated_file(bank)}/approve-clean-topic", data={"topic": "geo-intro"})
    assert r.status_code == 200 and "Approved 1 item(s) without warnings in geo-intro." in r.text
    status = {x["item_id"]: x["status"] for x in staged(bank)}
    assert status == {"gen-card-rain-q1": "pending",  # has a warning
                      "gen-card-rain-q2": "approved", "gen-card-other-q1": "pending"}  # other topic untouched


def test_a_changed_card_marks_its_generated_questions_stale(client, bank, api):
    add_cards(bank, [RAIN])
    api.replies = {"Explain how rain forms.": RAIN_REPLY}
    generate()
    login(client)
    first, second = staged(bank)
    client.post(f"/review/items/{first['id']}/approve")
    changed = copy.deepcopy(RAIN)
    changed["answer"]["points"][0]["text"] = "Sea water is heated by the sun and evaporates."
    assert import_cards(bank, [changed])["updated"] == 1
    page = client.get(f"/review/files/{generated_file(bank)}").text
    assert page.count("stale") == 2  # one pending, one approved: both still usable
    assert "Stale: this card has changed" in client.get(f"/review/items/{second['id']}").text
    # The card is now due again for generation at its new version.
    todo, _ = generate_run.cards_to_generate(bank, "geography")
    assert [t["card"]["id"] for t in todo] == ["card-rain"]


def test_regenerate_replaces_only_pending_questions_and_keeps_approved_ones(client, bank, api):
    add_cards(bank, [RAIN])
    api.replies = {"Explain how rain forms.": RAIN_REPLY}
    generate()
    login(client)
    first, second = staged(bank)
    client.post(f"/review/items/{first['id']}/approve")
    again = copy.deepcopy(RAIN_REPLY)  # also answers slot 1, which was not asked for: ignored
    again["questions"][1]["question"] = "Explain, step by step, how clouds produce rain."
    api.replies = {"Explain how rain forms.": again}
    r = client.post(f"/review/items/{second['id']}/regenerate")
    assert r.status_code == 200 and "Regenerated card-rain: 1 new pending question(s)." in r.text
    assert "slot 1:" not in api.calls[1] and "slot 2:" in api.calls[1]
    rows = {x["id"]: x["status"] for x in bank.execute("SELECT id, status FROM staged_items")}
    assert rows[first["id"]] == "approved" and rows[second["id"]] == "superseded"
    pending = staged(bank, "pending")
    assert len(pending) == 1 and pending[0]["item_id"] == "gen-card-rain-q2"
    assert "step by step" in json.loads(pending[0]["record_json"])["question"]
    assert bank.execute("SELECT COUNT(*) FROM questions WHERE id = 'gen-card-rain-q1'").fetchone()[0] == 1


# ---- The synthetic source is invisible to ingestion --------------------------------------------------------

def test_ingestion_summary_reassemble_and_matching_leave_the_generated_source_alone(bank, api, capsys):
    add_cards(bank, [RAIN])
    api.replies = {"Explain how rain forms.": RAIN_REPLY}
    generate()
    before = [(r["id"], r["status"], r["record_json"], r["warnings_json"]) for r in staged(bank)]
    file = bank.execute("SELECT * FROM source_files WHERE material_type = 'generated'").fetchone()
    source = bank.execute("SELECT * FROM sources WHERE id = ?", (file["source_id"],)).fetchone()
    assert source["path"] == "generated:geography:sec1"

    assert summary.current_files(bank, source["id"]) == []  # not in ingestion summaries
    staging.assemble_file(bank, file, None, set())
    assert staging.match_source(bank, source["id"], None, set()) == {"matched": 0, "unmatched": 0, "orphans": 0}
    lines = []
    assert reassemble_main([source["path"], "--apply"], out=lines.append) == 1  # not a source folder
    assert summary.main([source["path"]], out=lines.append) == 1  # nothing to summarise
    after = [(r["id"], r["status"], r["record_json"], r["warnings_json"]) for r in staged(bank)]
    assert after == before
    assert not any("gen-card" in line or "Generated" in line for line in lines)


# ---- Confidence --------------------------------------------------------------------------------------------

def test_existing_sources_keep_their_confidence_whatever_the_setting():
    for source, base in rules.BASE_CONFIDENCE.items():
        if source == "generated":
            continue
        for setting in (None, "low", "medium", "high"):
            assert rules.confidence(source, [], setting)[0] == base
            assert rules.confidence(source, ["borderline"], setting) == rules.confidence(source, ["borderline"])
    assert rules.confidence("unknown source", [], "high")[0] == "low"


def test_generated_questions_are_low_unless_the_setting_is_passed():
    assert rules.confidence("generated", [])[0] == "low"  # as before
    label, reason = rules.confidence("generated", [], "medium")
    assert label == "medium" and "approved by a parent" in reason
    assert rules.confidence("generated", ["borderline"], "medium")[0] == "low"  # still drops one step
    assert rules.confidence("generated", [], "high")[0] == "high"


def _answer_with(monkeypatch):
    output = {"points": [{"id": "MP1", "awarded": True, "evidence_quote": "diffusion", "reason": "r",
                          "reversed": False}], "missing_key_terms": [], "filler_quotes": [],
              "misread_question": False, "borderline": False, "borderline_reason": "",
              "full_marks_answer": "Diffusion", "technique_note": "n"}
    monkeypatch.setattr("app.marking.marker.structured_call", lambda conn, **kw: (output, INFO))
    return marker


def test_the_marker_gives_approved_generated_questions_the_setting_and_other_sources_nothing_new(bank, monkeypatch):
    m = _answer_with(monkeypatch)
    generated = {**ONE_MARK_QUESTION, "id": "gen-x-q1", "source": "generated"}
    assert app_settings.get_all(bank)["generated_approved_confidence"] == "medium"
    assert m.mark_answer(bank, generated, "It is diffusion.")["confidence"] == "medium"
    assert m.mark_answer(bank, ONE_MARK_QUESTION, "It is diffusion.")["confidence"] == "high"
    assert app_settings.save(bank, {**app_settings.get_all(bank), "generated_approved_confidence": "low"}) == []
    assert m.mark_answer(bank, generated, "It is diffusion.")["confidence"] == "low"
    assert m.mark_answer(bank, ONE_MARK_QUESTION, "It is diffusion.")["confidence"] == "high"
    school = {**ONE_MARK_QUESTION, "source": "school"}
    assert m.mark_answer(bank, school, "It is diffusion.")["confidence"] == "medium"


def test_the_setting_is_validated_and_can_be_changed_on_the_settings_page(client, bank):
    login(client)
    form = {"cards_per_set": "4", "recall_method": "type", "recall_timing": "after_set",
            "interval_1": "1", "interval_2": "2", "interval_3": "4", "interval_4": "8", "interval_5": "16",
            "known_threshold": "80"}
    assert 'name="generated_approved_confidence"' in client.get("/parent/settings").text
    r = client.post("/parent/settings", data={**form, "generated_approved_confidence": "high"})
    assert r.status_code == 200 and app_settings.get_all(bank)["generated_approved_confidence"] == "high"
    r = client.post("/parent/settings", data={**form, "generated_approved_confidence": "certain"})
    assert r.status_code == 422 and app_settings.get_all(bank)["generated_approved_confidence"] == "high"
    client.post("/parent/settings", data=form)  # a form without the field keeps the default
    assert app_settings.get_all(bank)["generated_approved_confidence"] == "medium"


def test_store_helpers_find_a_cards_items(bank, api):
    add_cards(bank, [RAIN])
    api.replies = {"Explain how rain forms.": RAIN_REPLY}
    generate()
    items = store.card_items(bank)["card-rain"]
    assert [(i["slot"], i["version"], i["status"]) for i in items] == [(1, 1, "pending"), (2, 1, "pending")]
    assert store.has_questions_for_version(items, 1) and not store.has_questions_for_version(items, 2)
