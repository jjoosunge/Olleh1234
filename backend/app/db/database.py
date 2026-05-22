import sqlite3
from pathlib import Path

import sqlite_vec

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = PROJECT_ROOT / "db" / "knowledge.db"

EMBEDDING_DIM = 1536


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> sqlite3.Connection:
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            tags TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
            embedding FLOAT[{EMBEDDING_DIM}] distance_metric=cosine
        )
        """
    )
    conn.commit()
    return conn


def reset_db(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM knowledge_chunks")
    conn.execute("DELETE FROM vec_chunks")
    conn.execute("DELETE FROM sqlite_sequence WHERE name='knowledge_chunks'")
    conn.commit()
