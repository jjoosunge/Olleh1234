"""분석 히스토리 저장·평가·피드백 학습.

모든 /api/analyze 호출 결과를 analyses 테이블에 영구 저장한다.
사용자가 '장면 판독'과 '코칭'을 각각 👍/👎로 평가할 수 있고, 두 축
모두 👍를 받은 분석은 질문 임베딩을 vec_analyses(sqlite-vec)에 적재해
이후 분석에서 질문 유사도로 검색·재사용된다(in-context 학습).
"""

import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.db.database import get_connection
from app.services.embedding import get_embedding
from app.services.rag import serialize_f32

# vec_analyses 편입 조건이 되는 평가값
RATING_VALUES = ("up", "down")


def _ensure_tables() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                clip_id TEXT,
                frame_number INTEGER,
                match_id TEXT,
                puuid TEXT,
                model TEXT,
                user_question TEXT NOT NULL,
                analysis_text TEXT NOT NULL,
                metadata_json TEXT,
                notes_json TEXT,
                examples_used_json TEXT,
                rating_reading TEXT,
                rating_coaching TEXT,
                rated_at TEXT
            )
            """
        )
        # 우수 분석(두 축 모두 👍)의 질문 임베딩. rowid = analyses.id
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_analyses USING vec0(
                embedding FLOAT[1536] distance_metric=cosine
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


_ensure_tables()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_exemplary(reading: Optional[str], coaching: Optional[str]) -> bool:
    """판독·코칭 모두 👍여야 학습 예시 풀(vec_analyses)에 편입된다."""
    return reading == "up" and coaching == "up"


def save_analysis(
    *,
    clip_id: Optional[str],
    frame_number: Optional[int],
    match_id: Optional[str],
    puuid: Optional[str],
    model: str,
    user_question: str,
    analysis_text: str,
    metadata: dict,
    notes: list[dict],
    examples_used: list[int],
) -> int:
    """분석 1건을 저장하고 새 id를 반환한다."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO analyses (
                created_at, clip_id, frame_number, match_id, puuid, model,
                user_question, analysis_text, metadata_json, notes_json,
                examples_used_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now(), clip_id, frame_number, match_id, puuid, model,
                user_question, analysis_text,
                json.dumps(metadata, ensure_ascii=False),
                json.dumps(notes, ensure_ascii=False),
                json.dumps(examples_used, ensure_ascii=False),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _row_to_dict(row: Any, *, full: bool) -> dict:
    out = {
        "id": row["id"],
        "created_at": row["created_at"],
        "clip_id": row["clip_id"],
        "frame_number": row["frame_number"],
        "match_id": row["match_id"],
        "model": row["model"],
        "user_question": row["user_question"],
        "analysis_text": row["analysis_text"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "rating_reading": row["rating_reading"],
        "rating_coaching": row["rating_coaching"],
        "rated_at": row["rated_at"],
    }
    if full:
        out["puuid"] = row["puuid"]
        out["notes"] = json.loads(row["notes_json"] or "[]")
        out["examples_used"] = json.loads(row["examples_used_json"] or "[]")
    return out


def list_analyses(limit: int = 50, offset: int = 0) -> list[dict]:
    """최신순 분석 목록."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM analyses ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r, full=False) for r in rows]


def get_analysis(analysis_id: int) -> Optional[dict]:
    """분석 1건 전체(코치 노트·주입 예시 포함)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row, full=True) if row else None


def rate_analysis(
    analysis_id: int,
    reading: Optional[str],
    coaching: Optional[str],
) -> Optional[dict]:
    """평가를 갱신한다. reading/coaching 은 'up'|'down'|None(미평가).
    두 축 모두 👍로 바뀌면 vec_analyses에 편입, 아니게 되면 제외한다."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT rating_reading, rating_coaching, user_question "
            "FROM analyses WHERE id = ?",
            (analysis_id,),
        ).fetchone()
        if row is None:
            return None
        was = _is_exemplary(row["rating_reading"], row["rating_coaching"])
        question = row["user_question"]
        conn.execute(
            "UPDATE analyses SET rating_reading=?, rating_coaching=?, "
            "rated_at=? WHERE id=?",
            (reading, coaching, _now(), analysis_id),
        )
        conn.commit()
    finally:
        conn.close()

    now = _is_exemplary(reading, coaching)
    if now and not was:
        _add_to_vec(analysis_id, question)
    elif was and not now:
        _remove_from_vec(analysis_id)

    return get_analysis(analysis_id)


def delete_analysis(analysis_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM analyses WHERE id=?", (analysis_id,))
        conn.execute("DELETE FROM vec_analyses WHERE rowid=?", (analysis_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def clip_has_exemplary(clip_id: str) -> bool:
    """해당 클립의 분석 중 두 축 모두 👍인 것이 하나라도 있으면 True."""
    if not clip_id:
        return False
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT count(*) AS c FROM analyses "
            "WHERE clip_id = ? AND rating_reading = 'up' "
            "AND rating_coaching = 'up'",
            (clip_id,),
        ).fetchone()
    finally:
        conn.close()
    return row["c"] > 0


def rating_stats() -> dict:
    """전체 분석의 평가 분포 (메타 코칭 통계용)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT rating_reading, rating_coaching FROM analyses"
        ).fetchall()
    finally:
        conn.close()
    total = len(rows)

    def tally(key: str) -> dict:
        up = sum(1 for r in rows if r[key] == "up")
        down = sum(1 for r in rows if r[key] == "down")
        return {"up": up, "down": down, "unrated": total - up - down}

    return {
        "total_analyses": total,
        "reading": tally("rating_reading"),
        "coaching": tally("rating_coaching"),
    }


def recent_analyses_for_report(limit: int = 15) -> list[dict]:
    """메타 리포트용 최근 분석(질문 + 분석 텍스트). 👎 받은 분석은 제외."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT user_question, analysis_text FROM analyses "
            "WHERE rating_reading IS NOT 'down' "
            "AND rating_coaching IS NOT 'down' "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "user_question": r["user_question"],
            "analysis_text": r["analysis_text"],
        }
        for r in rows
    ]


def recent_failed_analyses(limit: int = 20) -> list[dict]:
    """판독 또는 코칭에서 👎를 받은 최근 분석 — 실패 패턴 분석용."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT user_question, analysis_text, rating_reading, "
            "rating_coaching FROM analyses "
            "WHERE rating_reading = 'down' OR rating_coaching = 'down' "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "user_question": r["user_question"],
            "analysis_text": r["analysis_text"],
            "rating_reading": r["rating_reading"],
            "rating_coaching": r["rating_coaching"],
        }
        for r in rows
    ]


def _add_to_vec(analysis_id: int, question: str) -> None:
    """우수 분석의 질문을 임베딩해 학습 풀에 넣는다. 실패해도 조용히 넘어간다."""
    try:
        emb = get_embedding(question)
    except Exception as err:  # 임베딩 실패가 평가 자체를 막지 않도록
        print(f"[History] vec embed skipped for {analysis_id}: {err}")
        return
    conn = get_connection()
    try:
        conn.execute("DELETE FROM vec_analyses WHERE rowid=?", (analysis_id,))
        conn.execute(
            "INSERT INTO vec_analyses (rowid, embedding) VALUES (?, ?)",
            (analysis_id, serialize_f32(emb)),
        )
        conn.commit()
    finally:
        conn.close()


def _remove_from_vec(analysis_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM vec_analyses WHERE rowid=?", (analysis_id,))
        conn.commit()
    finally:
        conn.close()


def search_good_analyses(question: str, top_k: int = 2) -> list[dict]:
    """질문과 의미가 비슷한, 사용자가 좋게 평가한 과거 분석을 검색한다.
    학습 풀이 비어 있으면 임베딩 호출 없이 바로 빈 리스트."""
    if not question.strip():
        return []

    conn = get_connection()
    try:
        pool = conn.execute(
            "SELECT count(*) AS c FROM vec_analyses"
        ).fetchone()["c"]
    finally:
        conn.close()
    if pool == 0:
        return []

    try:
        emb = get_embedding(question)
    except Exception as err:  # 검색 실패가 분석을 막지 않도록
        print(f"[History] good-analysis search skipped: {err}")
        return []
    blob = serialize_f32(emb)

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT a.id, a.user_question, a.analysis_text, v.distance
            FROM vec_analyses v
            JOIN analyses a ON a.id = v.rowid
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (blob, top_k),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": r["id"],
            "user_question": r["user_question"],
            "analysis_text": r["analysis_text"],
            "similarity": round(max(0.0, 1.0 - r["distance"]), 4),
        }
        for r in rows
    ]
