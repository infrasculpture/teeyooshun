"""The Study level (Sec 1 / Sec 2 / both): what it filters, what it must never change, the student's chips in Revise
and Practice, the parent's dashboard (always every level), and the "Practice" spelling. Made-up content; no API calls."""

import re
import sqlite3
from pathlib import Path

import pytest

from app import (app_settings, dashboard, db, mode_level, practice, revise, rewards, rewards_config, schedule,
                 session, session_plan)
from app.topics import set_topic_status, taught_topics
from conftest import PARENT_PIN
from revise_helpers import add_cards, make_card, point, settings
from session_helpers import add_questions, add_topic, answer, mk, now

T1, T2 = "sci-aaa-one", "sci-bbb-two"            # T1 is Sec 1, T2 is Sec 2
ALLOWED = {                                       # level -> what a student at that level may be given
    "sec1": {"topics": {T1}, "real": {"r1"}, "generated": {"g1"}, "cards": {"c1"}},
    "sec2": {"topics": {T2}, "real": {"r2"}, "generated": {"g2"}, "cards": {"c2"}},
    "both": {"topics": {T1, T2}, "real": {"r1", "r2"}, "generated": {"g1", "g2"}, "cards": {"c1", "c2"}},
}


def card(card_id, topic, level):
    return make_card(card_id, f"Define {card_id}.",
                     {"format": "bullets", "points": [point(f"{card_id} text {n}.", [f"{card_id}-{n}"]) for n in (1, 2)]},
                     topic=topic, subject="science", level=level)


def content(conn):
    """A Sec 1 topic and a Sec 2 topic, each with a real question, a generated question and a due card."""
    add_topic(conn, T1, sort_order=1, title="Topic Alpha", likely_level="sec1")
    add_topic(conn, T2, sort_order=2, title="Topic Beta", likely_level="sec2")
    add_questions(conn, [mk("r1", T1, level="sec1"), mk("r2", T2, level="sec2"),
                         mk("g1", T1, level="sec1", source="generated"), mk("g2", T2, level="sec2", source="generated")])
    add_cards(conn, [card("c1", T1, "sec1"), card("c2", T2, "sec2")])
    for card_id in ("c1", "c2"):
        conn.execute("INSERT INTO card_progress (card_id, box, last_attempt_at, due_at) VALUES (?, 1, ?, ?)",
                     (card_id, "2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z"))
    conn.commit()


def set_level(conn, level):
    assert app_settings.save(conn, {"current_level": level}) == []


def login(client):
    client.post("/parent/login", data={"pin": PARENT_PIN, "next": "/"}, follow_redirects=False)


# ---- Every student-side list follows the level --------------------------------------------------------------

@pytest.mark.parametrize("level", ["sec1", "sec2", "both"])
def test_every_student_side_list_returns_only_items_at_the_level(conn, level):
    content(conn)
    set_level(conn, level)
    ok = ALLOWED[level]
    assert taught_topics(conn) == ok["topics"]
    # Choose my own / Practice
    assert {t["topic"] for t in practice.list_topics(conn, "science")} == ok["topics"]
    assert sum(s["count"] for s in practice.list_subjects(conn)) == len(ok["real"]) + len(ok["generated"])
    for other in {T1, T2} - ok["topics"]:
        assert practice.next_question_id(conn, "science", other) is None
    # Revise
    assert {t["topic"] for t in revise.list_topics(conn, "science")} == ok["topics"]
    assert sum(s["cards"] for s in revise.list_subjects(conn)) == len(ok["cards"])
    assert set(revise.build_set(conn, "science", [], settings(cards_per_set=10), now=now())) == ok["cards"]
    # the map and the focus-topic choices
    assert {w["topic"] for region in rewards.map_view(conn) for w in region["waypoints"]} == ok["topics"]
    assert {t["id"] for t in schedule.taught_by_subject(conn)["science"]} == ok["topics"]
    # Today's session: focus topic, warm-up, Learn, Practice, topic check
    block = session_plan.build_block(conn, subject="science", minutes=60, now=now())
    assert block["focus_topic"] in ok["topics"]
    for st in block["steps"]:
        allowed = {"warmup": ok["cards"], "learn": ok["cards"], "quickfire": ok["cards"],
                   "practise": ok["real"], "check": ok["generated"]}.get(st["key"])
        if allowed is not None:
            assert st["items"] and set(st["items"]) <= allowed, st["key"]
    assert {st["key"] for st in block["steps"]} >= {"warmup", "practise", "check"}   # (Learn skips cards already in the warm-up)
    # Year-end review
    review = session_plan.build_year_end(conn, minutes=60, now=now())["steps"][0]
    assert review["key"] == "review" and set(review["items"]) == ok["generated"]


def test_a_level_can_be_passed_in_without_touching_the_setting(conn):
    content(conn)
    set_level(conn, "sec2")
    assert {t["topic"] for t in practice.list_topics(conn, "science", "sec1")} == {T1}
    assert {t["topic"] for t in revise.list_topics(conn, "science", level="both")} == {T1, T2}
    assert {t["topic"] for t in practice.list_topics(conn, "science", "all")} == {T1, T2}
    assert app_settings.get_all(conn)["current_level"] == "sec2"


def test_a_topic_with_no_known_level_or_both_shows_at_every_level(conn):
    add_topic(conn, T1, likely_level="both")
    add_questions(conn, [mk("r1", T1, level="both")])     # not one of sec1..sec4, so likely_level decides
    for level in ("sec1", "sec2", "both"):
        set_level(conn, level)
        assert taught_topics(conn) == {T1}


# ---- Switching levels changes nothing she has done -----------------------------------------------------------

def snapshot(conn):
    tables = ("card_progress", "attempts", "recall_attempts", "topic_status", "reward_events", "sessions")
    return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in tables}


def test_switching_sec2_sec1_sec2_leaves_progress_untouched(conn, fake_marker):
    content(conn)
    set_level(conn, "both")
    answer(conn, mk("r1", T1), True)
    answer(conn, mk("r2", T2), False)
    set_topic_status(conn, T1, "taught")
    conn.execute("UPDATE card_progress SET box = 3 WHERE card_id = 'c1'")
    conn.commit()
    set_level(conn, "sec2")
    before, states = snapshot(conn), rewards.topic_states(conn)
    set_level(conn, "sec1")
    middle = snapshot(conn)
    assert middle == before and rewards.topic_states(conn) == states    # hidden topics keep their state
    set_level(conn, "sec2")
    assert snapshot(conn) == before and rewards.topic_states(conn) == states
    assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 2


# ---- The parent's migration and setting ---------------------------------------------------------------------

def test_upgrading_a_version_18_database_maps_the_saved_level(tmp_path):
    for saved, expected in (('"sec1"', "sec1"), ('"sec2"', "sec2"), ('"sec3"', "sec2"), ('"sec4"', "sec2")):
        path = tmp_path / f"old{expected}{saved[-3:-1]}.db"
        conn = db.connect(path)
        conn.executescript(db.SCHEMA_FILE.read_text(encoding="utf-8"))
        conn.execute("PRAGMA user_version = 1")
        for n in range(2, 19):
            conn.executescript(getattr(db, f"SCHEMA_V{n}_FILE").read_text(encoding="utf-8"))
            conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO app_settings (key, value, updated_at) VALUES ('current_level', ?, 'x')", (saved,))
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 18
        conn.close()
        db.init_db(path)
        conn = db.connect(path)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 19
        assert app_settings.get_all(conn)["current_level"] == expected
        conn.execute("INSERT INTO mode_level_log (at, mode, level) VALUES ('2026-10-26T01:00:00Z', 'revise', 'sec1')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO mode_level_log (at, mode, level) VALUES ('x', 'revise', 'sec9')")
        conn.close()


def test_the_settings_page_offers_sec1_sec2_both(client, bank):
    login(client)
    page = client.get("/parent/settings").text
    assert 'name="current_level"' in page and "Study level" in page and "Her level" not in page
    assert 'value="both"' in page and 'value="sec3"' not in page
    assert client.post("/parent/settings", data={"cards_per_set": "4", "recall_method": "type", "recall_timing": "after_set",
                                                 "known_threshold": "80", "current_level": "both",
                                                 **{f"interval_{i}": d for i, d in enumerate(("1", "2", "4", "8", "16"), 1)}}
                       ).status_code == 200
    assert app_settings.get_all(bank)["current_level"] == "both"


# ---- The student's chips (Revise and Practice only) ---------------------------------------------------------

def active_chip(page: str) -> str:
    match = re.search(r'<a class="button selected" aria-current="true" href="[^"]*lv=(\w+)"', page)
    return match.group(1) if match else ""


def test_the_practice_chips_start_at_the_parents_level_and_do_not_change_it(client, bank):
    content(bank)
    set_level(bank, "sec2")
    page = client.get("/choose?subject=science").text
    assert active_chip(page) == "sec2" and "lv=sec1" in page and "lv=both" in page
    assert f"topic={T2}" in client.get("/topics?subject=science").text

    page = client.get("/choose?subject=science&lv=sec1").text          # she taps Sec 1
    assert active_chip(page) == "sec1"
    topics_page = client.get("/topics?subject=science").text           # the topic list, next page of this visit
    assert f"topic={T1}" in topics_page and f"topic={T2}" not in topics_page
    location = client.get("/practice?subject=science", follow_redirects=False).headers["location"]
    assert location.split("?")[0] in ("/q/r1", "/q/g1")                # a Sec 1 question, never r2 / g2
    assert app_settings.get_all(bank)["current_level"] == "sec2"       # the parent's setting is untouched

    page = client.get("/choose").text                                   # she opens Practice again
    assert active_chip(page) == "sec2"
    assert f"topic={T2}" in client.get("/topics?subject=science").text


def test_the_revise_chips_choose_the_cards_for_that_visit(client, bank):
    content(bank)
    set_level(bank, "sec2")
    assert active_chip(client.get("/revise?subject=science").text) == "sec2"
    page = client.get("/revise?subject=science&lv=both").text
    assert active_chip(page) == "both" and f'value="{T1}"' in page and f'value="{T2}"' in page
    page = client.get("/revise?subject=science&lv=sec1").text
    assert f'value="{T1}"' in page and f'value="{T2}"' not in page
    r = client.post("/revise/start", data={"subject": "science"}, follow_redirects=False)
    set_id = int(r.headers["location"].split("/")[3])
    assert revise.get_set(bank, set_id)["card_ids"] == ["c1"]
    assert app_settings.get_all(bank)["current_level"] == "sec2"
    assert active_chip(client.get("/revise").text) == "sec2"           # a fresh visit starts at the parent's level
    assert active_chip(client.get("/choose").text) == "sec2"           # and the modes don't share a choice


def test_each_switch_is_logged_for_the_parent(client, bank):
    content(bank)
    set_level(bank, "sec2")
    client.get("/choose?subject=science&lv=sec2")                       # already sec2: nothing to log
    client.get("/choose?subject=science&lv=sec1")
    client.get("/choose?subject=science&lv=sec1")                       # a refresh is not a second switch
    client.get("/revise?lv=both")
    switches = mode_level.recent_switches(bank)
    assert [(s["mode"], s["level"]) for s in switches] == [("Revise", "Both"), ("Practice", "Sec 1")]
    page_for_her = client.get("/parent/sessions", follow_redirects=False)
    assert page_for_her.status_code == 303                              # the log is behind the PIN
    login(client)
    page = client.get("/parent/sessions").text
    assert "Level switches" in page and "Revise" in page and "Sec 1" in page and "Both" in page


def test_the_chips_are_not_on_the_home_screen_or_in_a_session(client, bank):
    content(bank)
    set_level(bank, "sec2")
    assert "level-chips" in client.get("/choose").text and "level-chips" in client.get("/revise").text
    assert "level-chips" not in client.get("/").text
    s = session.create_daily(bank, 30, subject="science", focus=T2)
    assert "level-chips" not in client.get(f"/session/{s}").text
    client.get("/choose?lv=sec1")                                        # her choice in Practice ...
    plan = session_plan.build_block(bank, subject="science", minutes=30, now=now())
    assert plan["focus_topic"] == T2                                     # ... never reaches Today's session


# ---- The parent's dashboard shows every level ---------------------------------------------------------------

def test_the_dashboard_lists_every_level_and_the_filter_works(client, bank):
    content(bank)
    set_level(bank, "sec1")                                              # her study level hides Topic Beta from her
    d = dashboard.build(bank)
    rows = {r["topic"]: r for r in d["topics"]["science"]}
    assert {T1, T2} <= set(rows) and rows[T1]["level"] == "Sec 1" and rows[T2]["level"] == "Sec 2"   # (the bank also holds the real topic tree)

    def science(**kw):
        return {r["topic"] for r in dashboard.build(bank, **kw)["topics"]["science"]}
    assert T1 in science(level="sec1") and T2 not in science(level="sec1")
    assert T2 in science(level="sec2") and T1 not in science(level="sec2")
    assert {T1, T2} <= science(level="nonsense")

    login(client)
    page = client.get("/parent/dashboard").text
    assert "Topic Alpha" in page and "Topic Beta" in page and "<th>Level</th>" in page
    assert "level=sec1" in page and "level=sec2" in page and "level=all" in page
    page = client.get("/parent/dashboard?level=sec2").text
    assert "Topic Beta" in page and "Topic Alpha" not in page


def test_the_dashboard_topic_table_ignores_the_study_level(bank):
    content(bank)
    set_level(bank, "sec1")
    rows = dashboard.topics(bank, dashboard.make_period(bank, "all"), [], None, now())["science"]
    assert {T1, T2} <= {r["topic"] for r in rows}


# ---- The "Practice" spelling --------------------------------------------------------------------------------

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


def visible_text(source: str) -> str:
    source = re.sub(r"\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}", " ", source, flags=re.S)   # template code
    return re.sub(r"<[^>]*>", " ", source)                                         # tags and their attributes


def test_no_visible_practise_is_left_in_the_templates():
    found = []
    for path in sorted(TEMPLATES.glob("*.html")):
        for match in re.finditer(r"[Pp]ractis\w*", visible_text(path.read_text(encoding="utf-8"))):
            found.append(f"{path.name}: {match.group(0)}")
    assert found == []


def test_no_visible_practise_is_left_in_the_strings_she_or_a_parent_sees():
    visible = list(session_plan.TITLES.values())
    visible += [c["label"] for c in rewards_config.COMMISSION_POOL.values()]
    visible += app_settings.validate({k: None for k in app_settings.DEFAULTS})          # every validation message
    assert [v for v in visible if re.search(r"[Pp]ractis", v)] == []
    assert session_plan.TITLES["practise"] == "Practice"                                # the key stays, the label changes


def test_a_session_planned_before_the_change_shows_practice(bank):
    content(bank)
    s = session.create_daily(bank, 30, subject="science", focus=T1)
    row = bank.execute("SELECT plan_json FROM sessions WHERE id = ?", (s,)).fetchone()
    bank.execute("UPDATE sessions SET plan_json = ? WHERE id = ?", (row["plan_json"].replace('"Practice"', '"Practise"'), s))
    bank.commit()
    assert "Practise" in bank.execute("SELECT plan_json FROM sessions WHERE id = ?", (s,)).fetchone()[0]
    titles = [st["title"] for b in session.get(bank, s)["plan"]["blocks"] for st in b["steps"]]
    assert "Practice" in titles and "Practise" not in titles
