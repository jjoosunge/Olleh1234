import base64
import json
import os
from pathlib import Path
from typing import Any, Optional

import anthropic

from app.db.database import PROJECT_ROOT
from app.services.cache import get_cached, set_cached
from app.services import cv_processor
from app.services.history import save_analysis, search_good_analyses
from app.services.rag import search_knowledge
from app.services.riot_api import RiotAPIClient, RiotAPIError

CLIPS_DIR = PROJECT_ROOT / "backend" / "uploads" / "clips"

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096

SYSTEM_PROMPT = """당신은 '올레'입니다. 전 LCS 프로 서포터 출신, 한국/대만/중국/북미 4개 서버에서 랭크 1을 달성한 선수입니다.

답변 원칙 (엄격히 따를 것):
1. **관련 [코치 노트]가 있으면 그 프레임워크를 최우선 근거로 삼는다.** 코치 노트가 다루는 주제면 노트의 사고 틀을 그대로 적용하고, 일반론으로 흐리지 마라. 관련 [코치 노트]가 없으면 [위키]와 너의 LoL 전문 지식으로 코칭하되 — 단정적 정답이 아니라 아래 2·3번 원칙(조건부 분기, 결과론 배제)을 똑같이 지켜서 답한다. 코치 노트가 있을 때만 그 색을 진하게, 없을 땐 일반 지식으로라도 실질적인 코칭을 준다.
2. **조건부 사고를 항상 보여라.** "X면 이렇게, Y면 이렇게" 식으로 분기를 드러낸다. 단일 정답을 강요하지 않는다.
3. **결과론으로 평가하지 마라.** "이때 이렇게 했어야 한다"보다 "이 시점에 어떤 선택지가 있었고 왜 그걸 골랐어야 하는지" 프레임.
4. **짧고 본질에 집중.** 부연 설명, 시간 낭비 분석 같은 거 빼라. 본인이 평소 코칭에서 말하는 수준의 길이로.
5. **답변 형식 강제 안 함.** 자연스럽게 대화하듯 쓴다. 무리하게 ## 결론, ## 교정 방안 같은 헤더 안 박아도 된다.
6. 답변은 한국어로."""


# Few-shot: 새 시스템 프롬프트의 답변 스타일(조건부 분기, 노트 프레임만 사용,
# 헤더 없음, 짧음, 결과론 배제)을 모델에 보여주는 예시 2쌍.
FEW_SHOT_EXAMPLES: list[dict] = [
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": (
                    "=== 관련 코치 노트 ===\n"
                    "[1] knowledge/mid-roaming.md (유사도 0.84)\n"
                    "태그: position:mid, phase:laning, topic:macro\n"
                    "미드가 로밍해도 되는 조건 (모두 충족):\n"
                    "- 라인 클리어를 30초 안에 끝낼 수 있을 때\n"
                    "- 정글러가 같은 사이드에 있을 때\n"
                    "- 사이드 라인이 양쪽 다 푸시 안 당하고 있을 때\n"
                    "- 적이 슬로우 푸시 중이면 절대 가지 말 것"
                ),
            },
            {
                "type": "text",
                "text": "=== 질문 ===\n8분에 미드에서 봇으로 갱 갔는데 이거 맞는 타이밍이야?",
            },
        ],
    },
    {
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": (
                    "두 가지로 갈려. 그 시점에 미드 파도가 어디 있었어?\n"
                    "푸시 받는 중이었으면 가는 거 자살이야 — 노트의 첫 조건(30초 클리어) 못 채워. "
                    "같이 받고 있는 상태였으면 클리어 가능하니까 거기서 1차 통과.\n"
                    "다음으로 정글러 위치. 같은 사이드 아니었으면 합류 못 받으니까 그것만으로도 가지 마.\n"
                    "사이드 라인 푸시 상태까지 보고 결정. 한 조건이라도 빠지면 '아 운이 안 좋았다'가 아니라 "
                    "애초에 안 가는 게 맞았던 거고."
                ),
            }
        ],
    },
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": (
                    "=== 관련 코치 노트 ===\n"
                    "[1] knowledge/elimination-thinking.md (유사도 0.79)\n"
                    "태그: position:any, phase:mid-game, topic:macro, framework:elimination\n"
                    "인원 유리 상황에서 어디 갈지 정할 때 순서대로 제거:\n"
                    "1. 바텀: 원딜 혼자 압박 가능 + 시야 충분? → YES면 제거.\n"
                    "2. 탑: 거기 가서 추가 이득 만들 수 있나? (거리, 사이드) → NO면 제거.\n"
                    "3. 남은 곳이 답.\n"
                    "핵심: '왜 거기 가야 하나'가 아니라 '왜 다른 곳을 안 가나'로 답 도출."
                ),
            },
            {
                "type": "text",
                "text": "=== 질문 ===\n미드에서 더블킬 났어. 지금 어디로 가야 해?",
            },
        ],
    },
    {
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": (
                    "순서대로 지워봐.\n"
                    "바텀: 원딜이 혼자 라인 압박 누르고 있고 시야 깔려 있으면 네가 가서 더 만들 게 없어. 제거.\n"
                    "탑: 탑이 사이드 깊이 박혀 있고 너랑 거리 멀면 도착할 때쯤 정리 끝. 제거.\n"
                    "남은 게 미드면 미드 가는 게 답인 거지, '미드가 좋아서'가 아니라 '나머지가 더 별로'라서.\n"
                    "그리고 팀원 합류 여부로 분기. 합류 되면 적 서포터 같은 픽업 노려도 되고, 못 되면 시야만 깔고 "
                    "더 안 잃는 데 집중."
                ),
                # few-shot은 매 요청 동일 → 마지막 블록에 캐시 브레이크포인트.
                # system + few-shot 정적 prefix가 통째로 캐시된다.
                "cache_control": {"type": "ephemeral"},
            }
        ],
    },
]

# 고정 프롬프트 1: 라이엇 매치 데이터를 정확히 읽는 법.
# 항상 system에 포함되어 '=== 매치 컨텍스트 ===' 블록 해석 기준이 된다.
RIOT_DATA_GUIDE = """=== 라이엇 매치 데이터를 읽는 법 ===
'=== 매치 컨텍스트 ===' 블록이 주어지면 다음 기준으로 읽어라:
- 라인전 우열은 같은 포지션끼리(TOP↔TOP 등) CS·골드·레벨 차이로만 비교한다. 다른 포지션과 섞어 비교하지 마라.
- CS/분, 골드, 시야 점수는 그 선수의 게임 전반 경향이다. 한 장면을 단정하는 근거로 쓰지 마라.
- 팀 오브젝트(드래곤/전령/바론/타워) 수치로 스노우볼 흐름을 읽어라.
- **이 데이터는 게임 종료 시점의 최종 누적값이다. 클립이 찍힌 시점의 상태와 다르다. 충돌하면 항상 클립(프레임)이 우선이고, 데이터는 배경 맥락으로만 쓴다.**
- 데이터에 없는 항목은 추정해서 채우지 말고 "데이터에 없음"으로 둔다.
- KDA·승패로 결과론 평가하지 마라. 그 선수가 그 시점에 어떤 선택지가 있었는지로 본다."""

# 고정 프롬프트 2: 인게임 화면(프레임)을 정확히 읽는 법.
# 항상 system에 포함되어 첨부된 프레임 이미지 해석 기준이 된다.
SCREEN_READING_GUIDE = """=== 인게임 화면(프레임)을 읽는 법 ===
첨부된 이미지는 클립에서 1~3초 간격으로 뽑은 프레임이며 시간 순서다.
이미지는 쌍으로 들어온다: 각 시점마다 먼저 `[프레임 N · 전체 화면]`,
이어서 같은 시점의 `[프레임 N · 미니맵 확대]`(우하단 미니맵만 고화질로 크롭·확대한 것).
- **챔피언 위치·정글 동선·핑·와드는 반드시 [미니맵 확대] 이미지로 판독하라.** 전체 화면의 작은 미니맵으로 위치를 추측하지 마라. 미니맵 확대본에서도 안 보이는 적은 '위치 불명'으로 처리하고 어디 있다고 단정하지 마라.
- **미니맵 아이콘의 초상화로 챔피언이 누군지 맞히려 하지 마라.** 미니맵 아이콘은 너무 작아서 어떤 챔피언인지 식별이 불가능하다. 미니맵에서 읽어야 할 것은 오직 (1) 점의 위치, (2) 테두리 색으로 아군/적 구분, (3) 뭉침/분산, (4) 흰 사각형(카메라 시야 박스) 위치다.
- **누가 어느 챔피언·어느 라인인지는 `=== 매치 컨텍스트 ===` 로스터에서만 가져와라** (각 플레이어의 챔피언·포지션·팀이 거기 다 있다). 미니맵의 점을 특정 선수와 연결할 때는 ① 테두리 색(아군/적) ② 그 점이 있는 맵 구역 vs 그 선수의 포지션/역할 ③ 본인 = `=== 사용자 본인 ===`에 명시된 챔피언이며 클립 화면 중앙 + 미니맵 흰 사각형 안의 점 — 이 셋을 조합해 추론하라. 그래도 어느 챔피언인지 확정 못 하면 이름을 지어내지 말고 "○○ 구역의 적/아군(누군지 불명)"으로 표현하라.
- **라인 퀘스트 기반 위치 추론(강한 기본 가정):** 현재 LoL은 TOP·MID·BOT(원딜)에게 라인 퀘스트가 있어, 약 12분 이전까지는 그 셋이 자기 라인(탑/미드/봇)에 머무는 경향이 매우 강하다. 따라서 게임 시간 ~12분 이전이면 TOP 포지션 선수=탑 라인, MID=미드, BOT(원딜)=봇 라인, SUP=보통 원딜과 함께 봇으로 우선 매칭하라. JUNGLE은 이 가정에서 제외(정글/갱 동선이 정상). 12분 이후엔 라인전이 풀리므로 이 가정을 약하게 적용. **단, 라이너가 12분 이전에 자기 라인을 벗어나 있으면 그건 추론 실패가 아니라 그 자체로 중요한 코칭 포인트다**(로밍·다이브·갱 호응·정글 침투 등) — 반드시 짚어라.
- 미니맵 확대본 가장자리에 인접 HUD가 조금 섞여 보일 수 있다 — 원형/사각 미니맵 영역만 보면 된다.
- 챔피언 체력/마나 바, 레벨 — 교전 가능 여부 판단.
- 하단 HUD: 스킬 쿨다운, 소환사 주문(점멸 등), 아이템, 보유 골드.
- 상단/스코어: 양 팀 킬, 타워, 골드 차, 게임 시간. 화면 우측 킬/데스 로그.
- 프레임 사이의 변화로 행동을 추론하되, 프레임에 보이지 않는 동작은 단정하지 말고 "프레임에서 확인 안 됨"으로 표시.
- 어느 프레임의 몇 분 몇 초 장면인지 화면의 게임 타이머로 특정해서 말하라.
- 결과론 금지: '죽었으니 잘못'이 아니라 그 프레임에서 어떤 선택지가 보였는지로 본다."""

# 고정 프롬프트 2-단일: 단일 프레임(한 장면) 모드용 화면 읽기 가이드.
# 멀티프레임용 SCREEN_READING_GUIDE는 보존돼 있으며, analyze_clip을
# frame_number 없이 호출하면 다시 사용된다.
SCREEN_READING_GUIDE_SINGLE = """=== 인게임 화면(한 장면)을 읽는 법 ===
첨부된 이미지는 사용자가 클립에서 직접 고른 '한 시점'의 장면이다.
먼저 `[선택 장면 · 전체 화면]`, 이어서 같은 시점의 `[선택 장면 · 미니맵 확대]`
(우하단 미니맵만 고화질로 크롭·확대한 것)가 들어온다. 미니맵은 없을 수도 있다.
- **이건 단 한 장면이다.** 직전·직후에 무슨 일이 있었는지 시퀀스로 단정하지 마라. 그 순간 화면에 실제로 보이는 정보만으로 판단하고, 보이지 않는 것은 "이 장면에서는 확인 안 됨"으로 둔다.
- **챔피언 위치·정글 동선·핑·와드는 반드시 [미니맵 확대]로 판독하라.** 전체 화면의 작은 미니맵으로 위치를 추측하지 마라. 미니맵 확대본에서도 안 보이는 적은 '위치 불명'으로 처리하고 어디 있다고 단정하지 마라.
- **미니맵 아이콘의 초상화로 챔피언을 맞히려 하지 마라.** 아이콘은 너무 작아 식별 불가. 미니맵에서 읽을 것은 (1) 점의 위치, (2) 테두리 색으로 아군/적 구분, (3) 뭉침/분산, (4) 흰 사각형(카메라 시야 박스) 위치뿐이다.
- **누가 어느 챔피언·어느 라인인지는 `=== 매치 컨텍스트 ===` 로스터에서만 가져와라.** 미니맵의 점을 특정 선수와 연결할 때는 ① 테두리 색(아군/적) ② 그 점이 있는 맵 구역 vs 그 선수의 포지션/역할 ③ 본인 = `=== 사용자 본인 ===`에 명시된 챔피언이며 화면 중앙 + 미니맵 흰 사각형 안의 점 — 이 셋을 조합해 추론하라. 확정 못 하면 이름을 지어내지 말고 "○○ 구역의 적/아군(누군지 불명)"으로 표현하라.
- **라인 퀘스트 기반 위치 추론:** 약 12분 이전까지 TOP·MID·BOT(원딜)은 자기 라인에 머무는 경향이 강하다. 게임 시간 ~12분 이전이면 TOP=탑, MID=미드, BOT(원딜)=봇, SUP=보통 봇으로 우선 매칭하라(JUNGLE 제외). 12분 이후엔 약하게 적용. 단, 라이너가 12분 이전에 라인을 벗어나 있으면 그 자체가 중요한 코칭 포인트다 — 반드시 짚어라.
- 챔피언 체력/마나 바, 레벨로 교전 가능 여부를 판단하라.
- 하단 HUD: 스킬 쿨다운, 소환사 주문(점멸 등), 아이템, 보유 골드.
- 상단/스코어: 양 팀 킬, 타워, 골드 차, 게임 시간. 화면 우측 킬/데스 로그.
- 화면의 게임 타이머로 이 장면이 정확히 몇 분 몇 초인지 특정해서 말하라.
- 함께 주어지는 `=== CV 자동 판독 ===`과 `=== 타임라인 정밀 데이터 ===`는 코드·Riot API가 추출한 사실이다. 이미지로 본 것과 충돌하면 이 구조화 데이터를 우선하라.
- CV가 '화질 낮음'·'게임 시각 판독 실패'로 표시한 항목은 이미지로 추측해 채우지 말고 "정보를 얻기 힘들다"고 명시하라.
- 타임라인은 분 단위 스냅샷이다. 클립 시각과 스냅샷 시각의 차이를 감안해서 읽어라.
- 결과론 금지: 결과를 보고 평가하지 말고, 이 장면에서 어떤 선택지가 보였는지로 본다."""

# USD per 1M tokens (claude pricing, 2026)
MODEL_PRICING = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-7": {"input": 5.0, "output": 25.0},
    "claude-opus-4-6": {"input": 5.0, "output": 25.0},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
}
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.1

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다. backend/.env 확인 필요."
            )
        _client = anthropic.Anthropic()
    return _client


def _image_block(path: Path) -> dict:
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": data,
        },
    }


# Anthropic 메시지 요청당 이미지 100장 한도. 프레임마다 [전체]+[미니맵]
# 2장이 실리므로 쌍 수를 40으로 제한해 최대 80장 안쪽으로 둔다.
MAX_FRAME_PAIRS = 40


def _select_frame_indices(
    frame_count: int, max_pairs: int = MAX_FRAME_PAIRS
) -> list[int]:
    """분석에 쓸 프레임 번호(1-기반)를 고른다. frame_count가 상한을
    넘으면 시간 순서를 유지한 채 고르게 솎아낸다."""
    if frame_count <= 0:
        return []
    if frame_count <= max_pairs:
        return list(range(1, frame_count + 1))
    step = frame_count / max_pairs
    return sorted({int(i * step) + 1 for i in range(max_pairs)})


def _load_frames_as_blocks(
    clip_dir: Path, frame_count: int
) -> tuple[list[dict], int, int]:
    """선택된 프레임을 [전체 화면] + (있으면) [미니맵 확대] 쌍으로 싣는다.
    프레임 수가 상한을 넘으면 시간순으로 균등 샘플링한다.
    반환: (blocks, 전체프레임 수, 미니맵 수)"""
    blocks: list[dict] = []
    n_full = 0
    n_mini = 0
    frames_dir = clip_dir / "frames"
    for i in _select_frame_indices(frame_count):
        frame_path = frames_dir / f"frame_{i:04d}.jpg"
        if not frame_path.exists():
            continue
        blocks.append({"type": "text", "text": f"[프레임 {i} · 전체 화면]"})
        blocks.append(_image_block(frame_path))
        n_full += 1

        mm_path = frames_dir / f"minimap_{i:04d}.jpg"
        if mm_path.exists():
            blocks.append(
                {"type": "text", "text": f"[프레임 {i} · 미니맵 확대]"}
            )
            blocks.append(_image_block(mm_path))
            n_mini += 1
    return blocks, n_full, n_mini


def _load_single_frame_blocks(
    clip_dir: Path, frame_number: int
) -> tuple[list[dict], int, int]:
    """사용자가 고른 단일 프레임을 [전체 화면] + (있으면) [미니맵 확대]로
    싣는다. 반환: (blocks, 전체프레임 수(0/1), 미니맵 수(0/1))."""
    frames_dir = clip_dir / "frames"
    frame_path = frames_dir / f"frame_{frame_number:04d}.jpg"
    if not frame_path.exists():
        raise FileNotFoundError(f"Frame {frame_number} not found")
    blocks: list[dict] = [
        {"type": "text", "text": "[선택 장면 · 전체 화면]"},
        _image_block(frame_path),
    ]
    n_mini = 0
    mm_path = frames_dir / f"minimap_{frame_number:04d}.jpg"
    if mm_path.exists():
        blocks.append({"type": "text", "text": "[선택 장면 · 미니맵 확대]"})
        blocks.append(_image_block(mm_path))
        n_mini = 1
    return blocks, 1, n_mini


def _frame_paths(
    clip_dir: Path, frame_number: int
) -> tuple[Path, Optional[Path]]:
    """단일 프레임의 (전체화면 경로, 미니맵 경로 또는 None)."""
    frames_dir = clip_dir / "frames"
    fp = frames_dir / f"frame_{frame_number:04d}.jpg"
    mp = frames_dir / f"minimap_{frame_number:04d}.jpg"
    return fp, (mp if mp.exists() else None)


def _note_category(source_file: str) -> str:
    """source_file 경로로 코치 노트 / 위키를 구분한다."""
    sf = (source_file or "").replace("\\", "/")
    if sf.startswith("general-lol-wiki/"):
        return "wiki"
    return "coach"  # knowledge/ 및 기타는 코치 노트로 취급


def _format_notes(notes: list[dict]) -> str:
    coach = [n for n in notes if _note_category(n.get("source_file", "")) == "coach"]
    wiki = [n for n in notes if _note_category(n.get("source_file", "")) == "wiki"]

    lines: list[str] = []

    lines.append("=== 코치 노트 (코칭 판단의 유일한 근거) ===")
    if coach:
        for i, note in enumerate(coach, 1):
            lines.append("")
            lines.append(
                f"[코치 노트 {i}] {note.get('source_file')} "
                f"(유사도 {note.get('similarity')})"
            )
            if note.get("tags"):
                lines.append(f"태그: {note['tags']}")
            lines.append(note.get("content", ""))
    else:
        lines.append("(관련 코치 노트 없음 — 코칭 판단을 만들지 말고 '더 정보 필요'로 표시)")

    if wiki:
        lines.append("")
        lines.append("=== 위키 (사실 확인 보조 전용, 코칭 판단 근거 아님) ===")
        for i, note in enumerate(wiki, 1):
            lines.append("")
            lines.append(
                f"[위키 {i}] {note.get('source_file')} "
                f"(유사도 {note.get('similarity')})"
            )
            if note.get("tags"):
                lines.append(f"태그: {note['tags']}")
            lines.append(note.get("content", ""))

    return "\n".join(lines)


def _format_good_examples(examples: list[dict]) -> str:
    """과거에 사용자가 좋게 평가한 분석을 프롬프트 참고 예시로 포맷."""
    lines = [
        "=== 과거에 사용자가 좋게 평가한 분석 "
        "(참고: 이 수준·접근 방식으로 분석하라) ==="
    ]
    for i, ex in enumerate(examples, 1):
        lines.append("")
        lines.append(f"[우수 예시 {i}] 질문: {ex.get('user_question', '')}")
        lines.append(f"분석: {ex.get('analysis_text', '')}")
    return "\n".join(lines)


_POSITION_ORDER = {"TOP": 0, "JUNGLE": 1, "MIDDLE": 2, "BOTTOM": 3, "UTILITY": 4}


def _norm_duration_seconds(raw_duration: Any) -> int:
    """gameDuration은 패치에 따라 초 또는 ms. 큰 값이면 ms로 보고 환산."""
    try:
        d = int(raw_duration or 0)
    except (TypeError, ValueError):
        return 0
    return d // 1000 if d > 10000 else d


def _objectives_line(team: dict) -> str:
    obj = team.get("objectives") or {}

    def _k(name: str) -> int:
        return int((obj.get(name) or {}).get("kills", 0) or 0)

    return (
        f"드래곤 {_k('dragon')} / 전령 {_k('riftHerald')} / 바론 {_k('baron')} / "
        f"타워 {_k('tower')} / 억제기 {_k('inhibitor')}"
    )


def _find_user_desc(data: dict, user_puuid: Optional[str]) -> Optional[str]:
    """매치 참가자 중 사용자 본인을 puuid로 찾아 '챔피언 (이름, 포지션)' 반환."""
    if not user_puuid:
        return None
    info = data.get("info") if isinstance(data.get("info"), dict) else data
    for p in info.get("participants") or []:
        if p.get("puuid") == user_puuid:
            name = (
                p.get("summonerName") or p.get("riotIdGameName") or "Unknown"
            )
            pos = p.get("teamPosition") or p.get("individualPosition") or "?"
            return f"{p.get('championName')} ({name}, {pos})"
    return None


def _summarize_match(data: dict, user_puuid: Optional[str] = None) -> str:
    info = data.get("info") if isinstance(data.get("info"), dict) else data
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    participants = info.get("participants") or []
    teams = info.get("teams") or []
    duration = _norm_duration_seconds(
        info.get("gameDuration") or data.get("gameDuration")
    )
    minutes = max(duration / 60.0, 1.0)
    queue_id = info.get("queueId") or data.get("queueId")
    match_id_val = metadata.get("matchId") or data.get("matchId")

    teams_by_id = {t.get("teamId"): t for t in teams}

    lines = [
        "=== 매치 컨텍스트 ===",
        f"매치 ID: {match_id_val}",
        f"게임 시간: {duration // 60}분 {duration % 60}초  |  큐 ID: {queue_id}",
        "",
        "(아래 수치는 게임 종료 시점 최종 누적값 — 클립 시점 상태와 다를 수 있음)",
    ]

    for team_id, team_label in ((100, "블루"), (200, "레드")):
        members = [p for p in participants if p.get("teamId") == team_id]
        if not members:
            continue
        members.sort(
            key=lambda p: _POSITION_ORDER.get(
                p.get("teamPosition") or p.get("individualPosition") or "", 9
            )
        )
        team = teams_by_id.get(team_id, {})
        won = team.get("win")
        if won is None and members:
            won = members[0].get("win")
        result = "승리" if won else "패배"
        lines.append("")
        lines.append(f"[{team_label}팀 — {result}] 오브젝트: {_objectives_line(team)}")

        for p in members:
            name = (
                p.get("summonerName")
                or p.get("riotIdGameName")
                or "Unknown"
            )
            pos = p.get("teamPosition") or p.get("individualPosition") or "?"
            kda = f"{p.get('kills', 0)}/{p.get('deaths', 0)}/{p.get('assists', 0)}"
            cs = int(p.get("totalMinionsKilled", 0) or 0) + int(
                p.get("neutralMinionsKilled", 0) or 0
            )
            cs_min = round(cs / minutes, 1)
            gold = int(p.get("goldEarned", 0) or 0)
            dmg = int(p.get("totalDamageDealtToChampions", 0) or 0)
            vision = p.get("visionScore")
            level = p.get("champLevel")
            lv = f" Lv{level}" if level is not None else ""
            detail = (
                f"  - [{pos}] {p.get('championName')} ({name}){lv} | "
                f"KDA {kda} | CS {cs}({cs_min}/분) | 골드 {gold} | "
                f"챔피언딜 {dmg}"
            )
            if vision is not None:
                detail += f" | 시야 {vision}"
            if user_puuid and p.get("puuid") == user_puuid:
                detail += "   ← 사용자 본인"
            lines.append(detail)

    return "\n".join(lines)


def _get_raw_match(match_id: str) -> Optional[dict]:
    """raw 매치를 캐시(match_raw:)에서 가져오거나 Riot에서 받아 캐시한다.
    refined 캐시(match:)는 필드가 빈약해 raw 전용 키를 쓴다."""
    try:
        cached = get_cached(f"match_raw:{match_id}")
        if cached is not None:
            return cached
        raw = RiotAPIClient().get_match_by_id(match_id)
        if raw is None:
            return None
        set_cached(f"match_raw:{match_id}", raw)
        return raw
    except (RuntimeError, RiotAPIError) as err:
        print(f"[Analyze] match fetch skipped: {err}")
        return None


def _try_load_timeline(match_id: str) -> Optional[dict]:
    """매치 타임라인을 캐시(timeline_raw:)에서 가져오거나 Riot에서 받는다."""
    try:
        cached = get_cached(f"timeline_raw:{match_id}")
        if cached is not None:
            return cached
        tl = RiotAPIClient().get_match_timeline(match_id)
        if tl is None:
            return None
        set_cached(f"timeline_raw:{match_id}", tl)
        return tl
    except (RuntimeError, RiotAPIError) as err:
        print(f"[Analyze] timeline fetch skipped: {err}")
        return None


# 소환사의 협곡 좌표 정규화 기준값 (참가자 좌표는 대략 0~15000)
MAP_SIZE = 15000


def _build_roster(raw_match: dict) -> dict:
    """participantId(1~10) -> {champ, name, pos, team, puuid}."""
    info = (
        raw_match.get("info")
        if isinstance(raw_match.get("info"), dict)
        else raw_match
    )
    roster: dict = {}
    for p in info.get("participants") or []:
        pid = p.get("participantId")
        if pid is None:
            continue
        roster[int(pid)] = {
            "champ": p.get("championName") or "?",
            "name": p.get("riotIdGameName") or p.get("summonerName") or "?",
            "pos": p.get("teamPosition") or p.get("individualPosition") or "?",
            "team": "블루" if p.get("teamId") == 100 else "레드",
            "puuid": p.get("puuid"),
        }
    return roster


def _region(nx: float, ny: float) -> str:
    """정규화 좌표(0~1, 좌하단 블루 기지 원점)를 대략적 구역명으로."""
    if nx < 0.16 and ny < 0.16:
        return "블루 기지"
    if nx > 0.84 and ny > 0.84:
        return "레드 기지"
    d = nx - ny  # >0 봇(우하)쪽, <0 탑(좌상)쪽
    if d > 0.2:
        return "봇 쪽"
    if d < -0.2:
        return "탑 쪽"
    return "미드 쪽"


_MONSTER_KO = {
    "DRAGON": "드래곤",
    "RIFTHERALD": "전령",
    "BARON_NASHOR": "바론",
    "HORDE": "공허 유충",
}


def _describe_event(ev: dict, roster: dict) -> Optional[str]:
    """타임라인 이벤트를 한 줄 한국어 설명으로. 관심 없는 타입은 None."""

    def champ(pid: Any) -> Optional[str]:
        if not pid:
            return None
        meta = roster.get(int(pid))
        return meta["champ"] if meta else None

    t = ev.get("type")
    if t == "CHAMPION_KILL":
        victim = champ(ev.get("victimId"))
        if not victim:
            return None
        killer = champ(ev.get("killerId")) or "처치자 불명"
        n_assist = len(ev.get("assistingParticipantIds") or [])
        s = f"킬 — {killer}이(가) {victim} 처치"
        if n_assist:
            s += f" (어시 {n_assist})"
        return s
    if t == "ELITE_MONSTER_KILL":
        name = _MONSTER_KO.get(
            ev.get("monsterType", ""), ev.get("monsterType", "몬스터")
        )
        killer = champ(ev.get("killerId"))
        return f"오브젝트 — {killer or '한 팀'} 측이 {name} 처치"
    if t == "BUILDING_KILL":
        bt = ev.get("buildingType", "")
        lane = ev.get("laneType", "").replace("_LANE", "").lower()
        lane_ko = {"top": "탑", "mid": "미드", "bot": "봇"}.get(lane, lane)
        name = "타워" if bt == "TOWER_BUILDING" else "억제기"
        return f"건물 — {lane_ko} {name} 파괴"
    return None


def _summarize_timeline_at(
    timeline: dict,
    raw_match: dict,
    game_secs: int,
    user_puuid: Optional[str],
) -> str:
    """클립 시각에 가장 가까운 타임라인 스냅샷 + 주변 이벤트를 구조화한다."""
    info = (
        timeline.get("info")
        if isinstance(timeline.get("info"), dict)
        else timeline
    )
    frames = info.get("frames") or []
    if not frames:
        return ""
    target_ms = game_secs * 1000
    snap = min(
        frames, key=lambda f: abs((f.get("timestamp") or 0) - target_ms)
    )
    roster = _build_roster(raw_match)
    pframes = snap.get("participantFrames") or {}
    snap_secs = (snap.get("timestamp") or 0) // 1000

    lines = [
        "=== 타임라인 정밀 데이터 (Riot 정답값) ===",
        f"클립 시각 약 {game_secs // 60}:{game_secs % 60:02d}, "
        f"가장 가까운 타임라인 스냅샷 {snap_secs // 60}:{snap_secs % 60:02d}.",
        "좌표·골드·레벨·CS는 Riot가 준 정답값 — 미니맵·화면 추측보다 우선하라.",
        "위치 = 맵 좌하단(블루) 원점 기준 정규화 %.",
        "",
    ]
    for pid_str, pf in sorted(pframes.items(), key=lambda kv: int(kv[0])):
        meta = roster.get(int(pid_str), {})
        pos = pf.get("position") or {}
        nx = min(max((pos.get("x") or 0) / MAP_SIZE, 0.0), 1.0)
        ny = min(max((pos.get("y") or 0) / MAP_SIZE, 0.0), 1.0)
        cs = int(pf.get("minionsKilled") or 0) + int(
            pf.get("jungleMinionsKilled") or 0
        )
        line = (
            f"  [{meta.get('team', '?')}·{meta.get('pos', '?')}] "
            f"{meta.get('champ', '?')} | 위치 "
            f"({nx * 100:.0f}%,{ny * 100:.0f}%) {_region(nx, ny)} | "
            f"Lv{pf.get('level', '?')} 골드{pf.get('totalGold', '?')} CS{cs}"
        )
        if user_puuid and meta.get("puuid") == user_puuid:
            line += "  ← 사용자 본인"
        lines.append(line)

    window_ms = 90 * 1000
    events: list[tuple[int, str]] = []
    for fr in frames:
        for ev in fr.get("events") or []:
            ts = ev.get("timestamp") or 0
            if abs(ts - target_ms) <= window_ms:
                desc = _describe_event(ev, roster)
                if desc:
                    events.append((ts, desc))
    if events:
        lines.append("")
        lines.append("[클립 전후 ±90초 주요 이벤트]")
        for ts, desc in sorted(events):
            s = ts // 1000
            lines.append(f"  {s // 60}:{s % 60:02d} — {desc}")
    return "\n".join(lines)


def _format_cv(
    cv: dict, game_secs: Optional[int], time_source: str
) -> str:
    """CV 전처리 결과를 프롬프트용 텍스트로."""
    lines = ["=== CV 자동 판독 (코드가 프레임에서 추출한 사실) ==="]
    if cv.get("frame_quality") == "low":
        lines.append(
            "프레임 화질: 낮음 — HUD 숫자·작은 아이콘 등 세부는 신뢰하지 말고 "
            "'정보를 얻기 힘들다'로 처리하라."
        )
    else:
        lines.append("프레임 화질: 양호")

    if game_secs is not None:
        src = {"frame": "프레임 타이머 OCR", "user": "사용자 입력"}.get(
            time_source, time_source
        )
        lines.append(
            f"게임 시각: {game_secs // 60}:{game_secs % 60:02d} ({src})"
        )
    else:
        lines.append(
            "게임 시각: 판독 실패 — 화면 타이머가 보이면 그것으로만 시각을 말하라."
        )

    mq = cv.get("minimap_quality")
    if mq == "low":
        lines.append(
            "미니맵 화질: 낮음 — 미니맵 세부는 '정보를 얻기 힘들다'로 처리하라."
        )
    elif mq == "ok":
        lines.append("미니맵 화질: 양호")
    return "\n".join(lines)


def _estimate_cost(usage: Any, model: str) -> float:
    pricing = MODEL_PRICING.get(model)
    if not pricing:
        return 0.0

    input_t = getattr(usage, "input_tokens", 0) or 0
    output_t = getattr(usage, "output_tokens", 0) or 0
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

    cost = (
        input_t * pricing["input"]
        + cache_creation * pricing["input"] * CACHE_WRITE_MULTIPLIER
        + cache_read * pricing["input"] * CACHE_READ_MULTIPLIER
        + output_t * pricing["output"]
    ) / 1_000_000
    return round(cost, 4)


# 동기 함수다. 내부에서 Claude/OpenAI/Riot를 모두 블로킹 호출하므로,
# FastAPI가 이 경로를 외부 스레드풀에서 실행하도록 호출부(api/analyze.py)도
# 동기 def로 둔다. async def로 두면 분석 동안 이벤트 루프 전체가 멈춘다.
def analyze_clip(
    clip_id: str,
    user_question: str,
    match_id: Optional[str] = None,
    puuid: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    frame_number: Optional[int] = None,
    game_time: Optional[str] = None,
) -> dict:
    clip_dir = CLIPS_DIR / clip_id
    metadata_path = clip_dir / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Clip {clip_id} not found")

    clip_meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    frame_count = int(clip_meta.get("frame_count") or 0)

    # 단일 프레임 모드(현재 기본): 사용자가 고른 1장만 분석한다.
    # frame_number 없이 호출하면 멀티프레임 모드로 동작한다(기능 보존).
    cv_facts: Optional[dict] = None
    game_secs: Optional[int] = None
    time_source = "none"
    if frame_number is not None:
        frame_blocks, n_full, n_mini = _load_single_frame_blocks(
            clip_dir, frame_number
        )
        screen_guide = SCREEN_READING_GUIDE_SINGLE
        # CV 전처리: 화질 판정 + 타이머 OCR + 미니맵 점 검출
        fp, mp = _frame_paths(clip_dir, frame_number)
        override = (
            cv_processor.parse_game_time(game_time) if game_time else None
        )
        try:
            cv_facts = cv_processor.analyze_frame(
                fp, mp, read_timer=(override is None)
            )
        except Exception as err:
            print(f"[Analyze] CV preprocess failed: {err}")
        if override is not None:
            game_secs, time_source = override, "user"
        elif cv_facts and cv_facts.get("game_time_seconds") is not None:
            game_secs, time_source = cv_facts["game_time_seconds"], "frame"
    else:
        frame_blocks, n_full, n_mini = _load_frames_as_blocks(
            clip_dir, frame_count
        )
        screen_guide = SCREEN_READING_GUIDE

    notes = search_knowledge(user_question, top_k=5)
    raw_match = _get_raw_match(match_id) if match_id else None
    match_summary = _summarize_match(raw_match, puuid) if raw_match else None
    user_desc = _find_user_desc(raw_match, puuid) if raw_match else None

    # 타임라인 정밀 데이터: 게임 시각을 알고 매치가 있을 때만
    timeline_block: Optional[str] = None
    if raw_match and game_secs is not None and match_id:
        tl = _try_load_timeline(match_id)
        if tl:
            timeline_block = _summarize_timeline_at(
                tl, raw_match, game_secs, puuid
            )

    # 과거에 좋게 평가된 분석을 질문 유사도로 검색해 예시로 주입(in-context 학습)
    good_examples = search_good_analyses(user_question, top_k=2)

    user_content: list[dict] = []
    if notes:
        user_content.append(
            {
                "type": "text",
                "text": _format_notes(notes),
                "cache_control": {"type": "ephemeral"},
            }
        )
    if match_summary:
        user_content.append(
            {
                "type": "text",
                "text": match_summary,
                "cache_control": {"type": "ephemeral"},
            }
        )
    if timeline_block:
        user_content.append({"type": "text", "text": timeline_block})
    if cv_facts is not None:
        user_content.append(
            {
                "type": "text",
                "text": _format_cv(cv_facts, game_secs, time_source),
            }
        )
    if good_examples:
        user_content.append(
            {"type": "text", "text": _format_good_examples(good_examples)}
        )
    if user_desc:
        user_content.append(
            {
                "type": "text",
                "text": (
                    "=== 사용자 본인 ===\n"
                    f"이 클립은 **{user_desc}** 플레이어 본인의 화면이다. "
                    "클립 화면 중앙에서 조작되는 챔피언이 이 플레이어이며, "
                    "질문의 '나/내/너/네 선택·포지셔닝·동선'은 전부 이 "
                    f"챔피언({user_desc}) 기준으로만 분석하라. 매치 컨텍스트에서 "
                    "'← 사용자 본인'으로 표시된 행이 이 플레이어다. 다른 "
                    "챔피언을 사용자 본인으로 착각하지 마라."
                ),
            }
        )
    user_content.extend(frame_blocks)
    user_content.append(
        {"type": "text", "text": f"=== 질문 ===\n{user_question}"}
    )

    client = _get_client()
    response = client.messages.create(
        model=model,
        max_tokens=DEFAULT_MAX_TOKENS,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT},
            {"type": "text", "text": RIOT_DATA_GUIDE},
            {
                "type": "text",
                "text": screen_guide,
                # 마지막 system 블록에 캐시 브레이크포인트 → system 전체가 캐시됨
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[
            *FEW_SHOT_EXAMPLES,
            {"role": "user", "content": user_content},
        ],
    )

    analysis_text = next(
        (block.text for block in response.content if block.type == "text"),
        "",
    )

    cost = _estimate_cost(response.usage, model)

    metadata_out = {
        "frames_analyzed": n_full,
        "minimaps_analyzed": n_mini,
        "frame_number": frame_number,
        "notes_referenced": len(notes),
        "good_examples_used": len(good_examples),
        "match_id_used": match_id if match_summary else None,
        "game_time": cv_processor.fmt_secs(game_secs),
        "game_time_source": time_source,
        "frame_quality": cv_facts.get("frame_quality") if cv_facts else None,
        "minimap_quality": (
            cv_facts.get("minimap_quality") if cv_facts else None
        ),
        "timeline_used": timeline_block is not None,
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation_tokens": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        "estimated_cost_usd": cost,
        "stop_reason": response.stop_reason,
    }

    # 분석 결과를 히스토리에 저장. 저장 실패가 분석 응답을 막지 않도록 격리.
    analysis_id = None
    try:
        analysis_id = save_analysis(
            clip_id=clip_id,
            frame_number=frame_number,
            match_id=match_id if match_summary else None,
            puuid=puuid,
            model=model,
            user_question=user_question,
            analysis_text=analysis_text,
            metadata=metadata_out,
            notes=notes,
            examples_used=[ex["id"] for ex in good_examples],
        )
    except Exception as err:
        print(f"[Analyze] save_analysis failed: {err}")

    mode = f"frame#{frame_number}" if frame_number is not None else "multi"
    gt = cv_processor.fmt_secs(game_secs) or "?"
    print(
        f"[Analyze] clip={clip_id[:8]} mode={mode} t={gt} "
        f"timeline={'Y' if timeline_block else 'N'} frames={n_full} "
        f"minimap={n_mini} notes={len(notes)} examples={len(good_examples)} "
        f"id={analysis_id} model={model} cost=${cost}"
    )

    return {
        "analysis": analysis_text,
        "analysis_id": analysis_id,
        "metadata": metadata_out,
    }
