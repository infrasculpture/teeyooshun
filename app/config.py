"""Settings, read from the environment (.env on the server).

Read at call time rather than import time so tests can change them.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str
    marking_model: str  # multi-mark and LORMS marking
    light_model: str  # 1-mark marking now; hints and follow-ups later
    marking_effort: str  # low / medium / high / xhigh / max
    light_effort: str  # blank = don't send effort (Haiku 4.5 rejects it)
    refusal_fallback: bool  # retry on Anthropic's fallback model if the main model declines
    api_key_set: bool
    parent_pin: str  # parent actions are switched off while blank or "0000"
    session_secret: str  # signs the parent cookie; parent actions are off while blank


def get_settings() -> Settings:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return Settings(
        db_path=os.environ.get("DB_PATH", "/app/data/teeyooshun.db"),
        marking_model=os.environ.get("MARKING_MODEL", "claude-opus-5-5"),
        light_model=os.environ.get("LIGHT_MODEL", "claude-sonnet-5-5"),
        marking_effort=os.environ.get("MARKING_EFFORT", "medium").strip(),
        light_effort=os.environ.get("LIGHT_EFFORT", "").strip(),
        refusal_fallback=os.environ.get("REFUSAL_FALLBACK", "true").strip().lower() in ("1", "true", "yes"),
        api_key_set=bool(key) and key != "replace-me",
        parent_pin=os.environ.get("PARENT_PIN", "").strip(),
        session_secret=os.environ.get("SESSION_SECRET", "").strip(),
    )
