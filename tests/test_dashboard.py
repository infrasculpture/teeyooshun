"""The parent dashboard (docs/PARENT_DASHBOARD_SPEC.md): a fixture database of made-up rows, no API calls."""

import logging
from dataclasses import replace
from datetime import date

import pytest

from app import dashboard, pricing, schedule
from app.config import get_settings
from conftest import PARENT_PIN
from dashboard_helpers import NOW, add_api_call, add_attempt, add_event, add_recall, add_session
from revise_helpers import CLIMATE, RAIN, add_cards
from session_helpers import add_questions, add_topic, mk

POINTS_Q = "sci-test-points-q1"      # 2 marks, science (the shared fixture)
LORMS_Q = "his-test-lorms-q1"        # 5 marks, history (the shared fixture)


def login(client):
    client.post("/parent/login", data={"pin": PARENT_PIN, "next": "/"}, follow_redirects=False)


def build(conn, period="7", **settings):
    return dashboard.build(conn, period, NOW, replace(get_settings(), **settings) if settings else None)


# ---- Every section builds, on an empty database and on a full one ---------------------------------------------

@pytest.mark.parametrize("period", list(dashboard.PERIODS))
def test_every_section_builds_on_an_empty_database(bank, period):
    d = build(bank, period)
    for section in ("glance", "trend", "topics", "technique", "help", "habits", "revise", "rewards", "cost"):
        assert section in d
    assert d["glance"]["questions"] == 0 and d["glance"]["avg_pct"] is None


# ---- Period filter and first-attempt scoring -----------------------------------------------------------------------

def test_period_boundaries_follow_singapore_midnight(bank):
    """7 days = 14 Oct 00:00 to 20 Oct 24:00 Singapore time, i.e. 13 Oct 16:00Z to 20 Oct 16:00Z."""
    add_attempt(bank, POINTS_Q, "2026-10-13T15:59:59Z", 0, 2)   # 13 Oct 23:59:59 SGT: the previous period
    add_attempt(bank, POINTS_Q, "2026-10-13T16:00:00Z", 2, 2)   # 14 Oct 00:00:00 SGT: first second of the period
    add_attempt(bank, POINTS_Q, "2026-10-20T15:59:59Z", 1, 2)   # 20 Oct 23:59:59 SGT: last second
    add_attempt(bank, POINTS_Q, "2026-10-20T16:00:00Z", 0, 2)   # 21 Oct 00:00:00 SGT: not yet
    p = dashboard.make_period(bank, "7", NOW)
    assert (p.start, p.end) == ("2026-10-13T16:00:00Z", "2026-10-20T16:00:00Z")
    assert (p.prev_start, p.prev_end) == ("2026-10-06T16:00:00Z", "2026-10-13T16:00:00Z")
    g = build(bank)["glance"]
    assert g["questions"] == 2 and g["avg_pct"] == 75.0
    assert g["prev_pct"] == 0.0 and g["change"] == 75.0
    assert build(bank, "30")["glance"]["questions"] == 3                 # the 13 Oct 23:59:59 one joins; 21 Oct never does
    assert dashboard.make_period(bank, "bogus", NOW).key == "7"


def test_only_first_attempts_count_and_rewrites_are_shown_separately(bank):
    first = add_attempt(bank, POINTS_Q, "2026-10-15T04:00:00Z", 1, 2)
    add_attempt(bank, POINTS_Q, "2026-10-15T04:05:00Z", 2, 2, parent=first)
    d = build(bank)
    assert d["glance"]["questions"] == 1 and d["glance"]["avg_pct"] == 50.0
    assert d["technique"]["rewrites"] == {"offered": 1, "done": 1, "skipped": 0, "avg_gain": 1.0}


def test_rewrite_skips_are_counted(bank):
    first = add_attempt(bank, POINTS_Q, "2026-10-15T04:00:00Z", 0, 2)
    add_event(bank, first, "rewrite_skipped", "student")
    assert build(bank)["technique"]["rewrites"] == {"offered": 1, "done": 0, "skipped": 1, "avg_gain": None}


# ---- Topics and flags -------------------------------------------------------------------------------------------

def five_topics(conn):
    for i, pct in enumerate((100, 20, 40, 60, 80), 1):
        add_topic(conn, f"t{i}", sort_order=i)
        add_questions(conn, [mk(f"q{i}", f"t{i}", marks=5)])
        for _ in range(3):
            add_attempt(conn, f"q{i}", "2026-10-18T04:00:00Z", pct // 20, 5)


def flagged(d):
    return {r["topic"] for r in d["topics"]["science"] if r["flag"]}


def test_the_three_weakest_topics_with_enough_attempts_are_flagged(bank):
    five_topics(bank)
    d = build(bank)
    rows = {r["topic"]: r for r in d["topics"]["science"]}
    assert [rows[f"t{i}"]["avg"] for i in range(1, 6)] == [100.0, 20.0, 40.0, 60.0, 80.0]
    assert flagged(d) == {"t2", "t3", "t4"}
    assert rows["t2"]["flag"] == "low average" and rows["t1"]["n"] == 3


def test_a_topic_with_too_few_attempts_is_not_flagged_for_its_score(bank):
    add_topic(bank, "t1")
    add_questions(bank, [mk("q1", "t1", marks=5)])
    add_attempt(bank, "q1", "2026-10-18T04:00:00Z", 0, 5)
    add_attempt(bank, "q1", "2026-10-18T05:00:00Z", 0, 5)       # 2 attempts, both zero, but recent
    assert flagged(build(bank)) == set()


def test_a_taught_topic_idle_for_14_days_is_flagged_and_the_trend_arrow_compares_periods(bank):
    five_topics(bank)
    add_topic(bank, "t6", sort_order=6)
    add_questions(bank, [mk("q6", "t6", marks=5)])
    add_attempt(bank, "q6", "2026-09-01T04:00:00Z", 5, 5)       # last touched long ago
    d = build(bank)
    rows = {r["topic"]: r for r in d["topics"]["science"]}
    assert rows["t6"]["flag"] == "no activity in 14 days" and rows["t6"]["idle"]
    assert flagged(d) == {"t6", "t2", "t3"}
    add_attempt(bank, "q2", "2026-10-10T04:00:00Z", 4, 5)       # previous period: 80%, now 20%
    assert {r["topic"]: r for r in build(bank)["topics"]["science"]}["t2"]["trend"] == "▼"


def test_the_flagged_topic_button_sets_the_existing_focus_setting_for_7_days(client, bank):
    assert client.post("/parent/dashboard/focus", data={"subject": "science", "topic": "sci-07-particulate-nature"}
                       ).status_code == 403
    login(client)
    r = client.post("/parent/dashboard/focus", data={"subject": "science", "topic": "sci-07-particulate-nature",
                                                       "period": "30"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/parent/dashboard?period=30#topics"
    focus = schedule.get_focus(bank)["science"]
    assert focus["topic"] == "sci-07-particulate-nature"
    assert (date.fromisoformat(focus["until"]) - date.today()).days in (6, 7, 8)   # 7 days from Singapore's today
    bad = client.post("/parent/dashboard/focus", data={"subject": "science", "topic": "nope"})
    assert bad.status_code == 400


# ---- Exam technique, from the marking JSON ----------------------------------------------------------------------

def test_command_words_key_terms_reversals_and_waffle_come_from_the_marking_json(bank):
    add_topic(bank, "t1")
    add_questions(bank, [mk("q-state", "t1", marks=2, command="state"), mk("q-explain", "t1", marks=3, command="explain")])
    quiet = {"points": [], "missing_key_terms": [], "filler_quotes": []}
    add_attempt(bank, "q-state", "2026-10-18T04:00:00Z", 2, 2, detail=quiet)
    add_attempt(bank, "q-explain", "2026-10-18T05:00:00Z", 1, 3, answer="ok", detail={
        "points": [{"id": "MP1", "awarded": False, "reversed": True, "evidence_quote": "gas gets heavier", "reason": ""}],
        "missing_key_terms": ["Diffusion", "particles"], "filler_quotes": ["you know", "like"]})
    add_attempt(bank, "q-explain", "2026-10-18T06:00:00Z", 2, 3, answer="ok", detail={
        "points": [], "missing_key_terms": ["diffusion"], "filler_quotes": []})
    t = build(bank)["technique"]
    assert [(c["word"], c["n"], c["pct"]) for c in t["command_words"]] == [("explain", 2, 50.0), ("state", 1, 100.0)]
    assert t["missed_terms"][0] == {"term": "diffusion", "n": 2, "topic": "t1"}
    assert {m["term"] for m in t["missed_terms"]} == {"diffusion", "particles"}
    assert t["reversed_count"] == 1 and t["reversed_recent"][0]["phrase"] == "gas gets heavier"
    assert t["reversed_recent"][0]["question"] == "Made-up question q-explain?"
    assert t["points_answers"] == 3 and t["waffle_share"] == 33 and t["waffle_words"] == 1.0
    assert t["multi_mark"] == 3 and t["too_short"] == 2 and t["too_long"] == 0      # the two 1-word, mark-losing answers


def test_lorms_levels_and_what_the_next_level_needs(bank):
    for level in (2, 2, 3):
        add_attempt(bank, LORMS_Q, "2026-10-18T04:00:00Z", level, 5, level=level,
                    tutor={"lorms": {"next_level_needs": "Explain the link to the source."}})
    add_attempt(bank, LORMS_Q, "2026-10-18T05:00:00Z", 3, 5, level=3, tutor={"lorms": {"next_level_needs": "Add evidence"}})
    t = build(bank)["technique"]
    assert t["levels"] == [(2, 2), (3, 2)] and t["lorms_answers"] == 4
    assert t["next_level_needs"][0] == ("explain the link to the source", 3)


# ---- Help and discussion ------------------------------------------------------------------------------------------

def test_with_help_and_without_help_are_split(bank):
    add_attempt(bank, POINTS_Q, "2026-10-18T04:00:00Z", 1, 2, with_help=1)
    add_attempt(bank, POINTS_Q, "2026-10-18T05:00:00Z", 2, 2)
    for tier in (1, 3, 4):
        bank.execute("INSERT INTO hint_uses (question_id, tier, created_at) VALUES (?, ?, '2026-10-18T04:00:00Z')", (POINTS_Q, tier))
    bank.commit()
    h = build(bank)["help"]
    assert (h["with_help_share"], h["with_help_pct"], h["without_help_pct"]) == (50, 50.0, 100.0)
    assert h["tiers"] == [{"subject": "science", "counts": [1, 0, 1, 1]}]


def test_discussions_and_the_looks_wrong_queue(bank):
    bank.execute("INSERT INTO discussions (conv_key, kind, scope_id, stage, messages_json, student_messages, created_at, updated_at) "
                 "VALUES ('conv-aaaaaa', 'question', ?, 'before', '[]', 3, '2026-10-18T04:00:00Z', '2026-10-18T04:00:00Z')", (POINTS_Q,))
    bank.commit()
    waiting = add_attempt(bank, POINTS_Q, "2026-10-18T04:00:00Z", 0, 2)
    handled = add_attempt(bank, POINTS_Q, "2026-10-18T05:00:00Z", 0, 2)
    add_event(bank, waiting, "marking_looks_wrong", "student")
    add_event(bank, handled, "marking_looks_wrong", "student")
    add_event(bank, handled, "parent_agree", "parent")
    h = build(bank)["help"]
    assert (h["conversations"], h["messages"]) == (1, 3) and h["top_topics"] == [("sci-07-particulate-nature", 1)]
    assert h["looks_wrong"] == 1 and h["looks_wrong_ids"] == [waiting]


# ---- Habits, revise, rewards ------------------------------------------------------------------------------------------

def test_sessions_heatmap_time_on_task_and_skipped_steps(bank):
    add_session(bank, "2026-10-18T14:30:00Z", [      # Sunday 22:30 Singapore time
        {"key": "warmup", "status": "done", "started_at": "2026-10-18T14:30:00Z", "done_at": "2026-10-18T14:40:00Z"},
        {"key": "learn", "status": "skipped", "started_at": None, "done_at": "2026-10-18T14:41:00Z"}],
        ended_at="2026-10-18T14:50:00Z")
    add_session(bank, "2026-10-19T04:00:00Z", [])
    d = build(bank)
    assert d["glance"]["sessions_started"] == 2 and d["glance"]["sessions_completed"] == 1 and d["glance"]["minutes"] == 10
    assert d["habits"]["grid"][6][22] == 1 and d["habits"]["grid"][0][12] == 1       # Sun 22h; Mon 12h
    assert d["habits"]["steps"] == [{"kind": "daily", "done": 1, "skipped": 1}]
    assert d["habits"]["most_skipped"] == ("Learn", 1)
    assert "<svg" in d["habits"]["heatmap_svg"]


def test_time_per_mark_ignores_untimed_and_left_open_pages(bank):
    add_attempt(bank, POINTS_Q, "2026-10-18T04:00:00Z", 1, 2, seconds=120)
    add_attempt(bank, POINTS_Q, "2026-10-18T05:00:00Z", 1, 2, seconds=None)
    add_attempt(bank, POINTS_Q, "2026-10-18T06:00:00Z", 1, 2, seconds=99999)
    assert build(bank)["habits"]["minutes_per_mark"] == [{"subject": "science", "minutes": 1.0}]


def test_revise_figures(bank):
    add_cards(bank, [CLIMATE, RAIN])
    bank.execute("INSERT INTO card_progress (card_id, box, last_attempt_at, due_at) VALUES ('card-climate', 4, '2026-10-18T04:00:00Z', '2026-11-30T00:00:00Z')")
    bank.execute("INSERT INTO card_progress (card_id, box, last_attempt_at, due_at) VALUES ('card-rain', 1, '2026-10-18T04:00:00Z', '2026-10-19T00:00:00Z')")
    bank.commit()
    add_recall(bank, "card-rain", "2026-10-18T04:00:00Z", 2, 3)
    add_recall(bank, "card-climate", "2026-10-18T04:05:00Z", 1, 1, method="parent_quiz")
    r = build(bank)["revise"]
    assert (r["seen"], r["known"], r["due"]) == (2, 1, 1)
    assert r["methods"] == [{"method": "typed", "n": 1, "rate": 67}, {"method": "parent_quiz", "n": 1, "rate": 100}]
    assert build(bank)["glance"]["cards_recalled"] == 2


def test_rewards_section_counts_commissions_in_the_period(bank):
    bank.execute("INSERT INTO commissions (day, key, position, done_at) VALUES ('2026-10-18', 'practise', 0, '2026-10-18T04:00:00Z')")
    bank.execute("INSERT INTO commissions (day, key, position, done_at) VALUES ('2026-09-01', 'practise', 0, '2026-09-01T04:00:00Z')")
    bank.commit()
    r = build(bank)["rewards"]
    assert r["commissions"] == 1 and r["pending"] == 0 and r["rank"] >= 1


# ---- Cost ------------------------------------------------------------------------------------------------------------

def add_calls(conn):
    add_api_call(conn, "2026-10-05T04:00:00Z", "marking", "claude-sonnet-5-5", input_tokens=1_000_000)           # $2.00
    add_api_call(conn, "2026-10-05T04:00:00Z", "recall", "claude-haiku-4-5-20251001", output_tokens=1_000_000)   # $5.00
    add_api_call(conn, "2026-10-05T04:00:00Z", "tutor_explanation", "claude-opus-5-5", cache_write=1_000_000)    # $5.00
    add_api_call(conn, "2026-10-05T04:00:00Z", "ask_about_this", "claude-mystery-9", input_tokens=1000)          # no price
    add_api_call(conn, "2026-10-05T04:00:00Z", "calibration", "claude-sonnet-5-5", input_tokens=500_000)         # $1.00
    add_api_call(conn, "2026-09-30T15:59:59Z", "marking", "claude-sonnet-5-5", input_tokens=9_000_000)           # last month
    add_api_call(conn, "2026-09-30T16:00:00Z", "hint", "claude-sonnet-5-5")                                      # 1 Oct 00:00 SGT, no tokens


def test_cost_is_split_by_feature_with_revise_checks_on_its_own_line(bank):
    add_calls(bank)
    c = build(bank, usd_sgd_rate=2.0, subscription_sgd=10.0, monthly_budget_sgd=100.0)["cost"]
    by = {line["key"]: line for line in c["lines"]}
    assert by["marking"]["usd"] == 2.0 and by["revise_checks"]["usd"] == 5.0 and by["revise_checks"]["label"] == "Revise checks"
    assert by["teaching"]["usd"] == 5.0           # Opus cache write: 5.00 per million
    assert by["other"]["usd"] == 1.0 and by["ask"]["usd"] == 0.0
    assert c["total_usd"] == 13.0 and c["total_sgd"] == 26.0
    assert c["unpriced_calls"] == 1               # the call served by a model with no price
    assert c["month"] == "October 2026"


def test_projection_and_budget_colour(bank):
    add_calls(bank)
    c = build(bank, usd_sgd_rate=2.0, subscription_sgd=10.0, monthly_budget_sgd=100.0)["cost"]
    assert c["days_elapsed"] == 20 and c["days_in_month"] == 31
    assert c["projected_api_sgd"] == 40.3 and c["projected_total_sgd"] == 50.3 and c["so_far_sgd"] == 36.0
    assert (c["share"], c["colour"]) == (50, "green")
    assert build(bank, usd_sgd_rate=2.0, subscription_sgd=10.0, monthly_budget_sgd=60.0)["cost"]["colour"] == "amber"
    assert build(bank, usd_sgd_rate=2.0, subscription_sgd=10.0, monthly_budget_sgd=45.0)["cost"]["colour"] == "red"


def test_budget_colour_thresholds():
    assert [dashboard.budget_colour(s) for s in (0, 0.79, 0.8, 0.99, 1.0, 1.5, None)] == \
        ["green", "green", "amber", "amber", "red", "red", "green"]


# ---- Pricing: cache writes, dated model names, and the start-up warning -----------------------------------------------

def test_prices_include_cache_writes_and_dated_model_names():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_creation_input_tokens": 1_000_000,
             "cache_read_input_tokens": 1_000_000}
    assert pricing.estimate_usd("claude-opus-5-5", usage) == pytest.approx(4 + 20 + 5 + 0.20)
    assert pricing.estimate_usd("claude-sonnet-5-5", usage) == pytest.approx(2 + 10 + 2.50 + 0.10)
    assert pricing.estimate_usd("claude-haiku-4-5", usage) == pytest.approx(1 + 5 + 1.25 + 0.10)
    assert pricing.estimate_usd("claude-haiku-4-5-20251001", usage) == pricing.estimate_usd("claude-haiku-4-5", usage)
    assert pricing.estimate_usd("claude-mystery-9", usage) is None


def test_unpriced_configured_models_are_found_and_logged(caplog):
    settings = replace(get_settings(), marking_model="claude-sonnet-5-5", light_model="claude-haiku-4-5-20251001",
                       ingest_model="claude-mystery-9")
    assert pricing.unpriced_models(settings) == ["claude-mystery-9"]
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        assert pricing.warn_unpriced(settings) == ["claude-mystery-9"]
    assert "claude-mystery-9" in caplog.text
    ok = replace(settings, ingest_model="claude-sonnet-5-5")
    assert pricing.unpriced_models(ok) == []


def test_the_models_in_env_example_all_have_prices():
    from pathlib import Path
    text = (Path(__file__).parent.parent / ".env.example").read_text(encoding="utf-8")
    models = {line.split("=", 1)[1].strip() for line in text.splitlines()
              if line.startswith(("MARKING_MODEL=", "LIGHT_MODEL=")) and line.split("=", 1)[1].strip()}
    assert models and all(pricing.price_for(m) for m in models)


# ---- The page ----------------------------------------------------------------------------------------------------------

def test_the_page_needs_the_pin(client, bank):
    r = client.get("/parent/dashboard", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/parent/login")
    login(client)
    assert client.get("/parent/dashboard").status_code == 200


def test_charts_are_inline_svg_with_their_numbers_as_text(client, bank):
    from app.db import utc_now
    add_attempt(bank, POINTS_Q, utc_now(), 1, 2, detail={"points": [], "missing_key_terms": ["diffusion"], "filler_quotes": []})
    login(client)
    page = client.get("/parent/dashboard?period=30").text
    assert "<svg" in page and "Target 75%" in page and "Start 45%" in page
    assert "50.0%" in page                                  # the same number, as text, in the trend table and tiles
    assert "diffusion" in page and "Make this week's focus" in page
    assert "Revise checks" in page and "Budget status" in page
    assert "chart.js" not in page.lower()                   # no chart library
    for key in dashboard.PERIODS:
        assert client.get(f"/parent/dashboard?period={key}").status_code == 200


def test_migration_adds_the_dashboard_indexes(bank):
    names = {r[0] for r in bank.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_attempts_first_created", "idx_recall_created", "idx_hint_uses_created", "idx_events_created",
            "idx_sessions_started", "idx_api_calls_purpose"} <= names
    assert bank.execute("PRAGMA user_version").fetchone()[0] == 17
