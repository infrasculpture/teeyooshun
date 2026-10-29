"""Question numbers carried through an exam paper's answer key. The key prints a number once and the part
answers (a), (b), (c) run over later pages with only part letters, so the model may give a later part the
number of the next question's reprinted stem, or repeat the last number when parts restart.
Made-up data only; no API calls."""

import json

from app.db import connect
from app.ingest.assemble import KEY_SEQUENCE_WARNING, key_sequence_labels
from app.ingest.reassemble import main as reassemble
from app.ingest.summary import file_summary, format_summary
from ingest_helpers import PAPERS_YAML, a, ingest, make_source, page, q

TOPIC = "sci-05-ray-model-of-light"


def _qs(*items):
    return [q(n, p, topic=TOPIC) for n, p in items]


def _paper(env, questions, key_pages, yaml_text=PAPERS_YAML):
    """File pages: 1 instructions, 2 questions, 3.. the key pages."""
    folder = make_source(env, "papers", yaml_text,
                         {"2024_test_eoy.pdf": ["Instructions", "Q"] + [f"Key {i}" for i in range(len(key_pages))]})
    ingest(folder, [page("other", type_reason="instructions"), page(questions=questions), *key_pages])
    return folder


def _items(status="pending"):
    rows = connect().execute("SELECT * FROM staged_items WHERE status = ? ORDER BY id", (status,)).fetchall()
    return {r["item_id"].rsplit("-", 1)[1]: (json.loads(r["record_json"]), json.loads(r["warnings_json"])) for r in rows}


def _summary():
    conn = connect()
    return "\n".join(line for f in conn.execute("SELECT id FROM source_files WHERE status != 'superseded'").fetchall()
                     for line in format_summary(file_summary(conn, f["id"])))


def _texts(record):
    return [mp["text"] for mp in record["mark_points"]]


def _pts(*names):
    return tuple(names)


# Q1 has parts a-c and Q2 has a-b. Page 3 prints "1." with (a); (b) is on page 4 with no number; (c) is on
# page 5 together with Q2's reprinted stem, so the model called it Q2; Q2's answers on page 6 repeat "1".
QUESTIONS = _qs(("1", "a"), ("1", "b"), ("1", "c"), ("2", "a"), ("2", "b"))


def _key_pages():
    return [
        page("answer", answers=[a("1", "a", points=_pts("1a"))]),
        page("answer", answers=[a("", "b", points=_pts("1b"))]),
        page("answer", questions=[q("2", "a", topic=TOPIC)], answers=[a("2", "c", points=_pts("1c"))]),
        page("answer", answers=[a("1", "a", points=_pts("2a")), a("1", "b", points=_pts("2b"))]),
    ]


# ---- The rule on its own ------------------------------------------------------------------------

def _labels(entries, numbers=("1", "2", "3")):
    out = key_sequence_labels([(i, qn, p) for i, (qn, p) in enumerate(entries)], list(numbers))
    return [out[i] for i in range(len(entries))]


def test_parts_in_order_keep_the_running_number_across_pages():
    assert _labels([("1", "a"), ("1", "b"), ("2", "c")]) == [("1", "a"), ("1", "b"), ("1", "c")]  # reprinted Q2 stem
    assert _labels([("1", "a"), ("", "b"), ("", "c")]) == [("1", "a"), ("1", "b"), ("1", "c")]


def test_parts_restarting_move_to_the_next_question():
    assert _labels([("1", "a"), ("1", "b"), ("1", "a"), ("1", "b")]) == [
        ("1", "a"), ("1", "b"), ("2", "a"), ("2", "b")]
    assert _labels([("1", "a"), ("", "a")]) == [("1", "a"), ("2", "a")]


def test_a_number_that_agrees_with_the_parts_is_taken_as_printed():
    entries = [("1", "a"), ("1", "b"), ("2", "a"), ("2", "b"), ("3", ""), ("5", "")]
    assert _labels(entries, ("1", "2", "3", "5")) == entries


def test_nothing_to_move_to_leaves_the_label():
    assert _labels([("1", "a"), ("1", "a")], numbers=("1",)) == [("1", "a"), ("1", "a")]
    assert _labels([("we", "a"), ("1", "a")]) == [("we", "a"), ("1", "a")]
    assert _labels([("", "a")]) == [("", "a")]  # nothing seen yet


# ---- Through the pipeline -----------------------------------------------------------------------

def test_the_key_is_read_as_one_sequence(env):
    _paper(env, QUESTIONS, _key_pages())
    items = _items()
    assert [_texts(items[k][0]) for k in ("q1a", "q1b", "q1c", "q2a", "q2b")] == [["1a"], ["1b"], ["1c"], ["2a"], ["2b"]]
    assert KEY_SEQUENCE_WARNING not in items["q1a"][1] and KEY_SEQUENCE_WARNING not in items["q1b"][1]
    for k in ("q1c", "q2a", "q2b"):
        assert KEY_SEQUENCE_WARNING in items[k][1]
        assert not any("More than one answer" in w or "two different answers" in w for w in items[k][1])
    assert items["q1c"][0]["key_label"] == "Q2(c)"
    text = _summary()
    assert "  Key sequence relabelled: 3" in text and "Labels normalised" not in text
    assert "Answers without a question" not in text and "Questions without an answer" not in text


def test_the_preview_lists_each_relabel_with_its_page(env):
    folder = _paper(env, QUESTIONS, _key_pages())
    lines = []
    assert reassemble([str(folder)], out=lines.append) == 0
    text = "\n".join(lines)
    assert "Key sequence relabels (3): raw label -> new label (file page)" in text
    assert "    Q2(c) -> Q1(c) (page 5)" in text
    assert "    Q1(a) -> Q2(a) (page 6)" in text and "    Q1(b) -> Q2(b) (page 6)" in text


def test_a_label_not_in_the_question_list_is_left_alone(env):
    # Q2 has only (a): the restarted (b) on page 5 would be Q2(b), which does not exist.
    _paper(env, _qs(("1", "a"), ("1", "b"), ("2", "a")), [
        page("answer", answers=[a("1", "a", points=_pts("1a")), a("1", "b", points=_pts("1b"))]),
        page("answer", answers=[a("1", "a", points=_pts("2a")), a("1", "b", points=_pts("odd"))]),
    ])
    items = _items()
    assert _texts(items["q2a"][0]) == ["2a"] and _texts(items["q1b"][0]) == ["1b"]
    assert "  Key sequence relabelled: 1" in _summary()
    assert any("More than one answer" in w for w in items["q1b"][1])  # "odd" stays where the model put it


def test_a_question_that_has_an_answer_never_gets_a_second(env):
    # The restarted (a) would be Q2(a), but Q2(a) has its own answer later in the key.
    _paper(env, _qs(("1", "a"), ("2", "a")), [
        page("answer", answers=[a("1", "a", points=_pts("1a"))]),
        page("answer", answers=[a("1", "a", points=_pts("stray"))]),
        page("answer", answers=[a("2", "a", points=_pts("2a"))]),
    ])
    items = _items()
    assert _texts(items["q2a"][0]) == ["2a"] and "key_label" not in items["q2a"][0]
    assert "Key sequence relabelled" not in _summary()


def test_the_first_answer_at_a_label_stays_put(env):
    # The sequence says (c) belongs to Q1, but the entry labelled Q2(c) is the only answer Q2(c) has: it stays.
    _paper(env, _qs(("1", "a"), ("1", "b"), ("1", "c"), ("2", "c")), [
        page("answer", answers=[a("1", "a", points=_pts("1a")), a("1", "b", points=_pts("1b"))]),
        page("answer", answers=[a("2", "c", points=_pts("2c"))]),
    ])
    items = _items()
    assert _texts(items["q2c"][0]) == ["2c"] and _texts(items["q1c"][0]) == []
    assert "Key sequence relabelled" not in _summary()


def test_numbering_overrides_win(env):
    yaml_text = PAPERS_YAML + 'numbering_overrides:\n  "2024_test_eoy.pdf": {"5": "2"}\n'
    _paper(env, _qs(("1", "a"), ("5", "a")), [
        page("answer", answers=[a("1", "a", points=_pts("1a"))]),
        page("answer", answers=[a("1", "a", points=_pts("stray"))]),  # the sequence says Q2(a) = Q5(a) by the override
        page("answer", answers=[a("2", "a", points=_pts("five"))]),  # Q5's answer, filed under 2 by the override
    ], yaml_text)
    items = _items()
    assert _texts(items["q5a"][0]) == ["five"] and KEY_SEQUENCE_WARNING not in items["q5a"][1]
    assert "Key sequence relabelled" not in _summary()


def test_rejected_items_are_not_given_an_answer_on_a_rebuild(env):
    folder = _paper(env, QUESTIONS, _key_pages())
    conn = connect()
    from app.ingest import staging
    staging.reject(conn, conn.execute("SELECT id FROM staged_items WHERE item_id LIKE '%q2a'").fetchone()["id"])
    assert reassemble([str(folder), "--apply"], out=lambda _line: None) == 0
    assert set(_items("rejected")) == {"q2a"} and "q2a" not in _items()
    assert _texts(_items()["q2b"][0]) == ["2b"]  # the pending neighbour is still relabelled
