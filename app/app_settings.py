"""Settings a parent can change on the settings page, stored in the database.

Read on every request, so a change takes effect at once with no restart or code change.
A key that has never been saved uses its default.
"""

import json

from app.db import utc_now
from app.topics import LEVELS

DEFAULTS = {
    "cards_per_set": 4,
    "recall_method": "type",          # type / paper: which way of answering is offered first
    "recall_timing": "after_set",     # after_set: study the whole set, then recall it; after_each: card by card
    "underline_step": False,
    "box_intervals": [1, 2, 4, 8, 16],  # days before a card in box 1..5 is due again
    "known_threshold": 80,            # % of points covered for a multi-point card to count as known
    "exam_mode": False,
    "generated_approved_confidence": "medium",  # confidence base of a generated question once a parent approved it
    "current_level": "sec1",          # sec1..sec4: topics above this level count as not yet taught (unless set to taught)
    "warmup_max": 8,                  # session warm-up: most due cards (1 to 20)
    "learn_count": 4,                 # session Learn step: most cards (2 to 8)
    "help_enabled": True,             # "Help me" button on questions
    "help_tier2": True,               # the nudge (tier 2) as a step between "explain the question" and the card
}
CONFIDENCE_LEVELS = ("low", "medium", "high")
RECALL_METHODS = ("type", "paper")
RECALL_TIMINGS = ("after_set", "after_each")


def get_all(conn) -> dict:
    settings = dict(DEFAULTS)
    for row in conn.execute("SELECT key, value FROM app_settings"):
        if row["key"] in DEFAULTS:
            settings[row["key"]] = json.loads(row["value"])
    return settings


def validate(values: dict) -> list[str]:
    """Problems with a full set of settings (empty if they're fine)."""
    problems = []
    n = values.get("cards_per_set")
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 20:
        problems.append("Cards per set must be a whole number from 1 to 20.")
    if values.get("recall_method") not in RECALL_METHODS:
        problems.append("Default recall method must be type or paper.")
    if values.get("recall_timing") not in RECALL_TIMINGS:
        problems.append("Recall timing must be after the set or after each card.")
    intervals = values.get("box_intervals")
    if (not isinstance(intervals, list) or len(intervals) != 5
            or not all(isinstance(d, int) and not isinstance(d, bool) and 1 <= d <= 365 for d in intervals)):
        problems.append("Box intervals must be five whole numbers of days, each from 1 to 365.")
    threshold = values.get("known_threshold")
    if not isinstance(threshold, int) or isinstance(threshold, bool) or not 50 <= threshold <= 100:
        problems.append("The 'known' threshold must be a whole number from 50 to 100.")
    if values.get("generated_approved_confidence") not in CONFIDENCE_LEVELS:
        problems.append("Confidence of approved generated questions must be low, medium or high.")
    if values.get("current_level") not in LEVELS:
        problems.append("Her current level must be Sec 1, 2, 3 or 4.")
    for key, low, high, label in (("warmup_max", 1, 20, "Warm-up cards"), ("learn_count", 2, 8, "Learn cards")):
        n = values.get(key)
        if not isinstance(n, int) or isinstance(n, bool) or not low <= n <= high:
            problems.append(f"{label} must be a whole number from {low} to {high}.")
    for key in ("underline_step", "exam_mode", "help_enabled", "help_tier2"):
        if not isinstance(values.get(key), bool):
            problems.append(f"{key} must be on or off.")
    return problems


def save(conn, values: dict) -> list[str]:
    """Save a full set of settings if valid. Returns the problems (nothing is saved if there are any)."""
    merged = {**DEFAULTS, **{k: v for k, v in values.items() if k in DEFAULTS}}
    problems = validate(merged)
    if problems:
        return problems
    now = utc_now()
    for key, value in merged.items():
        conn.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                     (key, json.dumps(value), now))
    conn.commit()
    return []


def from_form(form: dict) -> dict:
    """Settings as posted by the settings page (text fields and checkboxes)."""
    def number(name):
        text = str(form.get(name, "")).strip()
        return int(text) if text.lstrip("-").isdigit() else text

    return {
        "cards_per_set": number("cards_per_set"),
        "recall_method": form.get("recall_method", ""),
        "recall_timing": form.get("recall_timing", ""),
        "underline_step": form.get("underline_step") == "on",
        "box_intervals": [number(f"interval_{i}") for i in range(1, 6)],
        "known_threshold": number("known_threshold"),
        "exam_mode": form.get("exam_mode") == "on",
        "help_enabled": form.get("help_enabled") == "on",
        "help_tier2": form.get("help_tier2") == "on",
        # a form without this field (an older page) keeps the default
        "generated_approved_confidence": form.get("generated_approved_confidence")
        or DEFAULTS["generated_approved_confidence"],
        "current_level": form.get("current_level") or DEFAULTS["current_level"],
        # a form without these fields (an older page) keeps the defaults
        "warmup_max": number("warmup_max") if str(form.get("warmup_max", "")).strip() else DEFAULTS["warmup_max"],
        "learn_count": number("learn_count") if str(form.get("learn_count", "")).strip() else DEFAULTS["learn_count"],
    }
