import json
import shutil
import uuid
from enum import IntEnum
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse


class FpsIntervalChoice(IntEnum):
    ONE_SEC = 1
    TWO_SEC = 2
    THREE_SEC = 3

from app.services import cv_processor
from app.services.cleanup import sweep_old_clips
from app.services.clip_processor import extract_frames

router = APIRouter(prefix="/api/clip", tags=["clip"])

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CLIPS_DIR = PROJECT_ROOT / "backend" / "uploads" / "clips"

MAX_FILE_SIZE = 200 * 1024 * 1024
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
READ_CHUNK = 1024 * 1024


def _validate_clip_id(clip_id: str) -> None:
    try:
        uuid.UUID(clip_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Clip not found")


def _clip_dir(clip_id: str) -> Path:
    _validate_clip_id(clip_id)
    return CLIPS_DIR / clip_id


def _load_metadata(clip_id: str) -> dict:
    meta_path = _clip_dir(clip_id) / "metadata.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Clip not found")
    return json.loads(meta_path.read_text(encoding="utf-8"))


@router.post("/upload")
async def upload_clip(
    video: UploadFile = File(...),
    fps_interval: FpsIntervalChoice = Form(default=FpsIntervalChoice.THREE_SEC),
    keep_original: bool = Form(default=False),
):
    fps_value = int(fps_interval)
    filename = video.filename or ""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported extension '{ext}'. "
                f"Allowed: {sorted(ALLOWED_EXTENSIONS)}"
            ),
        )

    clip_id = str(uuid.uuid4())
    clip_dir = CLIPS_DIR / clip_id
    clip_dir.mkdir(parents=True, exist_ok=True)
    original_path = clip_dir / f"original{ext}"

    try:
        total = 0
        with open(original_path, "wb") as f:
            while True:
                chunk = await video.read(READ_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Max {MAX_FILE_SIZE // (1024 * 1024)}MB",
                    )
                f.write(chunk)

        frames_dir = clip_dir / "frames"
        try:
            result = extract_frames(
                original_path, frames_dir, fps_interval=fps_value
            )
        except (RuntimeError, FileNotFoundError, ValueError) as err:
            raise HTTPException(status_code=500, detail=str(err))

        metadata = {
            "clip_id": clip_id,
            "original_filename": filename,
            "extension": ext,
            "frame_count": result["frame_count"],
            "minimap_count": result.get("minimap_count", 0),
            "duration_seconds": result["duration_seconds"],
            "fps_interval": result["fps_interval"],
            "width": result.get("width"),
            "height": result.get("height"),
            "original_kept": bool(keep_original),
        }
        (clip_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if not keep_original and original_path.exists():
            try:
                original_path.unlink()
            except OSError:
                pass

        # 업로드마다 오래된 클립 자동 정리
        try:
            sweep_old_clips()
        except Exception as err:
            print(f"[Clip] sweep skipped: {err}")

        return metadata

    except HTTPException:
        shutil.rmtree(clip_dir, ignore_errors=True)
        raise
    except Exception as err:
        shutil.rmtree(clip_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {err}")


@router.get("/list")
def list_clips() -> dict:
    """업로드된 클립 목록 (재사용용). 최신순.
    경로가 /{clip_id}보다 먼저 등록돼야 'list'가 clip_id로 해석되지 않는다."""
    if not CLIPS_DIR.exists():
        return {"clips": []}
    items = []
    for d in CLIPS_DIR.iterdir():
        meta_path = d / "metadata.json"
        if not d.is_dir() or not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        meta["uploaded_at"] = meta_path.stat().st_mtime
        items.append(meta)
    items.sort(key=lambda m: m.get("uploaded_at", 0), reverse=True)
    return {"clips": items}


@router.get("/{clip_id}")
def get_clip(clip_id: str) -> dict:
    return _load_metadata(clip_id)


@router.get("/{clip_id}/frames/{frame_number}")
def get_frame(clip_id: str, frame_number: int):
    if frame_number < 1:
        raise HTTPException(status_code=404, detail="Frame not found")

    metadata = _load_metadata(clip_id)
    if frame_number > int(metadata.get("frame_count", 0)):
        raise HTTPException(status_code=404, detail="Frame not found")

    frame_path = _clip_dir(clip_id) / "frames" / f"frame_{frame_number:04d}.jpg"
    if not frame_path.exists():
        raise HTTPException(status_code=404, detail="Frame not found")

    return FileResponse(frame_path, media_type="image/jpeg")


@router.get("/{clip_id}/frames/{frame_number}/cv")
def get_frame_cv(clip_id: str, frame_number: int) -> dict:
    """단일 프레임 CV 전처리 미리보기 — 화질·게임 시각·미니맵 점.
    프론트가 프레임 선택 시 호출해 '감지된 게임 시각'을 채운다."""
    if frame_number < 1:
        raise HTTPException(status_code=404, detail="Frame not found")
    metadata = _load_metadata(clip_id)
    if frame_number > int(metadata.get("frame_count", 0)):
        raise HTTPException(status_code=404, detail="Frame not found")

    frames_dir = _clip_dir(clip_id) / "frames"
    frame_path = frames_dir / f"frame_{frame_number:04d}.jpg"
    if not frame_path.exists():
        raise HTTPException(status_code=404, detail="Frame not found")
    mm_path = frames_dir / f"minimap_{frame_number:04d}.jpg"
    return cv_processor.analyze_frame(
        frame_path, mm_path if mm_path.exists() else None
    )


@router.delete("/{clip_id}")
def delete_clip(clip_id: str) -> dict:
    clip_dir = _clip_dir(clip_id)
    if not clip_dir.exists():
        raise HTTPException(status_code=404, detail="Clip not found")
    shutil.rmtree(clip_dir, ignore_errors=True)
    return {"deleted": True}
