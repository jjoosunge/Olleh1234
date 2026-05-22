"""클립 파일(프레임·영상) 정리.

분석 텍스트 기록(analyses 테이블)은 영구 보존하고, 무거운 클립 파일만
정리한다. 두 경로 모두 delete_clip_files를 쓴다:
  (1) 오래된 클립 자동 정리 — sweep_old_clips
  (2) 좋은 평가를 못 받은 클립 즉시 삭제 — api/history의 평가 처리에서 호출
"""

import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from app.db.database import PROJECT_ROOT

CLIPS_DIR = PROJECT_ROOT / "backend" / "uploads" / "clips"
# 이 일수보다 오래된 클립 폴더는 자동 정리 대상
DEFAULT_MAX_AGE_DAYS = 7


def _is_uuid(name: str) -> bool:
    try:
        uuid.UUID(name)
        return True
    except ValueError:
        return False


def delete_clip_files(clip_id: str, clips_dir: Optional[Path] = None) -> bool:
    """클립 폴더(프레임·미니맵·영상·metadata)를 삭제한다.
    분석 기록은 건드리지 않는다. 삭제했으면 True."""
    if not _is_uuid(clip_id):
        return False
    clip_dir = (clips_dir or CLIPS_DIR) / clip_id
    if not clip_dir.exists():
        return False
    shutil.rmtree(clip_dir, ignore_errors=True)
    return True


def sweep_old_clips(
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    clips_dir: Optional[Path] = None,
) -> list[str]:
    """수정 시각이 max_age_days보다 오래된 클립 폴더를 삭제한다.
    삭제된 clip_id 목록을 반환한다."""
    base = clips_dir or CLIPS_DIR
    if not base.exists():
        return []
    cutoff = time.time() - max_age_days * 86400
    removed: list[str] = []
    for d in base.iterdir():
        if not d.is_dir() or not _is_uuid(d.name):
            continue
        try:
            if d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed.append(d.name)
        except OSError:
            continue
    return removed
