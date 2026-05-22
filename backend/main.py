from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from app.api import rag as rag_router
from app.api import riot as riot_router
from app.api import clip as clip_router
from app.api import analyze as analyze_router

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


@app.get("/health")
def health():
    return {"status": "ok"}
