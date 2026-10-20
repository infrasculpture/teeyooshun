"""Settings, read from the environment (.env on the server).

Read at call time rather than import time so tests can change them.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str
    data_dir: str  # the data directory inside the container (renders, sources)
    marking_model: str  # multi-mark and LORMS marking
    light_model: str  # 1-mark marking now; hints and follow-ups later
    marking_effort: str  # low / medium / high / xhigh / max
    light_effort: str  # blank = don't send effort (Haiku 4.5 rejects it)
    ingest_model: str  # reads scanned pages; defaults to the marking model
    ingest_effort: str  # blank in .env = the marking effort; "none" = don't send effort
    ingest_max_edge: int  # long edge of page renders sent to the model, in pixels
    display_max_edge: int  # long edge of the sharper page images shown on screen, in pixels
    refusal_fallback: bool  # retry on Anthropic's fallback model if the main model declines
    api_key_set: bool
    parent_pin: str  # parent actions are switched off while blank or "0000"
    session_secret: str  # signs the parent cookie; parent actions are off while blank
    usd_sgd_rate: float  # fixed conversion for the dashboard's cost section
    subscription_sgd: float  # monthly subscription counted against the budget
    monthly_budget_sgd: float  # total monthly budget including the subscription


def _number(name: str, default: float) -> float:
    """A positive-or-zero number from the environment; blank or unreadable uses the default."""
    try:
        value = float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default
    return value if value >= 0 else default


def get_settings() -> Settings:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    marking_model = os.environ.get("MARKING_MODEL", "claude-opus-5-5").strip()
    marking_effort = os.environ.get("MARKING_EFFORT", "medium").strip()
    ingest_effort = os.environ.get("INGEST_EFFORT", "").strip() or marking_effort
    if ingest_effort.lower() == "none":
        ingest_effort = ""  # e.g. for a Haiku ingest model, which rejects effort
    return Settings(
        db_path=os.environ.get("DB_PATH", "/app/data/teeyooshun.db"),
        data_dir=os.environ.get("APP_DATA_DIR", "/app/data"),
        marking_model=marking_model,
        light_model=os.environ.get("LIGHT_MODEL", "claude-sonnet-5-5").strip(),
        marking_effort=marking_effort,
        light_effort=os.environ.get("LIGHT_EFFORT", "").strip(),
        ingest_model=os.environ.get("INGEST_MODEL", "").strip() or marking_model,
        ingest_effort=ingest_effort,
        ingest_max_edge=int(os.environ.get("INGEST_MAX_EDGE", "1568") or 1568),
        display_max_edge=int(os.environ.get("DISPLAY_MAX_EDGE", "2400") or 2400),
        refusal_fallback=os.environ.get("REFUSAL_FALLBACK", "true").strip().lower() in ("1", "true", "yes"),
        api_key_set=bool(key) and key != "replace-me",
        parent_pin=os.environ.get("PARENT_PIN", "").strip(),
        session_secret=os.environ.get("SESSION_SECRET", "").strip(),
        usd_sgd_rate=_number("USD_SGD_RATE", 1.35),
        subscription_sgd=_number("SUBSCRIPTION_SGD", 0.0),
        monthly_budget_sgd=_number("MONTHLY_BUDGET_SGD", 100.0),
    )
