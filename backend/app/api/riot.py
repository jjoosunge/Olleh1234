from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services.cache import get_cached, set_cached
from app.services.riot_api import RiotAPIClient, RiotAPIError

router = APIRouter(prefix="/api", tags=["riot"])

# 420 = 5v5 솔로/듀오 랭크. 워크플로우 ①단계는 솔로랭크만 노출한다.
SOLO_RANKED_QUEUE = 420


def _client() -> RiotAPIClient:
    try:
        return RiotAPIClient()
    except RuntimeError as err:
        raise HTTPException(status_code=500, detail=str(err))


def _translate(err: RiotAPIError) -> HTTPException:
    if err.status_code in (401, 403):
        return HTTPException(status_code=401, detail="Riot API key expired or invalid")
    if err.status_code == 429:
        return HTTPException(status_code=503, detail="Rate limit exceeded, please retry")
    return HTTPException(status_code=502, detail=err.message)


def _refine_match(match: dict) -> dict:
    info = match.get("info", {}) or {}
    metadata = match.get("metadata", {}) or {}

    participants: list[dict[str, Any]] = []
    for p in info.get("participants", []) or []:
        participants.append(
            {
                "puuid": p.get("puuid"),
                "championName": p.get("championName"),
                "summonerName": p.get("summonerName") or p.get("riotIdGameName"),
                "teamId": p.get("teamId"),
                "kills": p.get("kills"),
                "deaths": p.get("deaths"),
                "assists": p.get("assists"),
                "items": [p.get(f"item{i}") for i in range(7)],
                "win": p.get("win"),
            }
        )

    return {
        "matchId": metadata.get("matchId"),
        "gameDuration": info.get("gameDuration"),
        "queueId": info.get("queueId"),
        "gameCreation": info.get("gameCreation"),
        "participants": participants,
    }


@router.get("/summoner/{game_name}/{tag_line}")
def get_summoner(game_name: str, tag_line: str) -> dict:
    client = _client()
    try:
        data = client.get_account_by_riot_id(game_name, tag_line)
    except RiotAPIError as err:
        raise _translate(err)
    if data is None:
        raise HTTPException(status_code=404, detail="Summoner not found")
    return {
        "puuid": data.get("puuid"),
        "game_name": data.get("gameName"),
        "tag_line": data.get("tagLine"),
    }


@router.get("/matches/{puuid}")
def get_match_ids(
    puuid: str,
    count: int = Query(20, ge=1, le=100),
    queue: int = Query(
        SOLO_RANKED_QUEUE,
        ge=0,
        description="큐 ID 필터. 기본 420(솔로랭크). 0이면 전체 큐.",
    ),
) -> dict:
    client = _client()
    queue_filter = queue if queue > 0 else None
    try:
        ids = client.get_match_ids_by_puuid(
            puuid, count=count, queue=queue_filter
        )
    except RiotAPIError as err:
        raise _translate(err)
    return {"match_ids": ids, "queue": queue_filter}


@router.get("/match/{match_id}")
def get_match(match_id: str) -> dict:
    cache_key = f"match:{match_id}"
    cached = get_cached(cache_key)
    if cached is not None:
        return cached

    client = _client()
    try:
        data = client.get_match_by_id(match_id)
    except RiotAPIError as err:
        raise _translate(err)
    if data is None:
        raise HTTPException(status_code=404, detail="Match not found")

    refined = _refine_match(data)
    set_cached(cache_key, refined)
    return refined


@router.get("/match/{match_id}/timeline")
def get_timeline(match_id: str) -> dict:
    cache_key = f"timeline:{match_id}"
    cached = get_cached(cache_key)
    if cached is not None:
        return cached

    client = _client()
    try:
        data = client.get_match_timeline(match_id)
    except RiotAPIError as err:
        raise _translate(err)
    if data is None:
        raise HTTPException(status_code=404, detail="Match timeline not found")

    set_cached(cache_key, data)
    return data
