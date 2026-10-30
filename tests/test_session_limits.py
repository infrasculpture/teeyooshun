"""Session length and accountability: time estimates, per-step caps, the time-out and "Keep going", idle gaps,
how a session ended, per-step counts, short answers, the Session log and the dashboard summary.
Made-up content, fake marker, no API calls."""

import json
import re
import sqlite3
from datetime import timedelta

from app import app_settings, db, practice, revise, session, session_plan, session_track
from app.session_plan import stamp
from conftest import PARENT_PIN
from revise_helpers import add_cards, make_card, point, settings
from session_helpers import add_questions, add_topic, keys, mk, now, step

T1 = "sci-aaa-one"


# ---- Helpers ----------------------------------------------------------------------------------------------------

def login(client):
    client.post("/parent/login", data={"pin": PARENT_PIN, "next": "/"}, follow_redirects=False)


def settings_form(**changes):
    form = {"cards_per_set": "4", "recall_method": "type", "recall_timing": "after_set", "known_threshold": "80",
            **{f"interval_{i}": d for i, d in enumerate(("1", "2", "4", "8", "16"), 1)}}
    return {**form, **changes}


def practise_session(bank, n=3, marks=1, minutes=30, prefix="lim", at=None):
    """A session of focus -> practise (n questions worth `marks`, ids <prefix>-1 ...) -> wrap-up."""
    add_topic(bank, T1)
    add_questions(bank, [mk(f"{prefix}-{i}", T1, marks=marks) for i in range(1, n + 1)])
    return session.create_daily(bank, minutes, now=at, subject="science", focus=T1)


def go(client, sid):
    """"Let's go" on the focus screen, then Start on the practise step."""
    client.post(f"/session/{sid}/done")
    client.post(f"/session/{sid}/start-step")


def answer_q(client, sid, qid, text="kw1"):
    return client.post(f"/q/{qid}/answer", data={"answer": text, "shown_at": "", "scope": f"@s:{sid}"},
                       follow_redirects=False)


def age(conn, sid, minutes):
    """Make the session start `minutes` ago, with her last activity just now."""
    conn.execute("UPDATE sessions SET started_at = ?, last_active_at = ? WHERE id = ?",
                 (stamp(now() - timedelta(minutes=minutes)), stamp(now()), sid))
    conn.commit()


def last_active(conn, sid, minutes_ago):
    conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (stamp(now() - timedelta(minutes=minutes_ago)), sid))
    conn.commit()


def row_of(conn, sid):
    return conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()


def card_set_session(bank, n=3):
    """A session whose only step before the wrap-up is Learn with n cards, started. Returns (session id, set id, cards)."""
    cards = [make_card(f"lim-card-{i}", f"Define term {i}.", {"format": "bullets", "points": [point(f"Meaning {i}.", [f"meaning{i}"])]},
                       topic=T1, subject="science") for i in range(n)]
    add_cards(bank, cards)
    plan = {"blocks": [{"minutes": 30, "subject": "science", "focus_topic": T1,
                        "steps": [session_plan._step("learn", [c["id"] for c in cards]), session_plan._step("wrapup")]}], "log": []}
    sid = session._insert(bank, "daily", "2026-01-01", "science", now(), 30, 1, plan, T1)
    session.start_step(bank, session.get(bank, sid))
    return sid, session.current_block(session.get(bank, sid))["steps"][0]["set_id"], cards


# ---- The cause, and the time estimates --------------------------------------------------------------------------

def test_a_few_quick_test_answers_cannot_shrink_the_estimates(conn, fake_marker):
    qs = [mk(f"quick-{i}", T1, marks=2) for i in range(25)]
    add_questions(conn, qs)
    for q in qs[:session_plan.MIN_SAMPLES - 1]:
        practice.submit_first_attempt(conn, q["id"], "kw1 kw2", 8)               # 4 seconds a mark: a test click-through
    assert session_plan.time_estimates(conn) == (1.5, 1.2)                        # under 20 attempts: the defaults
    practice.submit_first_attempt(conn, qs[session_plan.MIN_SAMPLES - 1]["id"], "kw1 kw2", 8)
    assert session_plan.time_estimates(conn) == (1.5, 1.0)                        # 20: used, but never under a minute a mark


def test_her_own_pace_is_used_after_20_real_attempts_and_cards_never_go_under_a_minute(conn, fake_marker):
    qs = [mk(f"slow-{i}", T1, marks=2) for i in range(20)]
    add_questions(conn, qs)
    for q in qs:
        practice.submit_first_attempt(conn, q["id"], "kw1 kw2", 240)             # two minutes a mark
    c = make_card("card-fast", "Define it.", {"format": "bullets", "points": [point("It.", ["it"])]}, topic=T1, subject="science")
    add_cards(conn, [c])
    for _ in range(session_plan.MIN_SAMPLES):
        revise.record_attempt(conn, c, method="paper", covered_ids=[], units_total=1, settings=settings(), seconds_taken=10)
    assert session_plan.time_estimates(conn) == (1.0, 2.0)


def test_a_30_minute_session_no_longer_plans_dozens_of_questions(conn, fake_marker):
    add_topic(conn, T1)
    add_questions(conn, [mk(f"bulk-{i:02d}", T1) for i in range(60)])
    quick = [mk(f"q-{i}", "sci-zzz-other", marks=2) for i in range(25)]
    add_questions(conn, quick)
    for q in quick:
        practice.submit_first_attempt(conn, q["id"], "kw1 kw2", 6)                # the quick test answers behind the 52
    block = session_plan.build_block(conn, subject="science", minutes=30, now=now(), focus=T1)
    assert len(step(block, "practise")["items"]) == 8
    block = session_plan.build_block(conn, subject="science", minutes=60, now=now(), focus=T1)
    assert len(step(block, "practise")["items"]) == 15


# ---- Caps as parent settings ----------------------------------------------------------------------------------------

def test_caps_come_from_the_parents_settings(conn):
    add_topic(conn, T1)
    add_questions(conn, [mk(f"real-{i:02d}", T1) for i in range(20)] + [mk(f"gen-{i}", T1, source="generated") for i in range(9)])

    def counts(minutes):
        block = session_plan.build_block(conn, subject="science", minutes=minutes, now=now(), focus=T1)
        return len(step(block, "practise")["items"]), len(step(block, "check")["items"])

    assert counts(30) == (8, 5) and counts(60) == (15, 5)                         # the defaults
    assert app_settings.save(conn, {"practise_max_30": 3, "practise_max_60": 4, "check_max": 2}) == []
    assert counts(30) == (3, 2) and counts(60) == (4, 2)


def test_cap_settings_are_validated_and_have_defaults(conn):
    saved = app_settings.get_all(conn)
    assert (saved["practise_max_30"], saved["practise_max_60"], saved["check_max"]) == (8, 15, 5)
    for bad in ({"practise_max_30": 0}, {"practise_max_30": 41}, {"practise_max_60": "x"}, {"check_max": 0}, {"check_max": 11}):
        assert app_settings.save(conn, bad), bad
    assert app_settings.get_all(conn)["check_max"] == 5
    assert app_settings.save(conn, {"practise_max_30": 40, "practise_max_60": 1, "check_max": 10}) == []
    assert app_settings.from_form({})["check_max"] == 5                            # a form without the field keeps the default
    assert app_settings.from_form({"practise_max_30": "6"})["practise_max_30"] == 6


def test_the_parent_sets_the_caps_on_the_settings_page(client, bank):
    login(client)
    page = client.get("/parent/settings").text
    for name, value in (("practise_max_30", 8), ("practise_max_60", 15), ("check_max", 5)):
        assert f'name="{name}" inputmode="numeric" value="{value}"' in page
    r = client.post("/parent/settings", data=settings_form(practise_max_30="6", practise_max_60="12", check_max="4"))
    assert r.status_code == 200 and app_settings.get_all(bank)["practise_max_60"] == 12
    assert client.post("/parent/settings", data=settings_form(check_max="0")).status_code == 422
    assert app_settings.get_all(bank)["check_max"] == 4


# ---- The time-out ----------------------------------------------------------------------------------------------------

def test_time_out_waits_for_the_current_item_then_goes_to_the_wrap_up(client, bank):
    sid = practise_session(bank)
    go(client, sid)
    age(bank, sid, 31)                                               # the planned 30 minutes pass in the middle of question 1
    assert client.get(f"/q/lim-1", params={"scope": f"@s:{sid}"}).status_code == 200
    assert "timeout" not in session.get(bank, sid)["plan"]           # opening the question changes nothing
    assert answer_q(client, sid, "lim-1").status_code == 303         # she finishes the question she was on
    wrap = client.get(f"/session/{sid}").text
    assert "That's today's session done" in wrap and "Keep going" in wrap and "Nearly done" not in wrap
    s = session.get(bank, sid)
    practise = step(session.current_block(s), "practise")
    assert practise["status"] == "done" and practise["cut"] == 2     # the other two questions were cut
    assert s["plan"]["timeout"] and not s["plan"]["timeout"].get("continued")


def test_no_time_out_before_the_planned_length(client, bank):
    sid = practise_session(bank)
    go(client, sid)
    age(bank, sid, 29)
    answer_q(client, sid, "lim-1")
    r = client.get(f"/session/{sid}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/q/lim-2")


def test_keep_going_continues_and_the_session_ends_as_continued(client, bank):
    sid = practise_session(bank)
    go(client, sid)
    age(bank, sid, 31)
    answer_q(client, sid, "lim-1")
    assert "Keep going" in client.get(f"/session/{sid}").text
    assert client.post(f"/session/{sid}/keep-going", follow_redirects=False).status_code == 303
    r = client.get(f"/session/{sid}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/q/lim-2")   # on with the rest
    answer_q(client, sid, "lim-2")
    answer_q(client, sid, "lim-3")
    wrap = client.get(f"/session/{sid}").text                          # still over time, but it never times out twice
    assert "Nearly done" in wrap and "That's today's session done" not in wrap
    client.post(f"/session/{sid}/done")
    assert session.get(bank, sid)["end_state"] == "continued"


def test_a_card_set_ends_after_the_card_she_is_on(client, bank):
    sid, set_id, _ = card_set_session(bank)
    client.post(f"/revise/sets/{set_id}/tick/0", data={"unit": "P1", "shown_at": ""})
    assert f'href="/revise/sets/{set_id}/recall/1"' in client.get(f"/revise/sets/{set_id}/result/0").text   # in time: the next card
    age(bank, sid, 31)
    assert f'href="/session/{sid}"' in client.get(f"/revise/sets/{set_id}/result/0").text               # over time: that was the last
    assert "That's today's session done" in client.get(f"/session/{sid}").text
    learn = step(session.current_block(session.get(bank, sid)), "learn")
    assert learn["status"] == "done" and learn["cut"] == 2


def test_time_out_in_a_longer_session_skips_the_break_and_keep_going_brings_it_back(bank):
    sid = practise_session(bank, minutes=90)
    s = session.get(bank, sid)
    assert s["block_count"] == 2
    age(bank, sid, 95)
    session.finish_step(bank, s, "done")                                # the focus screen
    s = session.get(bank, sid)
    assert session.apply_timeout(bank, s) is True
    s = session.get(bank, sid)
    assert s["block_count"] == 1 and keys(session.current_block(s)) == ["focus", "practise", "wrapup"]
    assert session.position(s)["step"]["key"] == "wrapup"
    assert session.keep_going(bank, s) is True
    s = session.get(bank, sid)
    assert s["block_count"] == 2 and keys(session.current_block(s)) == ["focus", "practise"]
    assert session.position(s)["step"]["key"] == "practise"
    assert session.apply_timeout(bank, s) is False and session.keep_going(bank, s) is False   # once only


# ---- Idle gaps, breaks ---------------------------------------------------------------------------------------------

def test_gaps_over_three_minutes_are_logged_as_idle_and_do_not_count_toward_the_time_out(bank):
    t0 = now() - timedelta(minutes=70)
    sid = practise_session(bank, at=t0)
    control = practise_session(bank, at=t0, prefix="ctl")                  # same start, no idle gaps
    assert session_track.touch(bank, sid, t0 + timedelta(minutes=2)) == 0
    assert session_track.touch(bank, sid, t0 + timedelta(minutes=4, seconds=59)) == 0            # a 179 second gap is not idle
    t3 = t0 + timedelta(minutes=4, seconds=59) + timedelta(seconds=181)
    assert session_track.touch(bank, sid, t3) == 181                                               # 181 seconds is
    assert session_track.touch(bank, sid, t3 + timedelta(minutes=20)) == 1200
    s = session.get(bank, sid)
    gaps = json.loads(s["idle_json"])
    assert s["idle_seconds"] == 1381 and [g["seconds"] for g in gaps] == [181, 1200]
    assert not session_track.is_overtime(s, t0 + timedelta(minutes=31))        # 31 minutes on the clock, 8 of them real
    assert session_track.is_overtime(s, t0 + timedelta(minutes=60))           # 60 minutes, 37 of them real
    assert session_track.is_overtime(session.get(bank, control), t0 + timedelta(minutes=31))


def test_idle_time_is_left_out_when_the_time_out_is_checked(client, bank):
    sid = practise_session(bank)
    go(client, sid)
    age(bank, sid, 50)
    bank.execute("UPDATE sessions SET idle_seconds = 1500 WHERE id = ?", (sid,))     # 25 of the 50 minutes were idle
    bank.commit()
    answer_q(client, sid, "lim-1")
    r = client.get(f"/session/{sid}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/q/lim-2")      # 25 minutes of work: carries on
    bank.execute("UPDATE sessions SET idle_seconds = 600 WHERE id = ?", (sid,))        # 40 minutes of work
    bank.commit()
    assert "That's today's session done" in client.get(f"/session/{sid}").text


def test_a_break_between_blocks_is_neither_idle_nor_study_time(bank):
    t0 = now() - timedelta(minutes=70)
    sid = practise_session(bank, at=t0)
    session_track.start_break(bank, sid, t0 + timedelta(minutes=10))
    assert session_track.touch(bank, sid, t0 + timedelta(minutes=25)) == 0             # a long wait on the break screen
    session_track.end_break(bank, sid, t0 + timedelta(minutes=25))
    s = session.get(bank, sid)
    assert s["break_seconds"] == 900 and s["idle_seconds"] == 0 and s["break_from"] is None
    assert not session_track.is_overtime(s, t0 + timedelta(minutes=40))               # 40 - 15 = 25 minutes of work
    assert session_track.is_overtime(s, t0 + timedelta(minutes=46))                   # 31


# ---- Welcome back and the page ping ---------------------------------------------------------------------------------

def test_welcome_back_screen_after_a_gap(client, bank):
    sid = practise_session(bank)
    go(client, sid)
    last_active(bank, sid, 10)
    page = client.get(f"/session/{sid}").text
    assert "Welcome back, ready to carry on?" in page and f'action="/session/{sid}/carry-on"' in page
    assert 'id="welcome-back"' not in page                                              # asked once, by this screen
    r = client.post(f"/session/{sid}/carry-on", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/session/{sid}"
    assert client.get(f"/session/{sid}", follow_redirects=False).status_code == 303    # no screen now: straight to the question
    assert session.get(bank, sid)["idle_seconds"] >= 600


def overlay(html):
    return re.search(r'<div id="welcome-back"[^>]*>', html).group(0)


def test_a_question_page_after_a_gap_opens_with_the_welcome_screen_showing(client, bank):
    sid = practise_session(bank)
    go(client, sid)
    page = client.get("/q/lim-1", params={"scope": f"@s:{sid}"}).text
    assert " hidden" in overlay(page) and "/ping" in page and "60000" in page            # tracked, screen hidden
    last_active(bank, sid, 10)
    page = client.get("/q/lim-1", params={"scope": f"@s:{sid}"}).text
    assert " hidden" not in overlay(page)                                                # she has been away: shown
    assert "welcome-back" not in client.get("/q/lim-1").text                             # outside a session: no pinger


def test_the_page_ping_records_activity_and_a_gap(client, bank):
    sid = practise_session(bank)
    last_active(bank, sid, 10)
    r = client.post(f"/session/{sid}/ping")
    assert r.status_code == 204 and r.content == b""
    s = session.get(bank, sid)
    assert s["idle_seconds"] >= 600 and len(json.loads(s["idle_json"])) == 1
    client.post(f"/session/{sid}/ping")
    assert len(json.loads(session.get(bank, sid)["idle_json"])) == 1                     # the next ping is not a new gap
    assert client.post("/session/9999/ping").status_code == 204                          # unknown session: nothing to record


# ---- How a session ended ---------------------------------------------------------------------------------------------

def test_end_states_are_recorded(client, bank):
    finished = practise_session(bank, n=1, prefix="fin")
    go(client, finished)
    answer_q(client, finished, "fin-1")
    client.get(f"/session/{finished}")
    client.post(f"/session/{finished}/done")
    assert session.get(bank, finished)["end_state"] == "finished"

    timed_out = practise_session(bank, n=2, prefix="tmo")
    go(client, timed_out)
    age(bank, timed_out, 31)
    answer_q(client, timed_out, "tmo-1")
    client.get(f"/session/{timed_out}")
    client.post(f"/session/{timed_out}/done")
    assert session.get(bank, timed_out)["end_state"] == "timed_out"

    abandoned = practise_session(bank, n=1, prefix="abn")
    last_active(bank, abandoned, 180)
    session_track.sweep_abandoned(bank, now())
    s = session.get(bank, abandoned)
    assert s["end_state"] == "abandoned" and s["ended_at"] is None
    assert session.get(bank, finished)["end_state"] == "finished"                       # others are left alone
    client.get(f"/session/{abandoned}")                                                  # she comes back
    assert session.get(bank, abandoned)["end_state"] is None


def test_a_running_session_is_in_progress_until_it_has_been_quiet_for_two_hours(bank):
    sid = practise_session(bank)
    assert session_track.state_of(row_of(bank, sid), now()) == "running"
    assert session_track.state_of(row_of(bank, sid), now() + timedelta(hours=3)) == "abandoned"


# ---- Counts ---------------------------------------------------------------------------------------------------------

def test_step_counts_planned_done_skipped(client, bank):
    sid = practise_session(bank)
    go(client, sid)
    answer_q(client, sid, "lim-1")
    client.post(f"/session/{sid}/skip")                                               # "Not now" for the other two
    s = session.get(bank, sid)
    practise = step(session.current_block(s), "practise")
    assert session_track.step_counts(bank, s["started_at"], practise) == {"planned": 3, "done": 1, "skipped": 2, "cut": 0}


def test_card_step_counts(client, bank):
    sid, set_id, _ = card_set_session(bank)
    for i in (0, 1):
        client.post(f"/revise/sets/{set_id}/tick/{i}", data={"unit": "P1", "shown_at": ""})
    session.finish_step(bank, session.get(bank, sid), "skipped")
    s = session.get(bank, sid)
    learn = step(session.current_block(s), "learn")
    assert session_track.step_counts(bank, s["started_at"], learn) == {"planned": 3, "done": 2, "skipped": 1, "cut": 0}


def test_short_and_blank_answers_are_counted(client, bank):
    sid = practise_session(bank, n=4, marks=2)
    go(client, sid)
    r = client.post("/q/lim-1/answer", data={"answer": "   ", "shown_at": "", "scope": f"@s:{sid}"})
    assert r.status_code == 422 and session.get(bank, sid)["blank_submits"] == 1
    answer_q(client, sid, "lim-1", "kw1")                                  # 1 word on a 2-mark question: short
    answer_q(client, sid, "lim-2", "kw1 kw2 and a few more")                # 6 words: fine
    answer_q(client, sid, "lim-3", "a b c d")                               # 4 words: short
    answer_q(client, sid, "lim-4", "one two three four five")               # exactly 5 words: not short
    row = session_track.describe(bank, row_of(bank, sid), now())
    assert (row["multi_answers"], row["short_answers"], row["blank_submits"]) == (4, 2, 1)
    assert row["flags"] == []                                               # two is not yet a run of them


def test_one_mark_questions_never_count_as_short(client, bank):
    sid = practise_session(bank, n=2, marks=1)
    go(client, sid)
    answer_q(client, sid, "lim-1", "kw1")
    row = session_track.describe(bank, row_of(bank, sid), now())
    assert (row["multi_answers"], row["short_answers"]) == (0, 0)


def test_a_run_of_short_answers_is_flagged_and_so_is_a_mostly_idle_session(client, bank):
    sid = practise_session(bank, n=3, marks=2)
    go(client, sid)
    for i in (1, 2, 3):
        answer_q(client, sid, f"lim-{i}", "kw1")
    row = session_track.describe(bank, row_of(bank, sid), now())
    assert row["flags"] == ["3 of 3 multi-mark answers were very short"]
    age(bank, sid, 30)
    bank.execute("UPDATE sessions SET idle_seconds = 900 WHERE id = ?", (sid,))        # idle for half of it
    bank.commit()
    flags = session_track.describe(bank, row_of(bank, sid), now())["flags"]
    assert any(f.startswith("Idle for 50%") for f in flags) and len(flags) == 2


# ---- The Session log (PIN) and the dashboard summary -----------------------------------------------------------------

def test_the_session_log_needs_the_pin(client, bank):
    practise_session(bank)
    r = client.get("/parent/sessions", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/parent/login?next=/parent/sessions"
    assert "Session log" not in client.get("/").text                                  # no link for her
    login(client)
    assert "Session log" in client.get("/parent/sessions").text
    assert 'href="/parent/sessions"' in client.get("/").text                           # the parent's menu links to it


def test_the_log_lists_sessions_newest_first_with_their_figures(client, bank):
    older = practise_session(bank, n=2, prefix="old")
    go(client, older)
    answer_q(client, older, "old-1")
    client.post(f"/session/{older}/skip")
    client.get(f"/session/{older}")
    client.post(f"/session/{older}/done")
    bank.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (stamp(now() - timedelta(days=1)), older))
    bank.commit()
    practise_session(bank, n=1, prefix="new")                                          # started later, still running
    login(client)
    page = client.get("/parent/sessions").text
    assert page.index("In progress") < page.index("Finished")                          # newest first
    assert "Practice: 2 / 1 / 1" in page                                               # planned / done / skipped
    assert "Welcome back" not in page


def test_the_dashboard_summarises_how_sessions_ended_and_flags_the_idle_ones(client, bank):
    done = practise_session(bank, n=1, prefix="d1")
    go(client, done)
    answer_q(client, done, "d1-1")
    client.get(f"/session/{done}")
    client.post(f"/session/{done}/done")
    stale = practise_session(bank, n=1, prefix="d2")
    last_active(bank, stale, 300)
    idle = practise_session(bank, n=1, prefix="d3")
    go(client, idle)
    age(bank, idle, 30)
    bank.execute("UPDATE sessions SET idle_seconds = 900 WHERE id = ?", (idle,))
    bank.commit()
    login(client)
    page = client.get("/parent/dashboard").text
    assert "How sessions ended" in page and "finished (1 of 3)" in page and "abandoned (1)" in page
    assert "idle in total" in page and "15 min" in page
    assert "Idle for 50% of the session" in page and 'href="/parent/sessions"' in page


# ---- The database ----------------------------------------------------------------------------------------------------

def test_upgrading_a_version_17_database_keeps_its_sessions(tmp_path):
    path = tmp_path / "old.db"
    conn = db.connect(path)
    conn.executescript(db.SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 1")
    for n in range(2, 18):
        conn.executescript(getattr(db, f"SCHEMA_V{n}_FILE").read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("INSERT INTO sessions (day, started_at, ended_at, length_planned, plan_json) "
                 "VALUES ('2026-10-01', '2026-10-01T10:00:00Z', '2026-10-01T10:30:00Z', 30, '{}')")
    conn.execute("INSERT INTO sessions (day, started_at, length_planned, plan_json) VALUES ('2026-10-02', '2026-10-02T10:00:00Z', 30, '{}')")
    conn.commit()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 17
    conn.close()
    db.init_db(path)
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 21
    rows = conn.execute("SELECT end_state, last_active_at, idle_seconds, idle_json FROM sessions ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("finished", "2026-10-01T10:30:00Z", 0, "[]"), (None, "2026-10-02T10:00:00Z", 0, "[]")]
    conn.close()
    assert sqlite3.sqlite_version_info >= (3, 31)                       # the idle log appends with json_insert('$[#]')
