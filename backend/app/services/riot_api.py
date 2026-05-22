import os
import time
import urllib.parse
from typing import Any, Optional

import requests

REGIONAL_DEFAULT = "asia"
PLATFORM_DEFAULT = "kr"

DEFAULT_TIMEOUT = 10
DEFAULT_MAX_RETRIES = 3


class RiotAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class RiotAPIClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        regional: str = REGIONAL_DEFAULT,
        platform: str = PLATFORM_DEFAULT,
    ):
        self.api_key = api_key or os.environ.get("RIOT_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "RIOT_API_KEY 환경변수가 설정되지 않았습니다. backend/.env 확인 필요."
            )
        self.regional_base = f"https://{regional}.api.riotgames.com"
        self.platform_base = f"https://{platform}.api.riotgames.com"
        self.session = requests.Session()
        self.session.headers.update({"X-Riot-Token": self.api_key})

    def _request(
        self,
        url: str,
        params: Optional[dict] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> Optional[Any]:
        for attempt in range(max_retries):
            resp = self.session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            status = resp.status_code

            if status == 200:
                return resp.json()
            if status == 404:
                return None
            if status == 403:
                raise RiotAPIError(403, "Riot API key expired or invalid")
            if status == 401:
                raise RiotAPIError(401, "Riot API key is missing or unauthorized")
            if status == 429:
                if attempt == max_retries - 1:
                    raise RiotAPIError(429, "Rate limit exceeded")
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                time.sleep(retry_after)
                continue
            if 500 <= status < 600:
                if attempt == max_retries - 1:
                    raise RiotAPIError(status, f"Riot API server error: {status}")
                time.sleep(1 + attempt)
                continue

            raise RiotAPIError(status, f"Riot API error {status}: {resp.text[:200]}")

        raise RiotAPIError(429, "Rate limit exceeded")

    def get_account_by_riot_id(
        self, game_name: str, tag_line: str
    ) -> Optional[dict]:
        gn = urllib.parse.quote(game_name, safe="")
        tl = urllib.parse.quote(tag_line, safe="")
        url = (
            f"{self.regional_base}/riot/account/v1/accounts/by-riot-id/{gn}/{tl}"
        )
        return self._request(url)

    def get_match_ids_by_puuid(
        self,
        puuid: str,
        count: int = 20,
        start: int = 0,
        queue: Optional[int] = None,
    ) -> list[str]:
        url = f"{self.regional_base}/lol/match/v5/matches/by-puuid/{puuid}/ids"
        params: dict[str, Any] = {"count": count, "start": start}
        if queue is not None:
            params["queue"] = queue
        result = self._request(url, params=params)
        return result if isinstance(result, list) else []

    def get_match_by_id(self, match_id: str) -> Optional[dict]:
        url = f"{self.regional_base}/lol/match/v5/matches/{match_id}"
        return self._request(url)

    def get_match_timeline(self, match_id: str) -> Optional[dict]:
        url = f"{self.regional_base}/lol/match/v5/matches/{match_id}/timeline"
        return self._request(url)


def _parse_retry_after(value: Optional[str]) -> float:
    if not value:
        return 1.0
    try:
        seconds = float(value)
        return max(0.1, min(seconds, 30.0))
    except ValueError:
        return 1.0
