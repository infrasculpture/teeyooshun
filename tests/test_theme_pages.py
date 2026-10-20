"""Theme and reward pages, end to end (STUDENT_UI_SPEC.md sections 1, 2 and 8). Template smoke tests at the two
screen sizes the spec names. A test client has no screen, so "at 1280x800 / 3840x2160" means: every page renders
with the one stylesheet, and the stylesheet's width rules that apply at that width give the intended layout.
Looking at the real screens stays a manual check. Fake marker, no API calls."""

import re
from pathlib import Path

import pytest

from app import rewards, session
from app import rewards_config as cfg

APP_DIR = Path(__file__).resolve().parent.parent / "app"
CSS = (APP_DIR / "static" / "theme.css").read_text(encoding="utf-8")
POINTS_Q = "sci-test-points-q1"          # in the fixture bank: science, 2 marks
PART = "The balloon expands."
FULL = "The balloon expands as particles move further apart."
SIZES = [(1280, 800), (3840, 2160)]


# ---- The stylesheet --------------------------------------------------------------------------------------

def media_blocks(css: str) -> list[tuple[int, str]]:
    """(min-width, body) for every @media (min-width: Npx) block."""
    blocks = []
    for m in re.finditer(r"@media \(min-width: (\d+)px\)\s*\{", css):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        blocks.append((int(m.group(1)), css[m.end():i - 1]))
    return blocks


def rules_at(width: int) -> str:
    return "\n".join(body for w, body in media_blocks(CSS) if width >= w)


@pytest.mark.parametrize("width,height", SIZES)
def test_layout_rules_at_each_screen_size(width, height):
    active = rules_at(width)
    assert ".qa-grid { grid-template-columns: 1fr 1fr" in active        # question left, answer right
    assert ".home-grid { grid-template-columns: 3fr 2fr" in active      # Start card beside commissions and map
    big_type = "html { font-size: var(--text-size-4k)" in active
    assert big_type == (width >= 2560)                                  # bigger type on the 4K screen only
    assert "--text-size: 20px" in CSS and "--work-width: 80em" in CSS   # body >= 20px; ~1600px working area


def variables(css: str) -> dict:
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", css.split("}")[0]))


def contrast(a: str, b: str) -> float:
    def lum(hex_colour):
        channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_all_colours_are_variables_and_text_stays_readable():
    v = variables(CSS)
    assert contrast(v["--ink"], v["--parchment"]) >= 7          # body text: AAA
    assert contrast(v["--muted"], v["--parchment"]) >= 4.5      # small text: AA
    for subject in ("science", "geography", "history"):         # white text on each element's buttons
        assert contrast(v[f"--{subject}-deep"], "#ffffff") >= 4.5, subject
        assert f'body[data-subject="{subject}"]' in CSS
    # every animation (the rank-up glow and the K-pop stage lights and confetti) sits inside the one reduced-motion guard
    guarded = CSS.split("@media (prefers-reduced-motion: no-preference)")
    assert len(guarded) == 2 and CSS.count("animation:") == guarded[1].count("animation:") >= 1
    assert CSS.count("animation-delay:") == guarded[1].count("animation-delay:")


def test_no_inline_styles_block_and_no_third_party_game_names():
    assert "<style" not in (APP_DIR / "templates" / "base.html").read_text(encoding="utf-8")
    banned = re.compile(r"genshin|paimon|teyvat|mondstadt|liyue|inazuma|sumeru|fontaine|primogem|hoyoverse|mihoyo", re.I)
    for path in APP_DIR.rglob("*"):
        if path.suffix in (".py", ".html", ".css", ".svg", ".sql"):
            assert not banned.search(path.read_text(encoding="utf-8")), path


def test_static_files_are_served(client):
    r = client.get("/static/theme.css")
    assert r.status_code == 200 and "text/css" in r.headers["content-type"] and "--parchment" in r.text
    assert client.get("/static/companion.svg").status_code == 200


# ---- Pages -----------------------------------------------------------------------------------------------

def assert_themed(page: str, subject: str = ""):
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page
    assert '/static/theme.css?v=' in page and "fonts.googleapis.com" in page
    assert f'data-subject="{subject}"' in page


def finished_session(client, bank) -> int:
    sid = session.create_daily(bank, 30, subject="science")
    client.post(f"/session/{sid}/done")                                   # "Let's go" on the focus step
    client.post(f"/session/{sid}/start-step")
    client.post(f"/q/{POINTS_Q}/answer", data={"answer": FULL, "shown_at": "", "scope": f"@s:{sid}"})
    return sid


@pytest.mark.parametrize("width,height", SIZES)
def test_home_adds_rank_commissions_and_map_without_crowding_start(client, width, height):
    page = client.get("/").text
    assert_themed(page, "science")
    assert cfg.RANK_LABEL in page and cfg.COMMISSIONS_LABEL in page and 'href="/map"' in page
    start, side = page.index("Start today's session"), page.index(cfg.COMMISSIONS_LABEL)
    assert start < side                                                  # stacked on narrow screens: Start first
    assert page.count('class="tick') == cfg.COMMISSIONS_PER_DAY


@pytest.mark.parametrize("width,height", SIZES)
def test_question_and_feedback_pages_take_the_subject_colour(client, width, height):
    assert_themed(client.get(f"/q/{POINTS_Q}").text, "science")
    r = client.post(f"/q/{POINTS_Q}/answer", data={"answer": PART, "shown_at": ""}, follow_redirects=False)
    feedback = client.get(r.headers["location"]).text
    assert_themed(feedback, "science")
    assert "The one thing to fix" in feedback and cfg.COMPANION_DEFAULT_IMAGE in feedback   # said by the companion


@pytest.mark.parametrize("width,height", SIZES)
def test_wrap_up_shows_xp_and_shards_earned(client, bank, width, height):
    sid = finished_session(client, bank)
    wrap = client.get(f"/session/{sid}").text
    assert_themed(wrap, "science")
    assert "Earned this session" in wrap and "Press Finish to collect" in wrap and cfg.COMPANION_DEFAULT_IMAGE in wrap
    client.post(f"/session/{sid}/done")
    done = client.get(f"/session/{sid}").text
    s = session.get(bank, sid)
    earned = rewards.session_earnings(bank, s)
    assert earned["xp"] >= cfg.XP["step_done"] + cfg.XP["session_done"]
    assert earned["shards"] >= cfg.SHARDS_PER_SESSION
    assert f"+{earned['xp']} XP" in done and f"+{earned['shards']} {cfg.SHARDS_LABEL}" in done


@pytest.mark.parametrize("width,height", SIZES)
def test_shop_and_map_pages_render(client, bank, width, height):
    shop = client.get("/shop").text
    assert_themed(shop)
    assert "Reward Shop" in shop and all(i["name"] in shop for i in cfg.SHOP)
    full = client.get("/map").text
    assert_themed(full)
    assert "Particulate Nature of Matter" in full and "Not started" in full


def test_buying_and_wearing_through_the_pages(client, bank):
    bank.execute("INSERT INTO reward_events (ref, kind, xp, shards, day, at, created_at) "
                 "VALUES ('gift', 'test', 0, 50, '2026-01-01', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')")
    bank.commit()
    r = client.post("/shop/buy", data={"item": "frame-oak"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/shop?m=bought"
    assert "Bought!" in client.get(r.headers["location"]).text
    assert client.post("/shop/buy", data={"item": "frame-oak"}, follow_redirects=False).headers["location"] == "/shop?m=owned"
    client.post("/shop/equip", data={"item": "frame-oak"})
    assert 'data-frame="frame-oak"' in client.get("/").text
    client.post("/shop/unequip", data={"slot": "frame"})
    assert 'data-frame=""' in client.get("/").text
