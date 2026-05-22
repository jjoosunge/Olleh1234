from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.analyzer import generate_failure_report, generate_meta_report
from app.services.history import (
    delete_analysis,
    get_analysis,
    list_analyses,
    rate_analysis,
    rating_stats,
    recent_analyses_for_report,
    recent_failed_analyses,
)

router = APIRouter(prefix="/api", tags=["history"])


@router.get("/analyses")
def get_analyses(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    return {"analyses": list_analyses(limit, offset)}


@router.get("/analyses/{analysis_id}")
def get_one(analysis_id: int) -> dict:
    rec = get_analysis(analysis_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return rec


class RatingRequest(BaseModel):
    # 'up' | 'down' | None(미평가). 프론트는 두 축의 최종 상태를 항상 함께 보낸다.
    reading: Optional[Literal["up", "down"]] = None
    coaching: Optional[Literal["up", "down"]] = None


@router.post("/analyses/{analysis_id}/rating")
def post_rating(analysis_id: int, req: RatingRequest) -> dict:
    # 👎를 받아도 클립을 즉시 삭제하지 않는다 — 잘못된 분석을 다시 검토할 수
    # 있도록 두고, 클립 파일은 7일 시간 정리(sweep_old_clips)에만 맡긴다.
    rec = rate_analysis(analysis_id, req.reading, req.coaching)
    if rec is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return rec


@router.delete("/analyses/{analysis_id}")
def remove(analysis_id: int) -> dict:
    if not delete_analysis(analysis_id):
        raise HTTPException(status_code=404, detail="Analysis not found")
    return {"deleted": True}


@router.get("/meta/stats")
def get_meta_stats() -> dict:
    """분석 평가 분포 (메타 코칭 통계)."""
    return rating_stats()


@router.post("/meta/report")
def post_meta_report() -> dict:
    """최근 분석을 모아 반복 약점 메타 코칭 리포트를 생성한다(Claude 1회 호출)."""
    analyses = recent_analyses_for_report(15)
    try:
        return generate_meta_report(analyses)
    except RuntimeError as err:
        raise HTTPException(status_code=500, detail=str(err))


@router.post("/meta/failure-report")
def post_failure_report() -> dict:
    """👎 받은 분석을 모아 AI의 반복 실패 패턴을 정리한다(Claude 1회 호출)."""
    analyses = recent_failed_analyses(20)
    try:
        return generate_failure_report(analyses)
    except RuntimeError as err:
        raise HTTPException(status_code=500, detail=str(err))
