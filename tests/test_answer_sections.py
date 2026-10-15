"""answer_sections in source.yaml: file pages of the answer key mapped to a unit, overriding the model's
unit guess, and re-applied by reassemble from stored readings with no API calls. Made-up data only."""

import json

import pytest

from app.db import connect
from app.ingest.assemble import stitch_answers
from app.ingest.reassemble import main as reassemble
from app.sources import SourceError, load_source
from ingest_helpers import SOURCE_YAML, a, ingest, make_source, page, q

YAML = SOURCE_YAML.replace('"01": sci-01-scientific-endeavour',
                           '"01": sci-01-scientific-endeavour\n  "02": sci-05-ray-model-of-light')
SECTIONS = 'answer_sections:\n  - {pages: "1-2", unit: "01"}\n  - {pages: 3, unit: "02"}\n'


def _write_yaml(folder, text):
    (folder / "source.yaml").write_text(text, encoding="utf-8")


def _bad(tmp_path, extra):
    folder = tmp_path / "bad"
    folder.mkdir()
    _write_yaml(folder, YAML + extra)
    return folder


# ---- stitch_answers -------------------------------------------------------------------------

def _pages():
    return [
        (1, "", page("answer", chapter="1", answers=[a("1", unit="c01")])),
        (2, "", page("answer", answers=[a("2")])),  # no heading: the model carries on with unit 1
        (3, "", page("answer", answers=[a("1", unit="c01"), a("2")])),  # heading wrongly read as unit 1 too
    ]


def test_sections_override_the_models_unit_on_their_pages_only():
    entries = stitch_answers(_pages(), answer_sections=[(3, 3, "02")])
    assert [(e["unit"], e["qnum"]) for e in entries] == [("01", "1"), ("01", "2"), ("02", "1"), ("02", "2")]


def test_without_sections_the_model_guess_stands():
    assert {e["unit"] for e in stitch_answers(_pages())} == {"01"}


def test_a_section_beats_a_chapter_heading_and_later_pages_follow_it():
    pages = [(1, "", page("answer", chapter="5", answers=[a("1")])),
             (2, "", page("answer", answers=[a("2")]))]
    entries = stitch_answers(pages, answer_sections=[(1, 1, "03")])
    assert [e["unit"] for e in entries] == ["03", "03"]  # page 2 has no section: it continues from the last unit


# ---- source.yaml ----------------------------------------------------------------------------

def test_answer_sections_are_read_from_source_yaml(tmp_path):
    folder = _bad(tmp_path, SECTIONS)
    cfg = load_source(folder)
    assert cfg.answer_sections == [(1, 2, "01"), (3, 3, "02")] and cfg.warnings == []
    _write_yaml(folder, YAML)
    assert load_source(folder).answer_sections == []


@pytest.mark.parametrize("extra, message", [
    ("answer_sections: {pages: '1-2', unit: '01'}\n", "must be a list"),
    ("answer_sections:\n  - {pages: '0-2', unit: '01'}\n", "count from 1"),
    ("answer_sections:\n  - {pages: '5-2', unit: '01'}\n", "count from 1"),
    ("answer_sections:\n  - {pages: 'one', unit: '01'}\n", "needs 'pages'"),
    ("answer_sections:\n  - {pages: '1-2'}\n", "needs 'pages'"),
    ("answer_sections:\n  - {pages: '1-4', unit: '01'}\n  - {pages: '4-6', unit: '02'}\n", "overlap"),
])
def test_bad_answer_sections_stop_the_run(tmp_path, extra, message):
    with pytest.raises(SourceError, match=message):
        load_source(_bad(tmp_path, extra))


def test_a_unit_with_no_units_entry_is_a_warning(tmp_path):
    cfg = load_source(_bad(tmp_path, 'answer_sections:\n  - {pages: "1-2", unit: "07"}\n'))
    assert cfg.answer_sections == [(1, 2, "07")] and any("no 'units' entry for 07" in w for w in cfg.warnings)


# ---- ingest and reassemble ------------------------------------------------------------------

def _book(env, yaml_text):
    return make_source(env, "split-book", yaml_text, {
        "answers.pdf": ["Key 1", "Key 2", "Key 3"],
        "qns_u01.pdf": ["Q1"],
        "qns_u02.pdf": ["Q1"],
    })


RESPONSES = [
    # answers.pdf sorts first. The model read every page as unit 1 (the headings said "Answers", not a number).
    page("answer", chapter="1", answers=[a("1", points=["Unit one point", "x"])]),
    page("answer", answers=[a("2", points=["Unit one Q2", "x"])]),
    page("answer", answers=[a("1", points=["Unit two point", "y"])]),
    page(questions=[q("1")]),
    page(questions=[q("1")]),
]


def _first_point(unit):
    row = connect().execute("SELECT record_json FROM staged_items WHERE item_id LIKE ?",
                            (f"%qns_u{unit}-q1",)).fetchone()
    points = json.loads(row["record_json"]).get("mark_points") or []
    return points[0]["text"] if points else None


def test_reassemble_applies_sections_from_stored_readings_without_api_calls(env):
    folder = _book(env, YAML)
    code, client, _ = ingest(folder, RESPONSES)
    assert code == 0 and len(client.requests) == 5
    assert _first_point("01") == "Unit one point"
    assert _first_point("02") is None  # wrong: the key's unit 2 page was filed under unit 1, so u02 has no answer

    _write_yaml(folder, YAML + SECTIONS)
    lines = []
    assert reassemble([str(folder), "--apply"], out=lines.append) == 0  # FakeClient is not involved: no API
    assert len(client.requests) == 5
    assert _first_point("01") == "Unit one point"
    assert _first_point("02") == "Unit two point"
    units = [r["unit"] for r in connect().execute(
        "SELECT unit FROM key_entries WHERE superseded = 0 ORDER BY id")]
    assert units == ["01", "01", "02"]


def test_reassemble_of_one_question_file_still_rebuilds_the_answer_file(env):
    folder = _book(env, YAML)
    ingest(folder, RESPONSES)
    _write_yaml(folder, YAML + SECTIONS)
    assert reassemble([str(folder), "--file", "qns_u02.pdf", "--apply"], out=lambda _line: None) == 0
    assert _first_point("02") == "Unit two point"


def test_ingest_applies_sections_straight_away(env):
    folder = _book(env, YAML + SECTIONS)
    ingest(folder, RESPONSES)
    assert _first_point("01") == "Unit one point" and _first_point("02") == "Unit two point"
