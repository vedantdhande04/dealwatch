"""SQLite storage for dealwatch price checks."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "dealwatch.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    price REAL NOT NULL,
    checked_at TEXT NOT NULL
)
"""


def connect(db_path=DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def record_check(
    product: str,
    url: str,
    title: str,
    price: float,
    db_path: Path | str = DEFAULT_DB,
    checked_at: str | None = None,
) -> int:
    """Insert one check row and return its id."""
    if checked_at is None:
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO checks (product, url, title, price, checked_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (product, url, title, price, checked_at),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()
