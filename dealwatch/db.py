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


def last_price(
    url: str, db_path: Path | str = DEFAULT_DB
) -> float | None:
    """Return the most recent stored price for a url, or None if never seen."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT price FROM checks WHERE url = ? ORDER BY id DESC LIMIT 1",
            (url,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


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


def history(
    term: str,
    db_path: Path | str = DEFAULT_DB,
    limit: int = 30,
) -> list[sqlite3.Row]:
    """Return the most recent checks for a product name or url fragment."""
    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT product, url, title, price, checked_at FROM checks"
            " WHERE product LIKE ? OR url LIKE ?"
            " ORDER BY id DESC LIMIT ?",
            (f"%{term}%", f"%{term}%", limit),
        ).fetchall()
        return list(reversed(rows))
    finally:
        conn.close()
