import struct

from app.db.database import get_connection
from app.services.embedding import get_embedding


def serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def search_knowledge(query: str, top_k: int = 5) -> list[dict]:
    if not query.strip():
        return []

    embedding = get_embedding(query)
    blob = serialize_f32(embedding)

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                c.source_file,
                c.content,
                c.tags,
                v.distance
            FROM vec_chunks v
            JOIN knowledge_chunks c ON c.id = v.rowid
            WHERE v.embedding MATCH ?
              AND k = ?
            ORDER BY v.distance
            """,
            (blob, top_k),
        ).fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        distance = row["distance"]
        similarity = max(0.0, 1.0 - distance)
        results.append(
            {
                "source_file": row["source_file"],
                "content": row["content"],
                "tags": row["tags"] or "",
                "similarity": round(similarity, 4),
            }
        )
    return results
