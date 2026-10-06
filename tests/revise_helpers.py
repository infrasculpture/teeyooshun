"""Made-up revision cards for Revise-mode tests. No real source material."""

from app.revision_cards import import_cards


def make_card(card_id, prompt, answer, *, topic="geo-intro", subject="geography", command="define",
              section="Made-up section", recall=None, **extra):
    card = {"id": card_id, "subject": subject, "level": "sec1", "source": "revision_guide", "topic": topic,
            "section": section, "source_ref": "Made-up guide, p.1", "command_word": command, "prompt": prompt,
            "answer": answer, "recall": recall or ("self_tick_only" if answer["format"] == "image" else "typed_or_paper")}
    card.update(extra)
    return card


def point(text, key_terms=(), sub=()):
    p = {"text": text, "key_terms": list(key_terms)}
    if sub:
        p["sub"] = [{"text": t, "key_terms": []} for t in sub]
    return p


CLIMATE = make_card("card-climate", "Define climate.", {"format": "bullets", "points": [
    point("Climate is the average weather of a place over a long period.", ["average weather"])]},
    examples=["Singapore is hot and wet all year."])

RAIN = make_card("card-rain", "Explain how rain forms.", {"format": "list", "points": [
    point("Water evaporates from the sea.", ["evaporates"]),
    point("Water vapour cools and condenses into clouds.", ["condenses"], sub=["This happens as the air rises."]),
    point("Droplets join and fall as rain.", ["droplets"])]}, command="explain")

FLOODS = make_card("card-floods", "Describe two effects of floods.", {"format": "grouped", "groups": [
    {"heading": "Damage", "points": [point("Homes are ruined."), point("Roads are washed away.")]},
    {"heading": "Health", "points": [point("Dirty water spreads disease.")]}]}, command="describe")

COMPARE = make_card("card-compare", "Compare weather and climate.", {"format": "table", "key_terms": ["long term"],
    "table": {"headers": ["", "Weather", "Climate"], "rows": [["Time", "short term", "long term"],
                                                              ["Described by", "today", "averages"]]}}, command="compare")

EVENTS = make_card("card-events", "Outline the made-up town's growth.", {"format": "timeline", "events": [
    {"date": "1300", "text": "A settlement begins.", "key_terms": ["settlement"]},
    {"date": "1350", "text": "A wall is built.", "key_terms": ["wall"]}]},
    topic="his-u1-temasek-to-singapore", subject="history", command="outline")

MAP = make_card("card-map", "Locate the three rivers.", {"format": "image", "alt": "A made-up map of three rivers.",
                                                        "image": {}}, command="locate")

ALL_CARDS = [CLIMATE, RAIN, FLOODS, COMPARE, EVENTS, MAP]


def add_cards(conn, cards) -> None:
    """Store cards as approved, in the order given (that order is their book order)."""
    import_cards(conn, cards, {c["id"]: f"rev_u01.pdf:0001:{i:08d}" for i, c in enumerate(cards)})


def settings(**changes) -> dict:
    from app.app_settings import DEFAULTS
    return {**DEFAULTS, **changes}
