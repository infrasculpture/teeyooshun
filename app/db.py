"""SQLite connection and schema setup."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings

SCHEMA_VERSION = 20
SCHEMA_FILE = Path(__file__).parent / "schema.sql"
SCHEMA_V2_FILE = Path(__file__).parent / "schema_v2.sql"
SCHEMA_V3_FILE = Path(__file__).parent / "schema_v3.sql"
SCHEMA_V4_FILE = Path(__file__).parent / "schema_v4.sql"
SCHEMA_V5_FILE = Path(__file__).parent / "schema_v5.sql"
SCHEMA_V6_FILE = Path(__file__).parent / "schema_v6.sql"
SCHEMA_V7_FILE = Path(__file__).parent / "schema_v7.sql"
SCHEMA_V8_FILE = Path(__file__).parent / "schema_v8.sql"
SCHEMA_V9_FILE = Path(__file__).parent / "schema_v9.sql"
SCHEMA_V10_FILE = Path(__file__).parent / "schema_v10.sql"
SCHEMA_V11_FILE = Path(__file__).parent / "schema_v11.sql"
SCHEMA_V12_FILE = Path(__file__).parent / "schema_v12.sql"
SCHEMA_V13_FILE = Path(__file__).parent / "schema_v13.sql"
SCHEMA_V14_FILE = Path(__file__).parent / "schema_v14.sql"
SCHEMA_V15_FILE = Path(__file__).parent / "schema_v15.sql"
SCHEMA_V16_FILE = Path(__file__).parent / "schema_v16.sql"
SCHEMA_V17_FILE = Path(__file__).parent / "schema_v17.sql"
SCHEMA_V18_FILE = Path(__file__).parent / "schema_v18.sql"
SCHEMA_V19_FILE = Path(__file__).parent / "schema_v19.sql"
SCHEMA_V20_FILE = Path(__file__).parent / "schema_v20.sql"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path=None) -> sqlite3.Connection:
    """Open the database (DB_PATH by default). Rows behave like dicts."""
    path = Path(db_path or get_settings().db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # FastAPI may run a request's steps on different worker threads; each request
    # still uses its own connection one step at a time, so this is safe.
    conn = sqlite3.connect(path, check_same_thread=False)
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
            conn.execute("PRAGMA user_version = 1")
            conn.commit()
            version = 1
        if version < 2:
            # The script sets user_version = 2 itself, inside its transaction.
            conn.executescript(SCHEMA_V2_FILE.read_text(encoding="utf-8"))
        if version < 3:
            # Rebuilds the questions table; the script sets user_version = 3 itself.
            conn.executescript(SCHEMA_V3_FILE.read_text(encoding="utf-8"))
            conn.execute("PRAGMA foreign_keys = ON")
        if version < 4:
            conn.executescript(SCHEMA_V4_FILE.read_text(encoding="utf-8"))
        if version < 5:
            conn.executescript(SCHEMA_V5_FILE.read_text(encoding="utf-8"))
        if version < 6:
            conn.executescript(SCHEMA_V6_FILE.read_text(encoding="utf-8"))
        if version < 7:
            conn.executescript(SCHEMA_V7_FILE.read_text(encoding="utf-8"))
        if version < 8:
            conn.executescript(SCHEMA_V8_FILE.read_text(encoding="utf-8"))
        if version < 9:
            conn.executescript(SCHEMA_V9_FILE.read_text(encoding="utf-8"))
        if version < 10:
            conn.executescript(SCHEMA_V10_FILE.read_text(encoding="utf-8"))
        if version < 11:
            conn.executescript(SCHEMA_V11_FILE.read_text(encoding="utf-8"))
        if version < 12:
            conn.executescript(SCHEMA_V12_FILE.read_text(encoding="utf-8"))
        if version < 13:
            conn.executescript(SCHEMA_V13_FILE.read_text(encoding="utf-8"))
        if version < 14:
            conn.executescript(SCHEMA_V14_FILE.read_text(encoding="utf-8"))
        if version < 15:
            conn.executescript(SCHEMA_V15_FILE.read_text(encoding="utf-8"))
        if version < 16:
            conn.executescript(SCHEMA_V16_FILE.read_text(encoding="utf-8"))
        if version < 17:
            conn.executescript(SCHEMA_V17_FILE.read_text(encoding="utf-8"))
        if version < 18:
            conn.executescript(SCHEMA_V18_FILE.read_text(encoding="utf-8"))
        if version < 19:
            conn.executescript(SCHEMA_V19_FILE.read_text(encoding="utf-8"))
        if version < 20:
            conn.executescript(SCHEMA_V20_FILE.read_text(encoding="utf-8"))
        # Later schema changes go here as "if version < 21: ...".
        conn.commit()
    finally:
        conn.close()
