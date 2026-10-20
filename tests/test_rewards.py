"""Rewards (STUDENT_UI_SPEC.md section 8): XP and Shards rules, the daily cap, Daily Commissions per Singapore day,
rank-ups, the Reward Shop and the region map. Made-up content only; the fake marker, no API calls."""

import json
from datetime import datetime, timedelta, timezone

from app import practice, revise, rewards
from app import rewards_config as cfg
from app.revision_cards import get_card
from app.session_plan import STAMP, local_day
from app.topics import set_topic_status
from revise_helpers import add_cards, make_card, point, settings
from session_helpers import add_questions, add_topic, answer, mk

UTC = timezone.utc


def stamp(dt):
    return dt.astimezone(UTC).strftime(STAMP)


def now():
    return datetime.now(UTC)


def start_rewards_at(conn, text="2000-01-01T00:00:00Z"):
    rewards.set_meta(conn, "started_at", text)


def grant(conn, xp=0, shards=0, ref="grant", at=None):
    """Put XP or Shards straight into the ledger (for shop and rank tests)."""
    at = at or stamp(now() - timedelta(days=30))
    conn.execute("INSERT INTO reward_events (ref, kind, xp, shards, day, at, created_at) VALUES (?, 'test', ?, ?, ?, ?, ?)",
                 (ref, xp, shards, rewards.day_of(at), at, at))
    conn.commit()


def step(key, status="done", items=(), done_at=None):
    return {"key": key, "title": key, "status": status, "items": list(items), "est_min": 5, "set_id": None,
            "started_at": done_at, "done_at": done_at}


def add_session(conn, steps, *, started, ended=None):
    """A session row with the given steps. Its times are a few minutes in the past, which is before the moment the
    test database switched rewards on, so rewards are switched on earlier (as on a server that has run a while)."""
    start_rewards_at(conn)
    plan ={"blocks": [{"subject": "science", "focus_topic": "t1", "minutes": 30, "steps": steps}], "log": []}
    cur = conn.execute(
        "INSERT INTO sessions (kind, day, subject, started_at, ended_at, length_planned, block_count, plan_json, focus_topic) "
        "VALUES ('daily', ?, 'science', ?, ?, 30, 1, ?, 't1')",
        (rewards.day_of(started), started, ended, json.dumps(plan)))
    conn.commit()
    return cur.lastrowid


def by_kind(conn) -> dict:
    """{kind: (xp, shards)} summed over the ledger."""
    return {r["kind"]: (r["xp"], r["shards"]) for r in conn.execute(
        "SELECT kind, SUM(xp) AS xp, SUM(shards) AS shards FROM reward_events GROUP BY kind")}


def checked_session(conn, good: bool):
    """A finished session: warm-up done, focus done, practise skipped, a topic check of two questions."""
    add_topic(conn, "t1")
    qs = [mk("chk-1", "t1", source="generated"), mk("chk-2", "t1", source="generated")]
    add_questions(conn, qs)
    t0 = now() - timedelta(minutes=30)
    for q in qs:
        answer(conn, q, good)
    done = stamp(now() - timedelta(minutes=1))
    return add_session(conn, [step("warmup", done_at=done), step("focus", done_at=done),
                              step("practise", status="skipped", done_at=done),
                              step("check", items=["chk-1", "chk-2"], done_at=done), step("wrapup", done_at=done)],
                       started=stamp(t0), ended=done)


# ---- XP and Shards rules ----------------------------------------------------------------------------

def test_session_steps_a_passed_check_and_finishing_pay_xp_and_shards(conn, fake_marker):
    checked_session(conn, good=True)
    rewards.award_pending(conn)
    kinds = by_kind(conn)
    assert kinds["step_done"] == (2 * cfg.XP["step_done"], 0)     # warm-up and check; not focus, not the skipped step
    assert kinds["check_passed"] == (cfg.XP["check_passed"], 0)
    assert kinds["session_done"] == (cfg.XP["session_done"], cfg.SHARDS_PER_SESSION)


def test_a_failed_check_pays_the_step_but_not_the_pass(conn, fake_marker):
    checked_session(conn, good=False)
    rewards.award_pending(conn)
    kinds = by_kind(conn)
    assert "check_passed" not in kinds and kinds["step_done"][0] == 2 * cfg.XP["step_done"]


def test_a_session_with_every_step_skipped_earns_nothing(conn):
    t = stamp(now() - timedelta(minutes=5))
    add_session(conn, [step("warmup", status="skipped", done_at=t), step("wrapup", done_at=t)],
                started=stamp(now() - timedelta(minutes=10)), ended=t)
    rewards.award_pending(conn)
    assert rewards.totals(conn) == {"xp": 0, "shards": 0}


def test_rewrites_and_cards_moved_up_a_box_earn_xp(conn, fake_marker):
    add_topic(conn, "t1")
    q = mk("two-mark", "t1", marks=2)
    add_questions(conn, [q])
    first = practice.submit_first_attempt(conn, "two-mark", "kw1")          # 1 of 2: a rewrite is offered
    practice.submit_rewrite(conn, first, "kw1 kw2")
    climate = make_card("card-up", "Define a made-up word.", {"format": "bullets", "points": [point("A made-up meaning.")]})
    add_cards(conn, [climate])
    card = get_card(conn, "card-up")
    for known in (True, True, False):                                    # box 1 -> 2 -> 3 -> back to 1
        revise.record_attempt(conn, card, method="paper", covered_ids=["u1"] if known else [], units_total=1,
                              settings=settings())
    rewards.award_pending(conn)
    kinds = by_kind(conn)
    assert kinds["rewrite_done"] == (cfg.XP["rewrite_done"], 0)
    assert kinds["card_up"] == (2 * cfg.XP["card_up"], 0)


def test_reloading_pages_never_pays_twice(conn, fake_marker):
    checked_session(conn, good=True)
    for _ in range(3):
        rewards.award_pending(conn)
    once = rewards.totals(conn)
    rewards.award_pending(conn)
    assert rewards.totals(conn) == once and once["xp"] > 0


def test_effort_from_before_rewards_were_switched_on_does_not_count(conn, fake_marker):
    checked_session(conn, good=True)
    start_rewards_at(conn, stamp(now() + timedelta(hours=1)))
    rewards.award_pending(conn)
    assert rewards.totals(conn) == {"xp": 0, "shards": 0}


def test_the_daily_xp_cap_is_two_full_sessions_and_counts_by_local_day(conn):
    typical = sum(cfg.XP[k] * n for k, n in cfg.TYPICAL_60_MIN.items())
    assert cfg.DAILY_XP_CAP == 2 * typical
    day_one = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)                  # 23:00 in Singapore on the 20th
    day_two = datetime(2026, 10, 20, 16, 30, tzinfo=UTC)                 # 00:30 in Singapore on the 21st
    conn.execute("BEGIN")
    for i in range(10):
        rewards._pay(conn, f"x{i}", "card_up", stamp(day_one), xp=100)
    rewards._pay(conn, "next-day", "card_up", stamp(day_two), xp=100)
    conn.commit()
    assert rewards.xp_on_day(conn, "2026-10-20") == cfg.DAILY_XP_CAP
    assert rewards.xp_on_day(conn, "2026-10-21") == 100


def test_only_the_first_two_sessions_of_a_day_pay_shards(conn):
    for i in range(3):
        t = stamp(now() - timedelta(minutes=30 - i))
        add_session(conn, [step("warmup", done_at=t), step("wrapup", done_at=t)], started=t, ended=t)
    rewards.award_pending(conn)
    rows = conn.execute("SELECT shards FROM reward_events WHERE kind = 'session_done' ORDER BY id").fetchall()
    assert [r[0] for r in rows] == [cfg.SHARDS_PER_SESSION, cfg.SHARDS_PER_SESSION, 0]


# ---- Daily Commissions ------------------------------------------------------------------------------

def set_commissions(conn, day, keys):
    conn.executemany("INSERT INTO commissions (day, key, position) VALUES (?, ?, ?)",
                     [(day, k, i) for i, k in enumerate(keys)])
    conn.commit()


def test_commissions_count_by_the_singapore_day(conn, fake_marker):
    start_rewards_at(conn)
    add_topic(conn, "t1")
    qs = [mk(f"p{i}", "t1") for i in range(4)]
    add_questions(conn, qs)
    ids = [answer(conn, q, True) for q in qs]
    times = ["2026-10-20T15:57:00Z", "2026-10-20T15:58:00Z", "2026-10-20T15:59:00Z",   # 23:57-23:59 on the 20th
             "2026-10-20T16:00:00Z"]                                                   # 00:00 on the 21st
    for i, t in zip(ids, times):
        conn.execute("UPDATE attempts SET created_at = ? WHERE id = ?", (t, i))
    conn.commit()
    for day in ("2026-10-20", "2026-10-21"):
        set_commissions(conn, day, ["finish_session", "practise", "one_rewrite"])
    when = datetime(2026, 10, 21, 1, 0, tzinfo=UTC)                         # 09:00 on the 21st
    rewards.award_pending(conn, when)
    done = {(r["day"], r["key"]): r["done_at"] for r in conn.execute("SELECT * FROM commissions")}
    assert done[("2026-10-20", "practise")] == "2026-10-20T15:59:00Z"    # the third answer completed it
    assert done[("2026-10-21", "practise")] is None
    today = rewards.commissions_today(conn, when)
    practise = next(c for c in today["items"] if c["key"] == "practise")
    assert (practise["have"], practise["done"]) == (1, False)
    paid = conn.execute("SELECT ref, shards FROM reward_events WHERE kind = 'commission'").fetchall()
    assert [(r[0], r[1]) for r in paid] == [("commission:2026-10-20:practise", cfg.SHARDS_PER_COMMISSION)]


def test_all_three_commissions_pay_the_bonus_once(conn, fake_marker):
    add_topic(conn, "t1")
    qs = [mk(f"p{i}", "t1") for i in range(3)] + [mk("two", "t1", marks=2)]
    add_questions(conn, qs)
    for q in qs[:3]:
        answer(conn, q, True)
    first = practice.submit_first_attempt(conn, "two", "kw1")
    practice.submit_rewrite(conn, first, "kw1 kw2")
    t = stamp(now())
    add_session(conn, [step("warmup", done_at=t), step("wrapup", done_at=t)], started=t, ended=t)
    set_commissions(conn, local_day(now()).isoformat(), ["finish_session", "practise", "one_rewrite"])
    rewards.award_pending(conn)
    rewards.award_pending(conn)
    kinds = by_kind(conn)
    assert kinds["commission"] == (0, 3 * cfg.SHARDS_PER_COMMISSION)
    assert kinds["commission_bonus"] == (0, cfg.COMMISSIONS_BONUS)
    assert rewards.commissions_today(conn)["all_done"]


def test_commissions_are_chosen_by_date_and_stay_fixed_for_the_day(conn):
    day = datetime(2026, 10, 20).date()
    picks = rewards.choose_commissions(conn, day)
    assert len(picks) == cfg.COMMISSIONS_PER_DAY == len(set(picks)) and picks[0] == cfg.ALWAYS_OFFERED
    assert picks == rewards.choose_commissions(conn, day)                # no randomness
    week = [rewards.choose_commissions(conn, day + timedelta(days=k)) for k in range(7)]
    assert all("pass_check" not in p for p in week)                      # no topic-check questions yet
    assert len({tuple(p) for p in week}) > 1                             # the rest rotate
    when = datetime(2026, 10, 20, 2, 0, tzinfo=UTC)
    first = [c["key"] for c in rewards.commissions_today(conn, when)["items"]]
    add_topic(conn, "t1")
    add_questions(conn, [mk("gen", "t1", source="generated")])
    assert [c["key"] for c in rewards.commissions_today(conn, when)["items"]] == first   # fixed once chosen
    assert any("pass_check" in rewards.choose_commissions(conn, day + timedelta(days=k)) for k in range(4))


# ---- Rank -----------------------------------------------------------------------------------------------

def test_rank_thresholds():
    assert rewards.rank_for(0)["rank"] == 1 and rewards.rank_for(149)["rank"] == 1
    r = rewards.rank_for(150)
    assert (r["rank"], r["title"], r["to_next"]) == (2, cfg.RANKS[1][1], cfg.RANKS[2][0] - 150)
    top = rewards.rank_for(cfg.RANKS[-1][0] + 5000)
    assert top["rank"] == len(cfg.RANKS) and top["top"] and top["pct"] == 100


def test_a_session_that_crosses_a_threshold_ranks_up_and_names_new_shop_items(conn):
    grant(conn, xp=140)
    t0, t1 = now() - timedelta(minutes=20), now() - timedelta(minutes=2)
    sid = add_session(conn, [step("warmup", done_at=stamp(t1)), step("wrapup", done_at=stamp(t1))],
                      started=stamp(t0), ended=stamp(t1))
    rewards.award_pending(conn)
    s = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    earned = rewards.session_earnings(conn, dict(s))
    assert earned["xp"] == cfg.XP["step_done"] + cfg.XP["session_done"]   # the earlier 140 isn't this session's
    assert earned["shards"] >= cfg.SHARDS_PER_SESSION
    assert earned["ranked_up"] and earned["rank"]["rank"] == 2
    assert {i["id"] for i in earned["new_items"]} == {i["id"] for i in cfg.SHOP if i["min_rank"] == 2}


# ---- Reward Shop ---------------------------------------------------------------------------------------

def test_shop_purchase_checks_rank_and_balance_on_the_server(conn):
    assert rewards.buy(conn, "no-such-item") == "unknown"
    assert rewards.buy(conn, "frame-oak") == "short"                     # 0 Shards
    grant(conn, shards=100)
    assert rewards.buy(conn, "frame-gilded") == "locked"                 # needs rank 3
    assert rewards.buy(conn, "frame-oak") == "bought"
    assert rewards.totals(conn)["shards"] == 100 - 30
    assert rewards.buy(conn, "frame-oak") == "owned"
    assert rewards.totals(conn)["shards"] == 70                          # not charged twice
    states = {i["id"]: i["state"] for i in rewards.shop_items(conn)}
    assert states["frame-oak"] == "owned" and states["frame-gilded"] == "locked"


def test_equip_needs_ownership_and_feeds_the_theme(conn):
    grant(conn, xp=200, shards=100)                                      # rank 2
    assert not rewards.equip(conn, "badge-pathfinder")                   # not bought yet
    assert rewards.buy(conn, "badge-pathfinder") == "bought" and rewards.equip(conn, "badge-pathfinder")
    assert rewards.rank_info(conn)["badge"] == "Pathfinder"
    assert rewards.buy(conn, "frame-oak") == "bought" and rewards.equip(conn, "frame-oak")
    assert rewards.cosmetics(conn)["frame"] == "frame-oak"
    rewards.unequip(conn, "frame")
    assert rewards.cosmetics(conn)["frame"] == ""
    assert rewards.cosmetics(conn)["companion"] == cfg.COMPANION_DEFAULT_IMAGE
    rewards.set_meta(conn, "companion_image", "/uploads/companion.png")  # what the 5b upload will set
    assert rewards.cosmetics(conn)["companion"] == "/uploads/companion.png"


# ---- Region map ------------------------------------------------------------------------------------------

def test_topic_state_rules():
    state = rewards.topic_state
    assert state(active=False, has_questions=True, good_questions=9, has_cards=True, avg_box=5) == "not_started"
    assert state(active=True, has_questions=True, good_questions=3, has_cards=False, avg_box=1) == "secure"
    assert state(active=True, has_questions=True, good_questions=2, has_cards=False, avg_box=1) == "learning"
    assert state(active=True, has_questions=False, good_questions=0, has_cards=True, avg_box=3) == "secure"
    assert state(active=True, has_questions=False, good_questions=0, has_cards=True, avg_box=2.9) == "learning"
    # both: both must hold
    assert state(active=True, has_questions=True, good_questions=3, has_cards=True, avg_box=2) == "learning"
    assert state(active=True, has_questions=True, good_questions=1, has_cards=True, avg_box=4) == "learning"
    assert state(active=True, has_questions=True, good_questions=3, has_cards=True, avg_box=3) == "secure"


def test_map_states_come_from_her_work_and_only_taught_topics_appear(conn, fake_marker):
    for i, t in enumerate(("t-new", "t-learning", "t-secure", "t-both", "t-off")):
        add_topic(conn, t, sort_order=i, title=f"Made-up {t}")
    add_questions(conn, [mk(f"{t}-{k}", t) for t in ("t-new", "t-learning", "t-secure", "t-both") for k in range(3)])
    for k in range(3):
        answer(conn, {"id": f"t-secure-{k}", "marks": 1}, True)
        answer(conn, {"id": f"t-both-{k}", "marks": 1}, True)
    answer(conn, {"id": "t-learning-0", "marks": 1}, True)
    answer(conn, {"id": "t-learning-1", "marks": 1}, False)
    add_cards(conn, [make_card("card-both", "Made-up prompt?", {"format": "bullets", "points": [point("Made up.")]},
                               topic="t-both", subject="science")])
    set_topic_status(conn, "t-off", "not_taught")
    states = {w["topic"]: w["state"] for r in rewards.map_view(conn) for w in r["waypoints"]}
    assert states == {"t-new": "not_started", "t-learning": "learning", "t-secure": "secure", "t-both": "learning"}
    card = get_card(conn, "card-both")
    for _ in range(2):                                                   # box 1 -> 3
        revise.record_attempt(conn, card, method="paper", covered_ids=["u1"], units_total=1, settings=settings())
    states = {w["topic"]: w["state"] for r in rewards.map_view(conn) for w in r["waypoints"]}
    assert states["t-both"] == "secure"


def test_trail_layout_winds_back_and_forth():
    t = rewards.trail(8)
    xs = [x for x, _ in t["points"]]
    assert xs[:6] == sorted(xs[:6]) and xs[6] > xs[7]                    # row 2 runs right to left
    assert t["path"].startswith("M 10 10") and t["height"] == 2 * 16 + 4
