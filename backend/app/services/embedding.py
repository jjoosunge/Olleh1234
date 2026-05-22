import os
import time
from typing import Optional

from openai import OpenAI, APIError, RateLimitError, APIConnectionError

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY 환경변수가 설정되지 않았습니다. backend/.env 확인 필요."
            )
        _client = OpenAI(api_key=api_key)
    return _client


def get_embeddings_batch(
    texts: list[str],
    max_retries: int = 5,
    initial_delay: float = 1.0,
) -> list[list[float]]:
    if not texts:
        return []

    client = _get_client()
    delay = initial_delay
    last_err: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=texts,
            )
            return [item.embedding for item in response.data]
        except (RateLimitError, APIConnectionError, APIError) as err:
            last_err = err
            if attempt == max_retries - 1:
                break
            time.sleep(delay)
            delay *= 2

    raise RuntimeError(
        f"임베딩 호출이 {max_retries}회 재시도 후 실패했습니다: {last_err}"
    )


def get_embedding(text: str) -> list[float]:
    return get_embeddings_batch([text])[0]
