"""SQLite connection and schema setup."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings

SCHEMA_VERSION = 1
SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path=None) -> sqlite3.Connection:
    """Open the database (DB_PATH by default). Rows behave like dicts."""
    path = Path(db_path or get_settings().db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path=None) -> None:
    """Create the tables if the database is new. Safe to run on every start."""
    conn = connect(db_path)
    try:
        # WAL lets the web app and a command-line run use the database at the same time.
        conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        # Later schema changes go here as "if version < 2: ...".
        conn.commit()
    finally:
        conn.close()
