from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from app.api import rag as rag_router
from app.api import riot as riot_router
from app.api import clip as clip_router
from app.api import analyze as analyze_router
from app.api import history as history_router

app = FastAPI(title="LoL Coach MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rag_router.router)
app.include_router(riot_router.router)
app.include_router(clip_router.router)
app.include_router(analyze_router.router)
app.include_router(history_router.router)


# 서버 기동 시 오래된 클립 1회 자동 정리
try:
    from app.services.cleanup import sweep_old_clips

    _swept = sweep_old_clips()
    if _swept:
        print(f"[Startup] 오래된 클립 {len(_swept)}개 정리")
except Exception as err:
    print(f"[Startup] clip sweep skipped: {err}")


@app.get("/health")
def health():
    return {"status": "ok"}
