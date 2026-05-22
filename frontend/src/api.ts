// 백엔드(FastAPI) 호출 래퍼. CORS는 backend/main.py에서 localhost:5173 허용.
// 127.0.0.1 고정: Windows에서 localhost가 IPv6(::1)로 풀려 IPv4 백엔드에
// 안 닿는 문제 방지. 백엔드는 127.0.0.1:8000(IPv4)에 바인딩됨.
const BACKEND_URL = 'http://127.0.0.1:8000'

export type Summoner = {
  puuid: string
  game_name: string
  tag_line: string
}

export type Participant = {
  puuid: string | null
  championName: string | null
  summonerName: string | null
  teamId: number | null
  kills: number | null
  deaths: number | null
  assists: number | null
  items: (number | null)[]
  win: boolean | null
}

export type MatchDetail = {
  matchId: string | null
  gameDuration: number | null
  queueId: number | null
  gameCreation: number | null
  participants: Participant[]
}

export type ClipMeta = {
  clip_id: string
  original_filename: string
  extension: string
  frame_count: number
  minimap_count: number
  duration_seconds: number
  fps_interval: number
  width: number | null
  height: number | null
  original_kept: boolean
}

export type AnalyzeResult = {
  analysis: string
  metadata: {
    frames_analyzed: number
    minimaps_analyzed: number
    frame_number: number | null
    notes_referenced: number
    match_id_used: string | null
    model: string
    input_tokens: number
    output_tokens: number
    cache_read_tokens: number
    cache_creation_tokens: number
    estimated_cost_usd: number
    stop_reason: string | null
  }
}

async function unwrap<T>(res: Response): Promise<T> {
  if (res.ok) {
    return (await res.json()) as T
  }
  let detail = `HTTP ${res.status}`
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body.detail === 'string' && body.detail) {
      detail = body.detail
    }
  } catch {
    // JSON 본문이 아니면 기본 메시지 유지
  }
  throw new Error(detail)
}

export async function getSummoner(
  gameName: string,
  tagLine: string,
): Promise<Summoner> {
  const gn = encodeURIComponent(gameName)
  const tl = encodeURIComponent(tagLine)
  const res = await fetch(`${BACKEND_URL}/api/summoner/${gn}/${tl}`)
  return unwrap<Summoner>(res)
}

export async function getMatchIds(
  puuid: string,
  count = 20,
): Promise<{ match_ids: string[]; queue: number | null }> {
  const res = await fetch(
    `${BACKEND_URL}/api/matches/${encodeURIComponent(puuid)}?count=${count}`,
  )
  return unwrap<{ match_ids: string[]; queue: number | null }>(res)
}

export async function getMatch(matchId: string): Promise<MatchDetail> {
  const res = await fetch(
    `${BACKEND_URL}/api/match/${encodeURIComponent(matchId)}`,
  )
  return unwrap<MatchDetail>(res)
}

export async function uploadClip(
  file: File,
  fpsInterval: number,
): Promise<ClipMeta> {
  const form = new FormData()
  form.append('video', file)
  form.append('fps_interval', String(fpsInterval))
  const res = await fetch(`${BACKEND_URL}/api/clip/upload`, {
    method: 'POST',
    body: form,
  })
  return unwrap<ClipMeta>(res)
}

// 추출된 단일 프레임 이미지 URL (프레임 선택 썸네일용).
export function frameUrl(clipId: string, frameNumber: number): string {
  return `${BACKEND_URL}/api/clip/${encodeURIComponent(
    clipId,
  )}/frames/${frameNumber}`
}

export async function analyze(
  clipId: string,
  userQuestion: string,
  matchId: string | null,
  puuid: string | null,
  frameNumber: number,
  model?: string,
): Promise<AnalyzeResult> {
  const res = await fetch(`${BACKEND_URL}/api/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      clip_id: clipId,
      user_question: userQuestion,
      match_id: matchId,
      puuid,
      frame_number: frameNumber,
      ...(model ? { model } : {}),
    }),
  })
  return unwrap<AnalyzeResult>(res)
}

// 동시 요청 수를 제한해 매치 상세를 일괄 조회 (Riot 레이트리밋 보호).
export async function mapLimit<T, R>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length)
  let cursor = 0
  async function worker(): Promise<void> {
    while (cursor < items.length) {
      const idx = cursor++
      results[idx] = await fn(items[idx])
    }
  }
  const pool = Array.from({ length: Math.min(limit, items.length) }, worker)
  await Promise.all(pool)
  return results
}

// gameDuration은 패치에 따라 초 또는 ms. 큰 값이면 ms로 보고 환산.
export function durationSeconds(raw: number | null): number {
  const d = raw ?? 0
  return d > 10000 ? Math.floor(d / 1000) : d
}
