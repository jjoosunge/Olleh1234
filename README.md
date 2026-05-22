# LoL Coach MVP

리그 오브 레전드 매치를 분석해 코칭을 제공하는 AI 서비스의 MVP입니다.
FastAPI 백엔드 + React 프론트엔드 모노레포 구조이며, Riot 매치 데이터·타임라인 · 클립 프레임(FFmpeg) · CV 전처리(타이머 OCR·화질 판정) · 코칭 노트(RAG) · 위키 지식을 결합해 Claude로 코칭 답변을 생성합니다.

> **기본값**: 모델 `claude-sonnet-4-6`, 프레임 간격 3초, 단일 프레임 분석(사용자가 고른 장면 1장), CV 전처리 + Riot 타임라인 정밀 데이터 주입, prompt caching 적용

## 사용자 워크플로우

1. **소환사 검색** — Riot ID(`닉네임#태그`) 입력
2. **솔로랭크 최근 20게임** — `queue=420` 필터로 솔로랭크만 노출
3. **경기 선택 → 상세** — 양 팀 참가자·결과 표시
4. **클립 업로드 & 장면 선택** — 1~3초당 1프레임 추출(FFmpeg) 후, 분석할 장면 1장을 선택. 선택 시 CV가 게임 시각(타이머 OCR)·화질을 자동 감지하며 게임 시각은 수정 가능
5. **질문 → 코칭 답변** — 고정 프롬프트 + 선택 장면(전체 화면 + 미니맵 확대) + CV 자동 판독 + 타임라인 정밀 데이터(그 시점 10명 좌표·골드·레벨) + 매치 데이터 + 코치 노트/위키(RAG) + 질문을 Claude에 전달

프론트엔드(`http://localhost:5173`)에서 위 5단계가 하나의 화면 흐름으로 구현돼 있습니다.

## 폴더 구조

```
.
├── backend/          # FastAPI + SQLite(sqlite-vec)
├── frontend/         # React + Vite + TypeScript (5단계 워크플로우 UI)
├── knowledge/        # 마크다운 코치 노트 (코칭 판단의 근거, RAG 인덱싱됨)
├── general-lol-wiki/ # 일반 LoL 위키 (사실 확인 보조, RAG 인덱싱됨)
├── wiki-vault/       # Obsidian 볼트 (RAG 미인덱싱, read-only)
└── db/               # SQLite DB 파일 위치 (knowledge.db)
```

## 백엔드 실행

```powershell
cd backend
# 최초 1회: venv 생성 및 의존성 설치
# (이미 backend/venv 가 있고 의존성이 설치돼 있으면 이 3줄은 건너뛰기)
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 환경 변수 설정 (.env.example 복사 후 키 입력)
copy .env.example .env

# 서버 실행 (http://localhost:8000)
.\venv\Scripts\Activate.ps1
uvicorn main:app --reload --port 8000
```

- 헬스 체크: `GET http://localhost:8000/health`
- API 문서(Swagger UI): `http://localhost:8000/docs`

## 프론트엔드 실행

```powershell
cd frontend
# 최초 1회
npm install

# 개발 서버 실행 (http://localhost:5173)
npm run dev
```

브라우저에서 `http://localhost:5173`을 열면 "올레 · LoL 코칭" 워크플로우 화면이 나옵니다.
소환사 검색 → 솔로랭크 매치 리스트 → 경기 상세 → 클립 업로드 → 질문/답변까지
한 페이지에서 진행됩니다. 백엔드(`http://localhost:8000`)가 먼저 떠 있어야 합니다.

## 필요한 환경 변수

`backend/.env`에 다음 키를 채워주세요.

| 키 | 용도 |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude (claude-sonnet-4-6) 호출 |
| `OPENAI_API_KEY` | 임베딩 생성 (text-embedding-3-small) |
| `RIOT_API_KEY` | Riot Games API |

## 노트 인덱싱

`knowledge/`(코치 노트) + `general-lol-wiki/`(일반 위키) 폴더의 마크다운 파일을
임베딩(`text-embedding-3-small`, 1536차원)해서 SQLite(`db/knowledge.db`)에 저장합니다.
두 폴더의 `README.md`는 인덱싱에서 제외됩니다. `wiki-vault/`는 인덱싱 대상이 아닙니다.
노트/위키를 추가하거나 수정한 뒤에는 항상 재인덱싱이 필요합니다.

```powershell
cd backend
.\venv\Scripts\Activate.ps1
python -m app.scripts.reindex
```

진행 상황은 `Indexing sample-mid-roaming.md... 1 chunks created` 형태로 출력됩니다.
실행 전 `backend/.env`에 `OPENAI_API_KEY`가 설정돼 있어야 합니다.

### 청크 분할 규칙

- `## ` 헤더 기준으로 1차 분할
- 한 섹션이 1500자 초과 시 800자 단위로 추가 분할
- 100자 미만 조각은 버림
- 청크 안에 `[태그] ...` 라인이 있으면 `tags` 컬럼에 저장됨

## Riot API 키 설정

1. https://developer.riotgames.com 접속 후 본인 Riot 계정으로 로그인
2. 메인 페이지 중간의 **DEVELOPMENT API KEY** 값을 복사 (만료 시각도 함께 표시됨)
3. `backend/.env`의 `RIOT_API_KEY=` 뒤에 붙여넣기
4. 백엔드가 `--reload`로 떠 있으면 자동 반영됨

> **24시간 만료 주의**: 개발용 키는 발급 후 24시간이 지나면 만료됩니다.
> 만료되면 응답으로 `HTTP 401 "Riot API key expired or invalid"`이 옵니다.
> https://developer.riotgames.com 에서 다시 발급받아 `.env`만 갱신하면 됩니다.

기본 지역 설정: regional = `asia` (Riot ID / match-v5), platform = `kr` (소환사용).
다른 지역으로 바꾸려면 `RiotAPIClient(regional=..., platform=...)`로 인스턴스 생성.

### Riot API 엔드포인트

| 메서드 / 경로 | 설명 | 캐시 |
| --- | --- | --- |
| `GET /api/summoner/{game_name}/{tag_line}` | Riot ID → PUUID | 없음 |
| `GET /api/matches/{puuid}?count=20&queue=420` | 최근 매치 ID 리스트 (`queue` 기본 420=솔로랭크, `queue=0`이면 전체 큐) | 없음 |
| `GET /api/match/{match_id}` | 매치 상세 (정제됨) | 24h |
| `GET /api/match/{match_id}/timeline` | 매치 타임라인 원본 | 24h |

**브라우저에서 바로 테스트** (URL 인코딩 주의 — 공백은 `%20`):

```
http://localhost:8000/api/summoner/Hide%20on%20bush/KR1
http://localhost:8000/api/matches/{받은-puuid}?count=5
http://localhost:8000/api/match/KR_7000000000
http://localhost:8000/api/match/KR_7000000000/timeline
```

`/docs` (Swagger UI)에서 `riot` 태그 아래 4개 엔드포인트를 직접 호출 가능.

### Riot 응답 에러 코드 매핑

| 원본(Riot) | 우리 응답 | 메시지 |
| --- | --- | --- |
| 403, 401 | 401 | Riot API key expired or invalid |
| 404 | 404 | Summoner not found / Match not found |
| 429 | 503 | Rate limit exceeded, please retry |

## 검색 API 테스트

백엔드 실행 후 `POST /api/search`로 노트를 검색합니다.

**PowerShell (Invoke-RestMethod)**

```powershell
$body = @{ query = "갱 가는 타이밍"; top_k = 3 } | ConvertTo-Json
Invoke-RestMethod -Uri http://localhost:8000/api/search `
  -Method Post -ContentType "application/json" -Body $body
```

**curl (Git Bash 등)**

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "갱 가는 타이밍", "top_k": 3}'
```

**Swagger UI**: `http://localhost:8000/docs`에서 `/api/search`를 직접 호출할 수 있습니다.

응답 형식:

```json
{
  "results": [
    {
      "source_file": "knowledge/sample-mid-roaming.md",
      "content": "...",
      "tags": "position:mid, phase:laning, topic:macro",
      "similarity": 0.82
    }
  ]
}
```

## 클립 업로드 테스트

### 사전 준비

**FFmpeg 설치 필수.** `ffmpeg`와 `ffprobe`가 PATH에 있어야 합니다.

Windows에서 winget으로 설치:

```powershell
winget install Gyan.FFmpeg
```

설치 확인:

```powershell
ffmpeg -version
ffprobe -version
```

### 업로드 흐름

| 메서드 / 경로 | 설명 |
| --- | --- |
| `POST /api/clip/upload` | multipart 영상 업로드 → 프레임 추출 |
| `GET /api/clip/{clip_id}` | metadata.json 반환 |
| `GET /api/clip/{clip_id}/frames/{frame_number}` | 단일 프레임 이미지 (1부터) |
| `GET /api/clip/{clip_id}/frames/{frame_number}/cv` | 프레임 CV 전처리 (화질·게임 시각·미니맵 점) |
| `DELETE /api/clip/{clip_id}` | 클립 폴더 전체 삭제 |

- 지원 확장자: `.mp4`, `.mov`, `.avi`, `.mkv`, `.webm`
- 최대 파일 크기: **200MB** (초과 시 HTTP 413)
- `fps_interval`: **1초 / 2초 / 3초 중 선택** (기본 3초). 짧은 클립일수록 작은 값, 매크로 분석엔 3초 추천.
- 저장 위치: `backend/uploads/clips/{clip_id}/frames/frame_0001.jpg ...`
- 기본은 원본 영상 파일 삭제(프레임만 보관). 원본 유지하려면 `keep_original=true`.

### PowerShell 업로드 예시

```powershell
$form = @{
    video        = Get-Item "C:\path\to\your_clip.mp4"
    fps_interval = "3"
}
$resp = Invoke-RestMethod -Uri http://localhost:8000/api/clip/upload `
    -Method Post -Form $form
$resp

# 결과: { clip_id = "..."; frame_count = 30; duration_seconds = 30.5; ... }

# 프레임 1번 가져오기
Invoke-WebRequest -Uri "http://localhost:8000/api/clip/$($resp.clip_id)/frames/1" `
    -OutFile frame1.jpg

# 삭제
Invoke-RestMethod -Uri "http://localhost:8000/api/clip/$($resp.clip_id)" -Method Delete
```

### curl 업로드 예시

```bash
curl -X POST http://localhost:8000/api/clip/upload \
  -F "video=@your_clip.mp4" \
  -F "fps_interval=3"
```

`/docs` (Swagger UI)에서 `clip` 태그 아래 엔드포인트를 폼 UI로 직접 호출할 수도 있습니다.

## AI 분석 테스트

업로드된 클립 프레임 + CV 자동 판독 + RAG(코치 노트/위키) + (선택)매치 데이터·타임라인을 Claude에 보내 코칭 답변을 받습니다.

**프롬프트 구성** (`app/services/analyzer.py`):

- `system` = 3블록 고정 프롬프트 — ① '올레' 페르소나·답변 원칙, ② 라이엇 데이터 해석 가이드, ③ 인게임 화면 이해 가이드 (마지막 블록에 `cache_control` → system 전체 캐시)
- few-shot 예시 2쌍(조건부 분기·노트 프레임 사용 스타일)
- user = `[코치 노트]`/`[위키]` 출처 분리 RAG 결과 + 매치 컨텍스트 + **타임라인 정밀 데이터**(게임 시각 기준 10명 좌표·골드·레벨·CS + 전후 ±90초 이벤트) + **CV 자동 판독**(화질·게임 시각·미니맵 점) + 프레임 이미지(단일 프레임: 선택 장면 1쌍 / 멀티프레임: 최대 40쌍) + 질문
- **위키 정책**: 코칭 판단·프레임워크는 `[코치 노트]`만 근거로 사용, `[위키]`는 챔피언/스킬/아이템/용어 같은 사실 확인 보조로만 사용

**전제 조건**

- `backend/.env`의 `ANTHROPIC_API_KEY` 설정
- 클립 한 개 이상 업로드 완료 (`/api/clip/upload`)
- 코칭 노트 인덱싱 완료 (`python -m app.scripts.reindex`)
- 매치 컨텍스트·타임라인 정밀 데이터를 쓸 거면 `RIOT_API_KEY`도 설정 (타임라인은 게임 시각이 있어야 정렬됨 — CV 자동 OCR 또는 직접 입력)

### 요청

`POST /api/analyze`

```json
{
  "clip_id": "8b3a8e21-...",
  "user_question": "내가 이때 갱 간 게 맞아? 라인 푸시 상태 보고 판단해줘.",
  "match_id": "KR_8214989964",
  "puuid": "사용자-본인-puuid",
  "frame_number": 7,
  "game_time": "8:32",
  "model": "claude-sonnet-4-6"
}
```

- `clip_id` (필수, UUID): 업로드 응답에 받은 ID
- `user_question` (필수, 최대 2000자)
- `match_id` (선택): 있으면 Riot 매치 상세를 컨텍스트로 첨부
- `puuid` (선택): 사용자 본인의 puuid. `match_id`와 함께 주면 매치에서 본인 챔피언을 식별해 "이 챔피언 POV로 분석" 지시가 주입됨(챔피언 오인 방지)
- `frame_number` (선택, ≥1): 사용자가 고른 단일 프레임 번호. 주면 그 1장만(전체 화면 + 미니맵 확대) 보는 **단일 프레임 모드**로 동작. 생략하면 클립 전체 프레임을 보는 멀티프레임 모드(기능 보존)
- `game_time` (선택, `"MM:SS"`): 클립 장면의 게임 시각. 주면 그 값으로 Riot 타임라인을 정렬해 정밀 데이터를 주입. 생략하면 CV가 프레임 타이머를 OCR해 자동 판독
- `model` (선택, 기본 `claude-sonnet-4-6`)

### 응답

```json
{
  "analysis": "결론: ...\n근거: ...\n교정: ...",
  "analysis_id": 12,
  "metadata": {
    "frames_analyzed": 1,
    "minimaps_analyzed": 1,
    "frame_number": 7,
    "notes_referenced": 3,
    "good_examples_used": 0,
    "match_id_used": "KR_8214989964",
    "game_time": "8:32",
    "game_time_source": "frame",
    "frame_quality": "ok",
    "minimap_quality": "ok",
    "timeline_used": true,
    "model": "claude-sonnet-4-6",
    "input_tokens": 12480,
    "output_tokens": 760,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "estimated_cost_usd": 0.0488,
    "stop_reason": "end_turn"
  }
}
```

`match_id`를 보냈는데 Riot 호출이 실패하면 `match_id_used`가 `null`로 반환되고, 분석은 매치 없이 진행됩니다 (콘솔에 `[Analyze] match fetch skipped: ...` 로그).

### PowerShell 호출 예시

```powershell
$body = @{
    clip_id       = "여기-clip-id"
    user_question = "이 장면에서 내 포지셔닝·갱 호응 판단 봐줘."
    match_id      = "KR_8214989964"
    frame_number  = 7
    game_time     = "8:32"
} | ConvertTo-Json

Invoke-RestMethod -Uri http://localhost:8000/api/analyze `
    -Method Post -ContentType "application/json" -Body $body
```

### 비용 안내 (Claude Sonnet 4.6 기준)

| 항목 | 단가 (per 1M tokens) |
| --- | --- |
| 입력 (이미지 포함) | $3.00 |
| 출력 | $15.00 |
| 캐시 쓰기 | $3.75 (1.25× 입력) |
| 캐시 읽기 | $0.30 (0.1× 입력) |

**분석 1건 예상 비용 (단일 프레임 모드 — 현재 기본)**: 선택 장면 1장(전체 화면 + 미니맵 = 이미지 2장) + 노트 5개 + 매치 컨텍스트 기준
- 입력 ≈ 12K tokens × $3 = **$0.036**
- 출력 ≈ 1K tokens × $15 = **$0.015**
- **합계 ≈ $0.04~$0.06** (system·few-shot 캐시 적중 시 더 낮음)

> 멀티프레임 모드(`frame_number` 생략)는 클립 전체를 보내 이미지 수가 프레임 수의
> 2배까지 늘어난다. 30초 클립(이미지 약 60장) 기준 1건당 $0.2~0.3 수준.

> 시스템 프롬프트 3블록(페르소나 + 데이터 가이드 + 화면 가이드)과 few-shot 예시까지
> 하나의 정적 prefix로 묶여 prompt caching이 적용됩니다. 첫 호출에서 이 prefix가
> `cache_creation`으로 기록되고, 이후 동일 system/few-shot 재요청은 `cache_read`(입력
> 단가의 0.1×)로 처리돼 반복 분석 비용이 크게 떨어집니다.
> (프레임·매치·노트는 매번 달라 캐시 대상 아님)

### 콘솔 로그

각 분석 호출마다 백엔드 로그에 한 줄 요약이 찍힙니다:

```
[Analyze] clip=8b3a8e21 mode=frame#7 t=8:32 timeline=Y frames=1 minimap=1 notes=3 examples=0 id=12 model=claude-sonnet-4-6 cost=$0.0488
```

## 분석 히스토리 · 평가 · 피드백 학습

모든 `/api/analyze` 결과는 SQLite(`db/knowledge.db`의 `analyses` 테이블)에
영구 저장됩니다. 분석마다 **'장면·미니맵 판독'**과 **'코칭'** 두 축을 각각
👍/👎로 평가할 수 있고, 두 축 모두 👍를 받은 분석은 질문 임베딩이
`vec_analyses`(sqlite-vec)에 적재돼 — 이후 분석에서 새 질문과 의미가 비슷한
과거 우수 분석이 검색돼 프롬프트에 '우수 예시'로 주입됩니다(in-context 학습).

> Claude 모델 자체를 재학습하는 게 아니라, 좋게 평가된 과거 분석을 다음
> 분석의 맥락 예시로 다시 넣어주는 방식입니다. 평가가 쌓일수록 예시가 좋아집니다.

### 엔드포인트

| 메서드 / 경로 | 설명 |
| --- | --- |
| `GET /api/analyses?limit=50&offset=0` | 분석 히스토리 목록 (최신순) |
| `GET /api/analyses/{id}` | 분석 1건 상세 (코치 노트·주입 예시 포함) |
| `POST /api/analyses/{id}/rating` | 평가 등록/수정 |
| `DELETE /api/analyses/{id}` | 분석 기록 삭제 |

평가 요청 본문 — `reading`/`coaching` 각각 `"up"` / `"down"` / `null`(미평가):

```json
{ "reading": "up", "coaching": "up" }
```

두 값 모두 `"up"`이면 그 분석이 학습 풀(`vec_analyses`)에 편입되고, 이후
어느 한쪽이라도 풀리면 제외됩니다. 프론트는 두 축의 최종 상태를 항상 함께 보냅니다.

### 프론트엔드

분석 결과 화면과 페이지 하단 "분석 히스토리" 패널에서 평가할 수 있습니다.
히스토리 항목을 펼치면 분석 전문 확인·평가·삭제가 가능합니다.

## 의존 도구

- Python 3.11+
- Node.js 18+
- FFmpeg (`ffmpeg`, `ffprobe` 둘 다 PATH에 있어야 함)
