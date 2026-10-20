"""Parent settings, task 5b: weekday defaults, focus this week, real-world rewards held through the ledger, picture
uploads, the K-pop celebration switch and the settings sections. Made-up content only; no API calls."""

import io
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from app import app_settings, rewards, schedule, session, session_plan, uploads
from app.session_plan import STAMP
from app.topics import set_topic_status
from conftest import PARENT_PIN
from session_helpers import add_questions, add_topic, mk

UTC = timezone.utc
NOON = datetime(2026, 10, 20, 4, 0, tzinfo=UTC)      # noon on Tuesday 20 Oct 2026 in Singapore
TODAY = date(2026, 10, 20)
POINTS_Q = "sci-test-points-q1"
FULL = "The balloon expands as particles move further apart."


def login(client):
    client.post("/parent/login", data={"pin": PARENT_PIN, "next": "/"}, follow_redirects=False)


def give(conn, shards=0, xp=0, ref="gift", session_id=None):
    at = datetime.now(UTC).strftime(STAMP)
    conn.execute("INSERT INTO reward_events (ref, kind, xp, shards, day, session_id, at, created_at) "
                 "VALUES (?, 'test', ?, ?, ?, ?, ?, ?)", (ref, xp, shards, rewards.day_of(at), session_id, at, at))
    conn.commit()


def week(minutes="30", subject="", **days):
    """The posted schedule form: every day the same, except days given as Name=(minutes, subject)."""
    form = {}
    for name in schedule.WEEKDAYS:
        m, s = days.get(name, (minutes, subject))
        form[f"minutes_{name}"], form[f"subject_{name}"] = m, s
    return form


def png(size=(40, 30), colour=(200, 30, 30), **save):
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG", **save)
    return buf.getvalue()


# ---- Weekday defaults ------------------------------------------------------------------------------------

def test_every_day_is_30_minutes_and_automatic_until_the_parent_changes_it(conn):
    assert all(d["minutes"] == 30 and d["subject"] == "" for d in schedule.get_schedule(conn))
    assert schedule.for_day(conn, TODAY) == {"minutes": 30, "subject": ""}


def test_schedule_is_per_weekday_and_bad_values_change_nothing(conn):
    form = week(Tuesday=("60", "history"), Saturday=("60", "mix"))
    assert schedule.save_schedule(conn, form) == []
    assert schedule.for_day(conn, TODAY) == {"minutes": 60, "subject": "history"}             # a Tuesday
    assert schedule.for_day(conn, date(2026, 10, 24)) == {"minutes": 60, "subject": "mix"}      # a Saturday
    assert schedule.for_day(conn, date(2026, 10, 21)) == {"minutes": 30, "subject": ""}         # Wednesday: untouched
    problems = schedule.save_schedule(conn, week(Monday=("45", "art")))
    assert len(problems) == 2 and "Monday" in problems[0]
    assert schedule.for_day(conn, TODAY) == {"minutes": 60, "subject": "history"}               # nothing was saved


def test_home_screen_preselects_todays_subject_and_length(client, bank):
    login(client)
    assert client.post("/parent/schedule", data=week("60", "mix")).status_code == 200      # every day: Mix, 60
    home = client.get("/").text
    assert 'name="subject" value="mix" checked' in home
    assert 'name="minutes" value="60" checked' in home and 'name="minutes" value="30" checked' not in home
    # she can still change it: starting a 30-minute Science session works whatever the default says
    r = client.post("/session/start", data={"subject": "science", "minutes": "30"}, follow_redirects=False)
    assert r.status_code == 303
    assert session.get(bank, int(r.headers["location"].rsplit("/", 1)[1]))["length_planned"] == 30
    assert 'name="subject" value="science" checked' in client.get("/").text                  # her choice is remembered today


def test_schedule_page_is_behind_the_pin(client, bank):
    assert client.post("/parent/schedule", data=week("60", "mix")).status_code == 403
    assert schedule.get_schedule(bank)[0]["minutes"] == 30


# ---- Focus this week -------------------------------------------------------------------------------------

def two_topics(conn):
    add_topic(conn, "t1", sort_order=1)
    add_topic(conn, "t2", sort_order=2)
    add_questions(conn, [mk("q-a", "t1"), mk("q-b", "t2")])


def test_focus_topic_is_used_by_the_planner_then_expires(conn):
    two_topics(conn)
    assert session_plan.build_block(conn, subject="science", minutes=30, now=NOON)["focus_topic"] == "t1"   # no focus set
    assert schedule.save_focus(conn, "science", "t2", "", TODAY) == []                  # blank end date = 7 days
    assert schedule.get_focus(conn)["science"] == {"topic": "t2", "until": "2026-10-27"}
    assert session_plan.build_block(conn, subject="science", minutes=30, now=NOON)["focus_topic"] == "t2"
    sid = session.create_daily(conn, 30, now=NOON, subject="science")
    assert session.get(conn, sid)["focus_topic"] == "t2"
    # the end date itself still counts; the day after it, the planner is back to its usual choice
    assert session_plan.build_block(conn, subject="science", minutes=30, now=NOON + timedelta(days=7))["focus_topic"] == "t2"
    assert session_plan.build_block(conn, subject="science", minutes=30, now=NOON + timedelta(days=8))["focus_topic"] == "t1"
    assert schedule.active_focus(conn, "science", TODAY + timedelta(days=8)) == ""


def test_focus_is_ignored_when_the_topic_is_no_longer_taught_and_clears_on_request(conn):
    two_topics(conn)
    schedule.save_focus(conn, "science", "t2", "2026-10-30", TODAY)
    set_topic_status(conn, "t2", "not_taught")
    assert schedule.active_focus(conn, "science", TODAY) == ""
    set_topic_status(conn, "t2", "taught")
    assert schedule.active_focus(conn, "science", TODAY) == "t2"
    assert schedule.save_focus(conn, "science", "", "", TODAY) == []                    # blank topic clears it
    assert schedule.get_focus(conn) == {}


def test_bad_focus_is_refused(conn):
    two_topics(conn)
    assert schedule.save_focus(conn, "science", "no-such-topic", "", TODAY)
    assert schedule.save_focus(conn, "geography", "t2", "", TODAY)                      # t2 is a science topic
    assert schedule.save_focus(conn, "science", "t2", "next week", TODAY)
    assert schedule.save_focus(conn, "science", "t2", "2026-10-01", TODAY)              # already over
    assert schedule.save_focus(conn, "science", "t2", "2030-01-01", TODAY)              # more than a year away
    assert schedule.get_focus(conn) == {}


def test_focus_form_through_the_page(client, bank):
    login(client)
    assert client.post("/parent/focus", data={"subject": "science", "topic": "sci-07-particulate-nature", "until": ""}).status_code == 200
    assert schedule.get_focus(bank)["science"]["topic"] == "sci-07-particulate-nature"
    assert "Focus saved" in client.post("/parent/focus", data={"subject": "science", "topic": "", "until": ""}).text
    assert schedule.get_focus(bank) == {}


# ---- Real-world rewards held through the ledger ------------------------------------------------------------

def ledger_total(conn):
    return conn.execute("SELECT COALESCE(SUM(shards), 0) FROM reward_events").fetchone()[0]


def test_request_holds_shards_in_the_ledger_and_decline_releases_them(conn):
    assert rewards.add_real_item(conn, "Game time", 50, "30 min game time") == []
    item_id = rewards.real_items(conn)[0]["id"]
    give(conn, 40)
    assert rewards.request_real(conn, item_id) == "short" and rewards.pending_count(conn) == 0
    give(conn, 20, ref="gift2")                                                        # 60 in all
    assert rewards.request_real(conn, item_id) == "requested"
    assert rewards.available_shards(conn) == 10 and rewards.held_shards(conn) == 50 and rewards.pending_count(conn) == 1
    hold = conn.execute("SELECT * FROM reward_events WHERE kind = 'hold'").fetchone()
    assert hold["shards"] == -50 and hold["ref"].startswith("hold:")
    assert rewards.request_real(conn, item_id) == "short"                              # the held Shards can't be spent twice
    request_id = rewards.pending_requests(conn)[0]["id"]
    assert rewards.decide_request(conn, request_id, approve=False) == "declined"
    assert rewards.available_shards(conn) == 60 == ledger_total(conn) and rewards.held_shards(conn) == 0
    assert conn.execute("SELECT shards FROM reward_events WHERE kind = 'release'").fetchone()[0] == 50
    assert rewards.decide_request(conn, request_id, approve=False) == "gone"           # answering twice changes nothing
    assert rewards.decide_request(conn, request_id, approve=True) == "gone"
    assert ledger_total(conn) == 60


def test_approve_keeps_the_shards_spent_with_no_extra_rows(conn):
    rewards.add_real_item(conn, "Game time", 50)
    item_id = rewards.real_items(conn)[0]["id"]
    give(conn, 60)
    rewards.request_real(conn, item_id)
    request_id = rewards.pending_requests(conn)[0]["id"]
    rows_before = conn.execute("SELECT COUNT(*) FROM reward_events").fetchone()[0]
    assert rewards.decide_request(conn, request_id, approve=True) == "approved"
    assert conn.execute("SELECT COUNT(*) FROM reward_events").fetchone()[0] == rows_before
    assert rewards.available_shards(conn) == 10 == ledger_total(conn) and rewards.held_shards(conn) == 0
    assert conn.execute("SELECT status FROM reward_requests").fetchone()[0] == "approved"


def test_editing_or_retiring_a_reward_never_changes_a_request_already_made(conn):
    rewards.add_real_item(conn, "Game time", 50, "an hour")
    item_id = rewards.real_items(conn)[0]["id"]
    give(conn, 60)
    rewards.request_real(conn, item_id)
    assert rewards.edit_real_item(conn, item_id, "Movie night", 5) == []
    rewards.set_real_retired(conn, item_id, True)
    request = rewards.pending_requests(conn)[0]
    assert (request["name"], request["cost"]) == ("Game time", 50)
    assert rewards.decide_request(conn, request["id"], approve=False) == "declined"
    assert rewards.available_shards(conn) == 60                                         # released at the original cost
    assert rewards.request_real(conn, item_id) == "retired" and rewards.real_shop(conn) == []
    rewards.set_real_retired(conn, item_id, False)
    assert rewards.real_shop(conn)[0]["name"] == "Movie night"
    assert rewards.request_real(conn, 9999) == "unknown" and rewards.request_real(conn, "x") == "unknown"


def test_reward_form_checks(conn):
    for name, cost, note in (("", "5", ""), ("x" * 61, "5", ""), ("ok", "0", ""), ("ok", "-3", ""), ("ok", "many", ""),
                             ("ok", "5", "n" * 81), ("ok", "10001", "")):
        assert rewards.add_real_item(conn, name, cost, note), (name, cost)
    assert rewards.real_items(conn, include_retired=True) == []


def test_holds_do_not_count_as_earnings_in_a_session(bank):
    sid = session.create_daily(bank, 30, subject="science")
    give(bank, 25, ref="earned-in-session", session_id=sid)
    at = datetime.now(UTC).strftime(STAMP)
    bank.execute("INSERT INTO reward_events (ref, kind, shards, day, session_id, at, created_at) "
                 "VALUES ('hold:99', 'hold', -10, ?, ?, ?, ?)", (rewards.day_of(at), sid, at, at))
    bank.commit()
    assert rewards.session_earnings(bank, session.get(bank, sid))["shards"] == 25


def test_reward_pages_flow_and_pin(client, bank):
    # not logged in: the parent actions are refused
    for url in ("/parent/rewards/items", "/parent/rewards/items/1", "/parent/rewards/items/1/retire",
                "/parent/rewards/requests/1/approve", "/parent/rewards/requests/1/decline"):
        assert client.post(url, data={"name": "x", "cost": "5"}).status_code == 403, url
    assert client.get("/parent/rewards", follow_redirects=False).status_code == 303

    login(client)
    r = client.post("/parent/rewards/items", data={"name": "Game time", "cost": "50", "note": "30 min game time"},
                    follow_redirects=False)
    assert r.status_code == 303 and "Game time" in client.get("/parent/rewards").text
    assert client.post("/parent/rewards/items", data={"name": "", "cost": "x"}).status_code == 422
    client.post("/parent/logout")

    # she asks (student pages need no PIN); a parent badge appears
    give(bank, 80)
    shop = client.get("/shop").text
    assert "Game time" in shop and "30 min game time" in shop and "Ask for 50" in shop
    r = client.post("/shop/request", data={"item": "1"}, follow_redirects=False)
    assert r.headers["location"] == "/shop?m=requested"
    shop = client.get("/shop?m=requested").text
    assert "Waiting for a parent" in shop and "50 Star Shards held" in shop and "Ask for 50" not in shop
    assert client.post("/shop/request", data={"item": "1"}, follow_redirects=False).headers["location"] == "/shop?m=short"
    assert 'class="badge"' in client.get("/").text                                     # on the plain parent link

    # a parent answers on the PIN page
    login(client)
    page = client.get("/parent/rewards").text
    assert "Waiting for you (1)" in page and "Approve" in page and 'class="badge"' in client.get("/").text
    r = client.post("/parent/rewards/requests/1/approve", follow_redirects=False)
    assert r.headers["location"] == "/parent/rewards?m=approved"
    assert rewards.available_shards(bank) == 30 and rewards.pending_count(bank) == 0
    assert 'class="badge"' not in client.get("/").text
    client.post("/parent/logout")
    assert "approved" in client.get("/shop").text


# ---- Uploads -------------------------------------------------------------------------------------------

def test_clean_image_accepts_png_jpg_and_webp_and_strips_metadata():
    info = PngInfo()
    info.add_text("Comment", "secret-comment")
    original = png(pnginfo=info)
    assert b"secret-comment" in original
    cleaned, ext = uploads.clean_image(original)
    assert ext == ".png" and b"secret-comment" not in cleaned and Image.open(io.BytesIO(cleaned)).format == "PNG"

    exif = Image.Exif()
    exif[0x010E] = "secret-description"
    for fmt, want in (("JPEG", ".jpg"), ("WEBP", ".webp")):
        buf = io.BytesIO()
        Image.new("RGB", (40, 30), (10, 120, 200)).save(buf, fmt, exif=exif.tobytes())
        assert b"secret-description" in buf.getvalue()
        cleaned, ext = uploads.clean_image(buf.getvalue())
        assert ext == want and b"secret-description" not in cleaned and Image.open(io.BytesIO(cleaned)).format == fmt


def test_clean_image_shrinks_a_huge_picture():
    cleaned, _ = uploads.clean_image(png(size=(3000, 1000)))
    assert max(Image.open(io.BytesIO(cleaned)).size) == uploads.MAX_EDGE


def test_uploads_are_checked_by_content_size_and_readability():
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, "GIF")
    good = png()
    wide = io.BytesIO()
    Image.new("L", (7000, 7000)).save(wide, "PNG")                                       # small file, 49 million pixels
    for bad in (b"", b"just some text, called .png by its name", gif.getvalue(), good[: len(good) // 2],
                b"x" * (uploads.MAX_BYTES + 1), wide.getvalue()):
        with pytest.raises(uploads.UploadError):
            uploads.clean_image(bad)
    assert len(wide.getvalue()) < uploads.MAX_BYTES                                       # refused for its pixels, not its size
    with pytest.raises(uploads.UploadError, match="5 MB"):
        uploads.clean_image(b"x" * (uploads.MAX_BYTES + 1))
    with pytest.raises(uploads.UploadError, match="PNG, JPG or WebP"):
        uploads.clean_image(gif.getvalue())


def upload(client, kind, data, name="pic.png", label=""):
    return client.post("/parent/images/upload", data={"kind": kind, "label": label},
                       files={"file": (name, data, "image/png")}, follow_redirects=False)


def test_upload_serve_equip_and_delete_a_scene(client, bank):
    assert upload(client, "scene", png()).status_code == 403                           # PIN needed
    login(client)
    original = png(colour=(20, 60, 160))
    assert upload(client, "scene", original, label="Lake").status_code == 303
    image = uploads.list_images(bank, "scene")[0]
    stored = uploads.upload_dir() / image["filename"]
    assert stored.is_file() and stored.read_bytes() != original                          # re-encoded, not copied
    served = client.get(image["url"])
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"
    assert served.headers["x-content-type-options"] == "nosniff"

    assert 'data-scene=""' in client.get("/").text                                       # original look is the default
    assert client.post(f"/parent/images/{image['id']}/use").status_code == 200
    home = client.get("/").text
    assert 'data-scene="custom"' in home and image["url"] in home
    shop = client.get("/shop").text
    assert "Lake" in shop and "Using now" in shop
    client.post("/shop/unequip", data={"slot": "scene"})
    assert 'data-scene=""' in client.get("/").text
    client.post("/shop/use-scene", data={"image": str(image["id"])})                     # she can pick it herself
    assert 'data-scene="custom"' in client.get("/").text

    client.post(f"/parent/images/{image['id']}/delete")
    assert not stored.exists() and client.get(image["url"]).status_code == 404
    assert 'data-scene=""' in client.get("/").text and uploads.list_images(bank, "scene") == []


def test_bad_uploads_are_refused_and_leave_nothing_behind(client, bank):
    login(client)
    r = upload(client, "scene", b"<html>not a picture</html>", name="holiday.png")        # right extension, wrong content
    assert r.status_code == 422 and "not a picture this app can read" in r.text
    r = client.post("/parent/images/upload", data={"kind": "scene"}, files={"file": ("empty.png", b"", "image/png")})
    assert r.status_code == 422
    assert upload(client, "banner", png()).status_code == 422                            # unknown kind
    assert uploads.list_images(bank, "scene") == [] and uploads.list_images(bank, "companion") == []
    folder = uploads.upload_dir()
    assert not folder.exists() or list(folder.iterdir()) == []


def test_at_most_five_scenes(client, bank):
    login(client)
    for n in range(uploads.MAX_SCENES):
        assert upload(client, "scene", png(colour=(n * 20, 50, 50))).status_code == 303
    r = upload(client, "scene", png())
    assert r.status_code == 422 and "Delete one first" in r.text
    assert len(uploads.list_images(bank, "scene")) == 5


def test_companion_upload_replaces_the_old_one_and_delete_restores_the_original(client, bank):
    login(client)
    assert rewards.cosmetics(bank)["companion"] == "/static/companion.svg"
    upload(client, "companion", png(colour=(1, 2, 3)))
    first = uploads.list_images(bank, "companion")[0]
    assert rewards.cosmetics(bank)["companion"] == first["url"] == rewards.companion_image(bank)
    upload(client, "companion", png(colour=(9, 9, 9)))
    second = uploads.list_images(bank, "companion")
    assert len(second) == 1 and second[0]["id"] != first["id"]
    assert not (uploads.upload_dir() / first["filename"]).exists()                       # the old file is gone too
    assert rewards.companion_image(bank) == second[0]["url"]
    client.post(f"/parent/images/{second[0]['id']}/delete")
    assert rewards.cosmetics(bank)["companion"] == "/static/companion.svg"
    assert "/static/companion.svg" in client.get("/parent/images").text


def test_only_generated_names_are_served(client):
    for name in ("..%2F..%2Fetc%2Fpasswd", "x.png", "A" * 24 + ".png", "0" * 24 + ".gif", "0" * 24 + ".png"):
        assert client.get(f"/uploads/{name}").status_code == 404, name


# ---- K-pop celebration -------------------------------------------------------------------------------------

def ranked_up_session(client, bank) -> int:
    sid = session.create_daily(bank, 30, subject="science")
    client.post(f"/session/{sid}/done")
    client.post(f"/session/{sid}/start-step")
    client.post(f"/q/{POINTS_Q}/answer", data={"answer": FULL, "shown_at": "", "scope": f"@s:{sid}"})
    give(bank, xp=400, ref="big-effort", session_id=sid)                                  # enough XP to pass a rank
    return sid


def test_kpop_toggle_hides_or_shows_the_stage_screen(client, bank):
    assert schedule.kpop_enabled(bank) is False                                           # off by default
    sid = ranked_up_session(client, bank)
    assert rewards.session_earnings(bank, session.get(bank, sid))["ranked_up"]
    wrap = client.get(f"/session/{sid}").text
    assert "Rank up!" in wrap and 'class="stage"' not in wrap and "confetti" not in wrap

    login(client)
    assert "Display settings saved" in client.post("/parent/display", data={"kpop_celebrations": "on"}).text
    on = client.get(f"/session/{sid}").text
    assert 'class="stage"' in on and 'class="confetti"' in on and "Rank up celebration" in on
    assert "<img" in on[on.index('class="stage"'):on.index("Earned this session")]      # only her companion's picture

    client.post("/parent/display", data={})                                                # unticked = off
    assert 'class="stage"' not in client.get(f"/session/{sid}").text


def test_no_stage_screen_without_a_rank_up(client, bank):
    login(client)
    client.post("/parent/display", data={"kpop_celebrations": "on"})
    sid = session.create_daily(bank, 30, subject="science")
    client.post(f"/session/{sid}/done")
    client.post(f"/session/{sid}/start-step")
    client.post(f"/q/{POINTS_Q}/answer", data={"answer": FULL, "shown_at": "", "scope": f"@s:{sid}"})
    assert 'class="stage"' not in client.get(f"/session/{sid}").text


def test_stage_motion_only_inside_the_reduced_motion_guard():
    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "theme.css").read_text(encoding="utf-8")
    outside, inside = css.split("@media (prefers-reduced-motion: no-preference)")
    assert ".confetti { display: none;" in outside and ".confetti { display: block; }" in inside
    assert "animation:" not in outside and "@keyframes" not in outside


# ---- Settings page sections ----------------------------------------------------------------------------------

def test_settings_page_is_split_into_sections_and_keeps_existing_values(client, bank):
    assert client.get("/parent/settings", follow_redirects=False).status_code == 303
    login(client)
    page = client.get("/parent/settings").text
    ids = [f'id="{i}"' for i in ("sessions", "help", "rewards", "images", "display")]
    assert [page.index(i) for i in ids] == sorted(page.index(i) for i in ids)              # in this order
    for heading in ("Sessions and schedule", "Help and Ask", "Rewards", "Images", "Display", "Her week", "Focus this week"):
        assert heading in page
    assert "Monday" in page and "Sunday" in page and 'href="/parent/rewards"' in page and 'href="/parent/images"' in page

    # existing settings keep their values, whichever other section is saved afterwards
    form = {"cards_per_set": "6", "recall_method": "paper", "recall_timing": "after_each", "warmup_max": "12",
            "interval_1": "1", "interval_2": "3", "interval_3": "7", "interval_4": "14", "interval_5": "30",
            "known_threshold": "60", "help_enabled": "on", "ask_enabled": "on", "ask_daily_cap": "25"}
    assert client.post("/parent/settings", data=form).status_code == 200
    saved = app_settings.get_all(bank)
    client.post("/parent/schedule", data=week("60", "mix"))
    client.post("/parent/display", data={"kpop_celebrations": "on"})
    client.post("/parent/focus", data={"subject": "science", "topic": "sci-07-particulate-nature", "until": ""})
    assert app_settings.get_all(bank) == saved and saved["cards_per_set"] == 6 and saved["warmup_max"] == 12
    assert set(saved) == set(app_settings.DEFAULTS)                                       # the new keys live apart
    page = client.get("/parent/settings").text
    assert 'name="cards_per_set" inputmode="numeric" value="6"' in page
    assert '<option value="60" selected>60 minutes</option>' in page and "K-pop stage celebrations" in page
    assert "checked" in page[page.index("kpop_celebrations"):]


def test_rewards_and_images_pages_need_the_pin(client):
    for url in ("/parent/rewards", "/parent/images"):
        r = client.get(url, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/parent/login")
    login(client)
    assert "Add a reward" in client.get("/parent/rewards").text
    assert "Background scenes (0 of 5)" in client.get("/parent/images").text
