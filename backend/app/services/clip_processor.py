import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(
            f"{name} 실행 파일을 찾을 수 없습니다. FFmpeg가 설치되어 PATH에 등록돼 있어야 합니다."
        )


def get_video_info(video_path: Path) -> dict:
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    _require_binary("ffprobe")

    cmd = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed: {proc.stderr.strip()[:500] or 'unknown error'}"
        )

    data = json.loads(proc.stdout or "{}")
    duration_raw = data.get("format", {}).get("duration")
    try:
        duration = float(duration_raw) if duration_raw is not None else 0.0
    except (TypeError, ValueError):
        duration = 0.0

    width: Optional[int] = None
    height: Optional[int] = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width = stream.get("width")
            height = stream.get("height")
            break

    if duration <= 0 and width is None:
        raise RuntimeError("영상 메타데이터를 읽지 못했습니다. 파일이 손상됐을 수 있습니다.")

    return {
        "duration": duration,
        "width": width,
        "height": height,
    }


def extract_frames(
    video_path: Path,
    output_dir: Path,
    fps_interval: int = 3,
) -> dict:
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if fps_interval <= 0:
        raise ValueError("fps_interval must be > 0")

    _require_binary("ffmpeg")

    info = get_video_info(video_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    pattern = output_dir / "frame_%04d.jpg"
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-vf", f"fps=1/{fps_interval},scale=1280:720",
        "-q:v", "5",
        str(pattern),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg frame extraction failed: {proc.stderr.strip()[-800:] or 'unknown error'}"
        )

    frame_count = sum(1 for _ in output_dir.glob("frame_*.jpg"))

    minimap_count = _extract_minimap(
        video_path, output_dir, fps_interval, info["width"], info["height"]
    )

    return {
        "frame_count": frame_count,
        "minimap_count": minimap_count,
        "duration_seconds": round(info["duration"], 2),
        "fps_interval": fps_interval,
        "width": info["width"],
        "height": info["height"],
    }


# LoL 미니맵은 우하단 모서리에 고정된 정사각형. 인게임 미니맵 스케일
# 설정에 따라 크기가 달라지므로, 실측(1920x1080 기준 미니맵 ≈ 화면
# 높이의 30%, 우하단)보다 넉넉한 36% 정사각형을 우하단 모서리에서
# 잘라 미니맵이 절대 잘리지 않게 한다. 전체 프레임 화질은 그대로 두고
# '미니맵 한정'으로만: 원본 해상도에서 크롭 → 896px 업스케일 →
# JPEG 최고 화질(q:v 1)로 모든 아이콘/와드/핑이 또렷하게 보이게 한다.
MINIMAP_SIDE_RATIO = 0.36
MINIMAP_OUT_PX = 896


def _extract_minimap(
    video_path: Path,
    output_dir: Path,
    fps_interval: int,
    src_w: Optional[int],
    src_h: Optional[int],
) -> int:
    """원본 해상도에서 미니맵 영역만 크롭해 고화질로 별도 추출.
    실패하거나 해상도를 모르면 조용히 건너뛴다(업로드는 계속 진행)."""
    if not src_w or not src_h:
        return 0

    side = int(round(src_h * MINIMAP_SIDE_RATIO))
    side = max(1, min(side, src_w, src_h))
    x = src_w - side
    y = src_h - side

    mm_pattern = output_dir / "minimap_%04d.jpg"
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-vf",
        (
            f"fps=1/{fps_interval},"
            f"crop={side}:{side}:{x}:{y},"
            f"scale={MINIMAP_OUT_PX}:{MINIMAP_OUT_PX}:flags=lanczos"
        ),
        "-q:v", "1",
        str(mm_pattern),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return 0
    return sum(1 for _ in output_dir.glob("minimap_*.jpg"))
