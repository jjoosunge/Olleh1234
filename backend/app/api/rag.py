from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.rag import search_knowledge

router = APIRouter(prefix="/api", tags=["rag"])


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(5, ge=1, le=50)


class SearchResultItem(BaseModel):
    source_file: str
    content: str
    tags: str
    similarity: float


class SearchResponse(BaseModel):
    results: list[SearchResultItem]


@router.post("/search", response_model=SearchResponse)
def search(req: SearchRequest) -> SearchResponse:
    try:
        results = search_knowledge(req.query, req.top_k)
    except RuntimeError as err:
        raise HTTPException(status_code=500, detail=str(err))
    return SearchResponse(results=results)
