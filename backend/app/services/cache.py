import json
import time
from typing import Any, Optional

from app.db.database import get_connection

DEFAULT_TTL_SECONDS = 86400  # 24h


def _ensure_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


_ensure_table()


def get_cached(key: str) -> Optional[Any]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        if float(row["expires_at"]) < time.time():
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()
            return None
        return json.loads(row["value"])
    finally:
        conn.close()


def set_cached(key: str, value: Any, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
    expires_at = time.time() + ttl_seconds
    payload = json.dumps(value, ensure_ascii=False)
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO cache (key, value, expires_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                expires_at = excluded.expires_at
            """,
            (key, payload, expires_at),
        )
        conn.commit()
    finally:
        conn.close()
