"""The taught rule and the session planner. Made-up content; no API calls."""

from collections import Counter
from datetime import date

from app import app_settings, practice, revise, session, session_plan
from app.topics import set_topic_status, taught_topics
from revise_helpers import add_cards, make_card, point, settings
from session_helpers import add_questions, add_topic, answer, keys, mk, now, step

T1, T2, T3 = "sci-aaa-one", "sci-bbb-two", "sci-ccc-three"


def card(card_id, topic, level="sec1", subject="science"):
    return make_card(card_id, f"Define {card_id}.", {"format": "bullets", "points": [point(f"{card_id} text.", [card_id])]},
                     topic=topic, subject=subject, level=level)


def set_level(conn, level):
    assert app_settings.save(conn, {"current_level": level}) == []


# ---- The taught rule ---------------------------------------------------------------------------

def test_no_row_means_taught_only_at_or_below_her_current_level(conn):
    add_questions(conn, [mk("q-low", T1, level="sec1"), mk("q-high", T2, level="sec2")])
    assert taught_topics(conn) == {T1}                       # default level is sec1
    set_level(conn, "sec2")
    assert taught_topics(conn) == {T1, T2}
    set_level(conn, "sec1")
    assert practice.next_question_id(conn, "science", T2) is None and practice.list_topics(conn, "science")[0]["topic"] == T1


def test_a_mixed_topic_uses_its_lowest_level_and_cards_count_too(conn):
    add_questions(conn, [mk("q-a", T1, level="sec2"), mk("q-b", T1, level="sec1"), mk("q-c", T2, level="sec2")])
    add_cards(conn, [card("card-low", T2, level="sec1")])
    assert taught_topics(conn) == {T1, T2}                   # T1: min is sec1; T2: its sec1 card


def test_likely_level_is_used_only_when_a_topic_has_no_questions_or_cards(conn):
    add_topic(conn, T1, likely_level="sec4")                 # has a sec1 question: the content wins
    add_topic(conn, T2, likely_level="sec3")                 # no content: likely_level decides
    add_topic(conn, T3, likely_level="sec1")
    add_questions(conn, [mk("q-a", T1, level="sec1")])
    assert taught_topics(conn) == {T1, T3}
    set_level(conn, "sec3")
    assert taught_topics(conn) == {T1, T2, T3}


def test_an_explicit_row_always_wins(conn):
    add_questions(conn, [mk("q-low", T1, level="sec1"), mk("q-high", T2, level="sec2")])
    set_topic_status(conn, T2, "taught")                     # above her level but taught (e.g. forces in Sec 1)
    set_topic_status(conn, T1, "not_taught")                 # at her level but excluded
    assert taught_topics(conn) == {T2}
    assert [t["topic"] for t in practice.list_topics(conn, "science")] == [T2]   # "Choose my own" follows it
    set_topic_status(conn, T2, "")                           # cleared: back to the level rule
    set_topic_status(conn, T1, "")
    assert taught_topics(conn) == {T1}


def test_revise_follows_the_same_rule(conn):
    add_cards(conn, [card("card-1", T1, level="sec1"), card("card-2", T2, level="sec2")])
    assert [t["topic"] for t in revise.list_topics(conn, "science")] == [T1]
    assert revise.build_set(conn, "science", [], settings(), now=now()) == ["card-1"]
    set_topic_status(conn, T2, "taught")
    assert {t["topic"] for t in revise.list_topics(conn, "science")} == {T1, T2}


def test_current_level_setting_is_validated_and_defaults_to_sec1(conn):
    assert app_settings.get_all(conn)["current_level"] == "sec1"
    assert app_settings.save(conn, {"current_level": "sec9"})          # a problem is returned, nothing saved
    assert app_settings.get_all(conn)["current_level"] == "sec1"
    assert app_settings.from_form({})["current_level"] == "sec1"       # a form without the field keeps the default


# ---- Step order, time budget, mixing ---------------------------------------------------------------

def topic_content(conn, topic=T1, cards=3, real=(1, 2, 1, 3), generated=(1, 1, 2, 1)):
    add_cards(conn, [card(f"{topic}-card-{i}", topic) for i in range(cards)])
    add_questions(conn, [mk(f"{topic}-r{i}", topic, marks=m) for i, m in enumerate(real, 1)]
                  + [mk(f"{topic}-g{i}", topic, marks=m, source="generated") for i, m in enumerate(generated, 1)])


def test_steps_come_in_the_planned_order_within_the_budget(conn):
    topic_content(conn)
    block = session_plan.build_block(conn, subject="science", minutes=30, now=now())
    assert keys(block) == ["focus", "learn", "practise", "check", "wrapup"]    # nothing due, so no warm-up
    assert len(step(block, "learn")["items"]) == 3
    assert step(block, "practise")["items"] == [f"{T1}-r{i}" for i in (1, 2, 3, 4)]
    assert all(i.startswith(f"{T1}-g") for i in step(block, "check")["items"])
    assert sum(s["est_min"] for s in block["steps"]) <= 30


def test_a_small_budget_still_gives_one_item_and_never_overruns(conn):
    topic_content(conn)
    block = session_plan.build_block(conn, subject="science", minutes=6, now=now())
    assert len(step(block, "practise")["items"]) == 1
    assert sum(s["est_min"] for s in block["steps"]) <= 6 + 1.2     # at most one item over, never more


def test_one_mark_and_multi_mark_questions_are_mixed(conn):
    topic_content(conn)
    block = session_plan.build_block(conn, subject="science", minutes=30, now=now())
    marks = [{f"{T1}-r{i}": m for i, m in enumerate((1, 2, 1, 3), 1)}[q] for q in step(block, "practise")["items"]]
    assert marks == [1, 2, 1, 3]


def test_weakest_command_words_come_first(conn, fake_marker):
    add_questions(conn, [mk("hist-explain", "sci-zzz-other", command="explain"), mk("hist-state", "sci-zzz-other", command="state")])
    answer(conn, mk("hist-explain", "sci-zzz-other", command="explain"), good=False)
    answer(conn, mk("hist-state", "sci-zzz-other", command="state"), good=True)
    add_questions(conn, [mk("p-state", T1, command="state"), mk("p-explain", T1, command="explain")])
    block = session_plan.build_block(conn, subject="science", minutes=30, now=now(), focus=T1)
    assert step(block, "practise")["items"] == ["p-explain", "p-state"]


def test_time_estimates_use_her_history_once_there_is_enough(conn):
    assert session_plan.time_estimates(conn) == (1.5, 1.2)
    c = card("card-h", T1)
    add_cards(conn, [c])
    for _ in range(5):
        revise.record_attempt(conn, c, method="paper", covered_ids=[], units_total=1, settings=settings(), seconds_taken=120)
    assert session_plan.time_estimates(conn)[0] == 2.0


# ---- Steps with nothing to offer are skipped silently, and their time moves on --------------------------

def test_no_cards_means_no_learn_step(conn):
    topic_content(conn, cards=0)
    assert keys(session_plan.build_block(conn, subject="science", minutes=30, now=now())) == \
        ["focus", "practise", "check", "wrapup"]


def test_no_real_questions_means_no_practise_step(conn):
    topic_content(conn, real=())
    assert keys(session_plan.build_block(conn, subject="science", minutes=30, now=now())) == \
        ["focus", "learn", "check", "wrapup"]


def test_no_generated_questions_means_no_topic_check(conn):
    topic_content(conn, generated=())
    assert keys(session_plan.build_block(conn, subject="science", minutes=30, now=now())) == \
        ["focus", "learn", "practise", "wrapup"]


def test_cards_only_leaves_just_the_learn_step(conn):
    topic_content(conn, real=(), generated=())
    assert keys(session_plan.build_block(conn, subject="science", minutes=30, now=now())) == ["focus", "learn", "wrapup"]


def test_nothing_due_means_no_warm_up_and_a_due_card_brings_it_back(conn):
    topic_content(conn)
    assert "warmup" not in keys(session_plan.build_block(conn, subject="science", minutes=30, now=now()))
    c = card("card-due", "sci-old-topic")
    add_cards(conn, [c])
    revise.record_attempt(conn, c, method="paper", covered_ids=[], units_total=1, settings=settings(),
                          now=now().replace(year=2026, month=1, day=1))
    block = session_plan.build_block(conn, subject="science", minutes=30, now=now())
    assert keys(block)[0] == "warmup" and step(block, "warmup")["items"] == ["card-due"]
    assert "card-due" not in step(block, "learn")["items"]


def test_an_empty_step_gives_its_time_to_the_next_step(conn):
    # 8 one-mark questions are 9.6 minutes. With a topic check to reserve room for (T1), practise is cut
    # short; with no generated questions (T2) practise takes the whole budget.
    topic_content(conn, topic=T1, cards=0, real=(1,) * 8, generated=(1, 1, 1))
    topic_content(conn, topic=T2, cards=0, real=(1,) * 8, generated=())
    with_check = session_plan.build_block(conn, subject="science", minutes=10, now=now(), focus=T1)
    assert len(step(with_check, "practise")["items"]) == 5 and len(step(with_check, "check")["items"]) == 3
    without = session_plan.build_block(conn, subject="science", minutes=10, now=now(), focus=T2)
    assert len(step(without, "practise")["items"]) == 8 and "check" not in keys(without)


def test_nothing_to_study_means_no_session(conn):
    assert session.create_daily(conn, 30) is None
    assert "nothing to study" in session_plan.describe(conn, session_plan.build_block(conn, subject="", minutes=30, now=now())).lower()


# ---- Focus topic --------------------------------------------------------------------------------

def test_focus_is_the_topic_with_the_lowest_recent_first_attempt_score(conn, fake_marker):
    for t, order in ((T1, 1), (T2, 2), (T3, 3)):
        add_topic(conn, t, sort_order=order)
    qs = {t: mk(f"{t}-q", t, marks=2) for t in (T1, T2, T3)}
    add_questions(conn, list(qs.values()))
    answer(conn, qs[T1], good=True)
    answer(conn, qs[T2], good=False)
    answer(conn, qs[T3], good=True)
    block = session_plan.build_block(conn, subject="science", minutes=30, now=now())
    assert block["focus_topic"] == T2
    assert [a["topic"] for a in step(block, "focus")["alternatives"]] == [T1, T3]


def test_without_recent_scores_the_most_recently_taught_topic_then_tree_order(conn):
    for t, order in ((T1, 1), (T2, 2), (T3, 3)):
        add_topic(conn, t, sort_order=order)
        add_questions(conn, [mk(f"{t}-q", t)])
    assert session_plan.build_block(conn, subject="science", minutes=30, now=now())["focus_topic"] == T1
    set_topic_status(conn, T2, "taught")
    set_topic_status(conn, T3, "taught")
    conn.execute("UPDATE topic_status SET updated_at = '2026-01-01T00:00:00Z' WHERE topic = ?", (T2,))
    conn.commit()
    assert session_plan.build_block(conn, subject="science", minutes=30, now=now())["focus_topic"] == T3


def test_a_parents_focus_is_used_if_the_topic_is_taught(conn):
    for t, order in ((T1, 1), (T2, 2)):
        add_topic(conn, t, sort_order=order)
        add_questions(conn, [mk(f"{t}-q", t)])
    assert session_plan.build_block(conn, subject="science", minutes=30, now=now(), focus=T2)["focus_topic"] == T2


def test_only_taught_topics_can_be_the_focus(conn):
    add_questions(conn, [mk("q-high", T1, level="sec2"), mk("q-low", T2, level="sec1")])
    assert session_plan.build_block(conn, subject="science", minutes=30, now=now())["focus_topic"] == T2


# ---- Subject rotation ---------------------------------------------------------------------------

DAYS = [date(2026, 10, 19 + i) for i in range(4)]


def test_science_comes_up_twice_as_often(conn):
    add_questions(conn, [mk("s", T1), mk("g", "geo-intro", subject="geography"), mk("h", "his-one", subject="history")])
    assert Counter(session_plan.pick_subject(conn, d) for d in DAYS) == {"science": 2, "geography": 1, "history": 1}
    assert session_plan.pick_subject(conn, DAYS[0], override="history") == "history"   # a parent's choice wins


def test_subjects_with_nothing_to_study_are_skipped(conn):
    add_questions(conn, [mk("h", "his-one", subject="history")])
    assert {session_plan.pick_subject(conn, d) for d in DAYS} == {"history"}
    assert session_plan.pick_subject(conn, DAYS[0], override="geography") == "history"   # no content: not used


# ---- Session state: skip, swap, resume, break ---------------------------------------------------------

def make_session(conn, minutes=30, **content):
    topic_content(conn, **content)
    return session.get(conn, session.create_daily(conn, minutes, subject="science"))


def test_skips_are_logged_and_the_step_is_passed_over(conn):
    s = make_session(conn)
    session.finish_step(conn, s, "done")                          # focus
    assert session.position(s)["step"]["key"] == "learn"
    session.finish_step(conn, s, "skipped")
    s = session.get(conn, s["id"])
    assert session.position(s)["step"]["key"] == "practise"
    assert [e["step"] for e in s["plan"]["log"] if e["event"] == "skip"] == ["learn"]


def test_the_wrap_up_cannot_be_skipped_and_ends_the_session(conn):
    s = make_session(conn, real=(), generated=(), cards=2)
    for _ in range(2):                                            # focus, learn
        session.finish_step(conn, s, "skipped")
    assert session.position(s)["step"]["key"] == "wrapup"
    session.finish_step(conn, s, "skipped")
    assert session.position(s)["step"]["key"] == "wrapup"
    session.finish_step(conn, s, "done")
    assert session.get(conn, s["id"])["ended_at"] and session.position(session.get(conn, s["id"]))["state"] == "ended"


def test_swapping_the_focus_replans_the_steps_after_it(conn):
    for t, order in ((T1, 1), (T2, 2)):
        add_topic(conn, t, sort_order=order)
        topic_content(conn, topic=t)
    s = session.get(conn, session.create_daily(conn, 30, subject="science"))
    assert s["focus_topic"] == T1
    assert session.swap_focus(conn, s, "not-a-suggested-topic") is False
    assert session.swap_focus(conn, s, T2) is True
    s = session.get(conn, s["id"])
    assert s["focus_topic"] == T2 and all(i.startswith(T2) for i in step(session.current_block(s), "practise")["items"])
    assert keys(session.current_block(s)) == ["focus", "learn", "practise", "check", "wrapup"]
    assert [e["event"] for e in s["plan"]["log"]] == ["swap"]


def test_card_steps_finish_when_every_card_is_recalled(conn):
    s = make_session(conn, real=(), generated=(), cards=2)
    session.finish_step(conn, s, "done")                          # focus
    session.start_step(conn, s)
    learn = session.position(s)["step"]
    revise_set = revise.get_set(conn, learn["set_id"])
    assert revise_set["session_id"] == s["id"] and revise_set["card_ids"] == learn["items"]
    s = session.refresh(conn, s)
    assert session.position(s)["step"]["key"] == "learn"          # nothing recalled yet
    for card_id in revise_set["card_ids"]:
        revise.record_attempt(conn, {"id": card_id, "version": 1}, method="paper", covered_ids=[], units_total=1,
                              settings=settings(), set_id=revise_set["id"])
    s = session.refresh(conn, s)
    assert session.position(s)["step"]["key"] == "wrapup"


def test_question_steps_finish_when_every_question_has_a_first_attempt(conn, fake_marker):
    s = make_session(conn, cards=0, generated=(), real=(1, 2))
    session.finish_step(conn, s, "done")
    session.start_step(conn, s)
    answer(conn, mk(f"{T1}-r1", T1), good=True)
    s = session.refresh(conn, s)
    assert session.position(s)["step"]["key"] == "practise"
    assert session.next_question_url(conn, s, session.position(s)["step"]).startswith(f"/q/{T1}-r2?scope=")
    answer(conn, mk(f"{T1}-r2", T1, marks=2), good=False)
    assert session.position(session.refresh(conn, s))["step"]["key"] == "wrapup"


def test_an_unfinished_session_can_be_resumed_the_same_day_only(conn):
    s = make_session(conn)
    assert session.open_today(conn)["id"] == s["id"]
    assert session.open_today(conn, now().replace(year=2025)) is None
    for _ in range(10):
        session.finish_step(conn, s, "done")
    assert session.open_today(conn) is None


def test_a_break_comes_after_each_60_minute_block(conn):
    s = make_session(conn, minutes=90)
    assert s["block_count"] == 2 and session.current_block(s)["minutes"] == 60
    assert "wrapup" not in keys(session.current_block(s))          # the wrap-up belongs to the last block
    while session.position(s)["state"] == "step":
        session.finish_step(conn, s, "skipped")
    assert session.position(s)["state"] == "break"
    session.next_block(conn, s)
    s = session.get(conn, s["id"])
    assert s["block"] == 1 and session.position(s)["step"]["key"] == "wrapup"   # nothing left to offer: finish up
    assert session.current_block(s)["minutes"] == 30


def test_the_next_block_uses_a_different_topic_and_fresh_items(conn):
    for t, order in ((T1, 1), (T2, 2)):
        add_topic(conn, t, sort_order=order)
        topic_content(conn, topic=t)
    s = session.get(conn, session.create_daily(conn, 90, subject="science"))
    first = session.current_block(s)
    while session.position(s)["state"] == "step":
        session.finish_step(conn, s, "skipped")
    session.next_block(conn, s)
    second = session.current_block(session.get(conn, s["id"]))
    assert first["focus_topic"] == T1 and second["focus_topic"] == T2


def two_topic_session(conn, minutes=30):
    for t, order in ((T1, 1), (T2, 2)):
        add_topic(conn, t, sort_order=order)
        topic_content(conn, topic=t)
    return session.get(conn, session.create_daily(conn, minutes, subject="science"))


def skip_to_wrapup(conn, s):
    while session.position(s)["step"]["key"] != "wrapup":
        session.finish_step(conn, s, "skipped")


def test_keep_going_adds_a_block_after_a_break_on_a_different_topic(conn):
    s = two_topic_session(conn)
    assert session.extend(conn, s, 30) is False                       # not at the wrap-up yet
    skip_to_wrapup(conn, s)
    assert session.extend(conn, s, 45) is False                       # only the existing 30 / 60 choices
    assert session.extend(conn, s, 60) is True
    s = session.get(conn, s["id"])
    assert s["block_count"] == 2 and s["length_planned"] == 90 and s["ended_at"] is None
    assert "wrapup" not in keys(session.current_block(s))              # the wrap-up moves to the new block
    assert session.position(s)["state"] == "break"
    session.next_block(conn, s)
    s = session.get(conn, s["id"])
    block = session.current_block(s)
    assert s["block"] == 1 and block["minutes"] == 60 and s["subject"] == "science"
    assert block["focus_topic"] == T2 and s["plan"]["blocks"][0]["focus_topic"] == T1   # avoids the earlier focus
    used = {i for st in s["plan"]["blocks"][0]["steps"] for i in st["items"]}
    assert not used & {i for st in block["steps"] for i in st["items"]}               # and the earlier items
    assert keys(block)[-1] == "wrapup" and "next_minutes" not in s["plan"]
    session.finish_step(conn, s, "skipped")                            # focus
    skip_to_wrapup(conn, s)
    session.finish_step(conn, s, "done")
    assert session.get(conn, s["id"])["ended_at"]
    assert session.extend(conn, s, 30) is False                        # not after it has ended


def test_keep_going_is_for_daily_sessions_only(conn):
    add_questions(conn, [mk("gen-one", T1, source="generated")])
    s = session.get(conn, session.create_year_end(conn))
    session.finish_step(conn, s, "skipped")                            # the review step
    assert session.extend(conn, s, 30) is False


# ---- Year-end review --------------------------------------------------------------------------------

def test_year_end_uses_only_approved_generated_questions_from_taught_topics_weakest_first(conn, fake_marker):
    gen = {t: [mk(f"{t}-g{i}", t, source="generated") for i in (1, 2)] for t in (T1, T2)}
    add_questions(conn, gen[T1] + gen[T2] + [mk("real-one", T1), mk("hidden-g", T3, source="generated")])
    set_topic_status(conn, T3, "not_taught")
    add_questions(conn, [mk("attempted", T2)])
    answer(conn, mk("attempted", T2), good=False)                  # T2 is her weak topic
    block = session_plan.build_year_end(conn, minutes=30, now=now())
    items = step(block, "review")["items"]
    assert items == [f"{T2}-g1", f"{T2}-g2", f"{T1}-g1", f"{T1}-g2"]
    assert "real-one" not in items and "hidden-g" not in items


def test_year_end_needs_approved_generated_questions(conn):
    add_questions(conn, [mk("real-one", T1)])
    assert session.create_year_end(conn) is None
    add_questions(conn, [mk("gen-one", T1, source="generated")])
    s = session.get(conn, session.create_year_end(conn))
    assert s["kind"] == "year_end" and keys(session.current_block(s)) == ["review", "wrapup"]
