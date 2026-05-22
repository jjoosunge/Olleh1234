from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.history import (
    delete_analysis,
    get_analysis,
    list_analyses,
    rate_analysis,
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
    rec = rate_analysis(analysis_id, req.reading, req.coaching)
    if rec is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return rec


@router.delete("/analyses/{analysis_id}")
def remove(analysis_id: int) -> dict:
    if not delete_analysis(analysis_id):
        raise HTTPException(status_code=404, detail="Analysis not found")
    return {"deleted": True}
