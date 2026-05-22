"""CV 전처리 — 프레임 화질 판정, 게임 타이머 OCR, 미니맵 점 검출.

분석에 쓸 '사실'을 코드로 추출해 analyzer가 Claude에 구조화된 형태로
넘기게 한다. 무거운 OCR 모델은 첫 사용 시점에 지연 로딩한다.
"""

import re
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

# --- 튜닝 상수 (실제 프레임에 맞춰 조정 가능) --------------------------
# Laplacian 분산. 이 값 미만이면 '흐림'. 게임 프레임은 UI 엣지가 많아
# 자연 사진보다 값이 높게 나오므로 보수적으로 잡았다.
BLUR_THRESHOLD = 40.0
# 타이머가 있는 상단 영역 (전체 폭 × 상단 9%)
TIMER_STRIP = (0.0, 0.0, 1.0, 0.09)
TIMER_RE = re.compile(r"(\d{1,2}):([0-5]\d)")
GAME_TIME_MAX = 75 * 60  # 게임 길이 상한(초) — OCR 오인식 거르기용
# 미니맵 챔피언 아이콘 테두리 색 범위 (HSV). 적=빨강(0·180 두 구간), 아군=청록.
_ENEMY_HSV = [
    ((0, 110, 110), (12, 255, 255)),
    ((168, 110, 110), (180, 255, 255)),
]
_ALLY_HSV = [((78, 80, 110), (105, 255, 255))]
MIN_DOT_AREA = 6     # 픽셀. 너무 작은 노이즈 제외
MAX_DOT_AREA = 600   # 너무 큰 덩어리(지형/하이라이트) 제외
# ----------------------------------------------------------------------

_ocr_engine = None


def _get_ocr():
    """rapidocr 엔진을 지연 로딩한다(모델 로드가 무겁다)."""
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr_engine = RapidOCR()
    return _ocr_engine


def _crop_ratio(img: np.ndarray, region: tuple) -> np.ndarray:
    h, w = img.shape[:2]
    x1, y1, x2, y2 = region
    return img[int(h * y1) : int(h * y2), int(w * x1) : int(w * x2)]


def _blur_score(gray: np.ndarray) -> float:
    """Laplacian 분산 — 높을수록 선명."""
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def fmt_secs(secs: Optional[int]) -> Optional[str]:
    if secs is None:
        return None
    return f"{secs // 60}:{secs % 60:02d}"


def parse_game_time(text: str) -> Optional[int]:
    """'MM:SS' 문자열 → 초. 형식/범위 검증 실패 시 None."""
    if not text:
        return None
    m = TIMER_RE.search(str(text).replace(" ", ""))
    if not m:
        return None
    secs = int(m.group(1)) * 60 + int(m.group(2))
    return secs if 0 <= secs <= GAME_TIME_MAX else None


def read_game_time(frame_bgr: np.ndarray) -> tuple[Optional[int], str]:
    """프레임 상단에서 게임 타이머(MM:SS)를 OCR한다.
    반환: (게임시각 초 | None, 'ok' | 'failed')."""
    strip = _crop_ratio(frame_bgr, TIMER_STRIP)
    if strip.size == 0:
        return None, "failed"
    # 확대 → OCR 가독성 향상
    big = cv2.resize(strip, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    try:
        result, _ = _get_ocr()(big)
    except Exception as err:
        print(f"[CV] timer OCR failed: {err}")
        return None, "failed"
    if not result:
        return None, "failed"
    for item in result:
        # rapidocr 결과 항목: [box, text, score]
        text = item[1] if len(item) > 1 else ""
        secs = parse_game_time(text)
        if secs is not None:
            return secs, "ok"
    return None, "failed"


def _detect_dots(
    hsv: np.ndarray, ranges: list, side: str, w: int, h: int
) -> list[dict]:
    mask = None
    for lo, hi in ranges:
        m = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
        mask = m if mask is None else cv2.bitwise_or(mask, m)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    dots = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < MIN_DOT_AREA or area > MAX_DOT_AREA:
            continue
        mom = cv2.moments(c)
        if mom["m00"] == 0:
            continue
        dots.append(
            {
                "side": side,
                "x": round(mom["m10"] / mom["m00"] / w, 3),
                "y": round(mom["m01"] / mom["m00"] / h, 3),
            }
        )
    return dots


def detect_minimap_dots(minimap_bgr: np.ndarray) -> list[dict]:
    """미니맵에서 아군/적 챔피언 점을 색으로 검출한다.
    좌표는 미니맵 좌상단 0,0 ~ 우하단 1,1 기준 상대값.
    색 기반 근사이므로 정확도는 프레임에 따라 다르다."""
    h, w = minimap_bgr.shape[:2]
    if h == 0 or w == 0:
        return []
    hsv = cv2.cvtColor(minimap_bgr, cv2.COLOR_BGR2HSV)
    return _detect_dots(hsv, _ENEMY_HSV, "enemy", w, h) + _detect_dots(
        hsv, _ALLY_HSV, "ally", w, h
    )


def analyze_frame(
    frame_path: Path,
    minimap_path: Optional[Path] = None,
    read_timer: bool = True,
) -> dict:
    """단일 프레임 CV 전처리. 실패해도 예외 대신 부분 결과를 돌려준다."""
    out = {
        "frame_quality": "unknown",
        "frame_blur_score": None,
        "minimap_quality": "none",
        "game_time_seconds": None,
        "game_time": None,
        "timer_confidence": "skipped",
        "minimap_dots": [],
    }
    frame = cv2.imread(str(frame_path))
    if frame is None:
        return out

    blur = _blur_score(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    out["frame_blur_score"] = round(blur, 1)
    out["frame_quality"] = "ok" if blur >= BLUR_THRESHOLD else "low"

    if read_timer:
        secs, conf = read_game_time(frame)
        out["game_time_seconds"] = secs
        out["game_time"] = fmt_secs(secs)
        out["timer_confidence"] = conf

    if minimap_path is not None and Path(minimap_path).exists():
        mm = cv2.imread(str(minimap_path))
        if mm is not None:
            mm_blur = _blur_score(cv2.cvtColor(mm, cv2.COLOR_BGR2GRAY))
            out["minimap_quality"] = (
                "ok" if mm_blur >= BLUR_THRESHOLD else "low"
            )
            if out["minimap_quality"] == "ok":
                out["minimap_dots"] = detect_minimap_dots(mm)
    return out
