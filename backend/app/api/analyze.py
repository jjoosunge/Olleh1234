import uuid
from typing import Optional

import anthropic
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services.analyzer import (
    DEFAULT_MODEL,
    analyze_clip,
    prepare_analysis,
    stream_analysis,
)

router = APIRouter(prefix="/api", tags=["analyze"])


class AnalyzeRequest(BaseModel):
    clip_id: str = Field(..., min_length=1)
    user_question: str = Field(..., min_length=1, max_length=2000)
    match_id: Optional[str] = None
    puuid: Optional[str] = None
    # 있으면 단일 프레임 모드(그 1장만 분석), 없으면 멀티프레임 모드.
    frame_number: Optional[int] = Field(default=None, ge=1)
    # 'MM:SS' 게임 시각. 주면 CV 타이머 OCR 대신 이 값으로 타임라인 정렬.
    game_time: Optional[str] = None
    # 사용자 티어 — 코칭 눈높이 보정용
    tier: Optional[str] = None
    model: str = DEFAULT_MODEL


def _validate_clip_id(clip_id: str) -> None:
    try:
        uuid.UUID(clip_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Clip not found")


@router.post("/analyze")
def analyze(req: AnalyzeRequest) -> dict:
    # analyze_clip은 동기 함수다(내부에서 Claude/Riot 블로킹 호출).
    # 라우트를 sync def로 두면 FastAPI가 외부 스레드풀에서 실행해
    # 분석 중에도 이벤트 루프가 막히지 않는다.
    _validate_clip_id(req.clip_id)
    try:
        return analyze_clip(
            clip_id=req.clip_id,
            user_question=req.user_question,
            match_id=req.match_id,
            puuid=req.puuid,
            model=req.model,
            frame_number=req.frame_number,
            game_time=req.game_time,
            tier=req.tier,
        )
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err))
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY가 잘못되었습니다.")
    except anthropic.RateLimitError:
        raise HTTPException(
            status_code=503,
            detail="Claude rate limit exceeded; retry after a short wait.",
        )
    except anthropic.BadRequestError as err:
        raise HTTPException(status_code=400, detail=f"Claude bad request: {err}")
    except anthropic.APIStatusError as err:
        raise HTTPException(
            status_code=502, detail=f"Claude API error {err.status_code}: {err}"
        )
    except RuntimeError as err:
        raise HTTPException(status_code=500, detail=str(err))


@router.post("/analyze/stream")
def analyze_stream(req: AnalyzeRequest) -> StreamingResponse:
    """분석 결과를 토큰 단위 SSE로 스트리밍한다.
    준비 단계 오류는 일반 HTTP 에러, 스트리밍 중 오류는 SSE error 이벤트."""
    _validate_clip_id(req.clip_id)
    try:
        prep = prepare_analysis(
            clip_id=req.clip_id,
            user_question=req.user_question,
            match_id=req.match_id,
            puuid=req.puuid,
            model=req.model,
            frame_number=req.frame_number,
            game_time=req.game_time,
            tier=req.tier,
        )
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err))
    except RuntimeError as err:
        raise HTTPException(status_code=500, detail=str(err))
    return StreamingResponse(
        stream_analysis(prep),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
