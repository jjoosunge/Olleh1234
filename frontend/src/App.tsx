import { useState } from 'react'
import type { FormEvent } from 'react'
import './App.css'
import {
  analyze,
  durationSeconds,
  frameUrl,
  getMatch,
  getMatchIds,
  getSummoner,
  mapLimit,
  uploadClip,
} from './api'
import type {
  AnalyzeResult,
  ClipMeta,
  MatchDetail,
  Participant,
  Summoner,
} from './api'

const MODELS = [
  'claude-sonnet-4-6',
  'claude-opus-4-7',
  'claude-haiku-4-5',
] as const

const FPS_CHOICES = [1, 2, 3] as const

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

function fmtDuration(raw: number | null): string {
  const s = durationSeconds(raw)
  return `${Math.floor(s / 60)}분 ${s % 60}초`
}

function fmtDate(ms: number | null): string {
  if (!ms) return ''
  return new Date(ms).toLocaleString('ko-KR')
}

function findMe(m: MatchDetail, puuid: string): Participant | undefined {
  return m.participants.find((p) => p.puuid === puuid)
}

function App() {
  // 1단계: 소환사
  const [riotId, setRiotId] = useState('')
  const [summoner, setSummoner] = useState<Summoner | null>(null)
  const [loadingSummoner, setLoadingSummoner] = useState(false)

  // 2단계: 매치 리스트
  const [matches, setMatches] = useState<MatchDetail[]>([])
  const [loadingMatches, setLoadingMatches] = useState(false)

  // 3단계: 선택된 매치
  const [selectedMatchId, setSelectedMatchId] = useState<string | null>(null)

  // 4단계: 클립
  const [file, setFile] = useState<File | null>(null)
  const [fpsInterval, setFpsInterval] = useState<number>(3)
  const [clip, setClip] = useState<ClipMeta | null>(null)
  const [uploading, setUploading] = useState(false)

  // 4-1단계: 분석할 단일 프레임 선택
  const [selectedFrame, setSelectedFrame] = useState<number | null>(null)

  // 5단계: 질문/분석
  const [question, setQuestion] = useState('')
  const [model, setModel] = useState<string>(MODELS[0])
  const [analyzing, setAnalyzing] = useState(false)
  const [result, setResult] = useState<AnalyzeResult | null>(null)

  const [error, setError] = useState<string | null>(null)

  const selectedMatch = matches.find((m) => m.matchId === selectedMatchId) ?? null

  async function handleSearch(e: FormEvent) {
    e.preventDefault()
    setError(null)
    const hash = riotId.lastIndexOf('#')
    if (hash < 1 || hash === riotId.length - 1) {
      setError('Riot ID 형식이 올바르지 않습니다. 예: 닉네임#KR1')
      return
    }
    const gameName = riotId.slice(0, hash).trim()
    const tagLine = riotId.slice(hash + 1).trim()

    setLoadingSummoner(true)
    setSummoner(null)
    setMatches([])
    setSelectedMatchId(null)
    setClip(null)
    setSelectedFrame(null)
    setResult(null)
    try {
      const s = await getSummoner(gameName, tagLine)
      setSummoner(s)
      await loadMatches(s)
    } catch (e) {
      setError(errMsg(e))
    } finally {
      setLoadingSummoner(false)
    }
  }

  async function loadMatches(s: Summoner) {
    setLoadingMatches(true)
    try {
      const { match_ids } = await getMatchIds(s.puuid, 20)
      const details = await mapLimit(match_ids, 4, async (id) => {
        try {
          return await getMatch(id)
        } catch {
          return null
        }
      })
      setMatches(details.filter((d): d is MatchDetail => d !== null))
    } catch (e) {
      setError(errMsg(e))
    } finally {
      setLoadingMatches(false)
    }
  }

  async function handleUpload() {
    if (!file) return
    setError(null)
    setUploading(true)
    setClip(null)
    setSelectedFrame(null)
    setResult(null)
    try {
      const meta = await uploadClip(file, fpsInterval)
      setClip(meta)
    } catch (e) {
      setError(errMsg(e))
    } finally {
      setUploading(false)
    }
  }

  async function handleAnalyze(e: FormEvent) {
    e.preventDefault()
    if (!clip || !selectedFrame || !question.trim()) return
    setError(null)
    setAnalyzing(true)
    setResult(null)
    try {
      const r = await analyze(
        clip.clip_id,
        question.trim(),
        selectedMatchId,
        summoner?.puuid ?? null,
        selectedFrame,
        model,
      )
      setResult(r)
    } catch (e) {
      setError(errMsg(e))
    } finally {
      setAnalyzing(false)
    }
  }

  function reset() {
    setRiotId('')
    setSummoner(null)
    setMatches([])
    setSelectedMatchId(null)
    setFile(null)
    setClip(null)
    setSelectedFrame(null)
    setQuestion('')
    setResult(null)
    setError(null)
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>올레 · LoL 코칭</h1>
        <button type="button" className="ghost" onClick={reset}>
          처음부터
        </button>
      </header>

      {error && <div className="banner error">{error}</div>}

      {/* 1단계: Riot ID */}
      <section className="card">
        <h2>1. 소환사 검색</h2>
        <form className="row" onSubmit={handleSearch}>
          <input
            type="text"
            placeholder="닉네임#KR1"
            value={riotId}
            onChange={(e) => setRiotId(e.target.value)}
          />
          <button type="submit" disabled={loadingSummoner}>
            {loadingSummoner ? '검색 중…' : '검색'}
          </button>
        </form>
        {summoner && (
          <p className="muted">
            {summoner.game_name}#{summoner.tag_line} · 솔로랭크 최근 20게임
          </p>
        )}
      </section>

      {/* 2단계: 매치 리스트 */}
      {summoner && (
        <section className="card">
          <h2>2. 최근 솔로랭크 경기</h2>
          {loadingMatches && <p className="muted">매치 불러오는 중…</p>}
          {!loadingMatches && matches.length === 0 && (
            <p className="muted">표시할 솔로랭크 경기가 없습니다.</p>
          )}
          <ul className="match-list">
            {matches.map((m) => {
              const me = findMe(m, summoner.puuid)
              const win = me?.win
              const active = m.matchId === selectedMatchId
              return (
                <li key={m.matchId}>
                  <button
                    type="button"
                    className={`match-row ${active ? 'active' : ''} ${
                      win ? 'win' : 'lose'
                    }`}
                    onClick={() => {
                      setSelectedMatchId(m.matchId)
                      setResult(null)
                    }}
                  >
                    <span className="tag">{win ? '승' : '패'}</span>
                    <span className="champ">
                      {me?.championName ?? '?'}
                    </span>
                    <span className="kda">
                      {me
                        ? `${me.kills}/${me.deaths}/${me.assists}`
                        : '-'}
                    </span>
                    <span className="muted small">
                      {fmtDuration(m.gameDuration)} ·{' '}
                      {fmtDate(m.gameCreation)}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {/* 3단계: 매치 상세 */}
      {selectedMatch && (
        <section className="card">
          <h2>3. 경기 상세</h2>
          <p className="muted small">
            {selectedMatch.matchId} · {fmtDuration(selectedMatch.gameDuration)}
          </p>
          {[100, 200].map((teamId) => {
            const team = selectedMatch.participants.filter(
              (p) => p.teamId === teamId,
            )
            const won = team[0]?.win
            return (
              <div key={teamId} className="team-block">
                <h3 className={won ? 'win' : 'lose'}>
                  {teamId === 100 ? '블루팀' : '레드팀'} ·{' '}
                  {won ? '승리' : '패배'}
                </h3>
                <table className="team-table">
                  <tbody>
                    {team.map((p, i) => {
                      const isMe = p.puuid === summoner?.puuid
                      return (
                        <tr key={i} className={isMe ? 'me' : ''}>
                          <td>{p.championName}</td>
                          <td>{p.summonerName ?? ''}</td>
                          <td>
                            {p.kills}/{p.deaths}/{p.assists}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )
          })}
        </section>
      )}

      {/* 4단계: 클립 업로드 */}
      {selectedMatch && (
        <section className="card">
          <h2>4. 코칭받을 클립 업로드</h2>
          <div className="row wrap">
            <input
              type="file"
              accept="video/*"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <label className="inline">
              프레임 간격
              <select
                value={fpsInterval}
                onChange={(e) => setFpsInterval(Number(e.target.value))}
              >
                {FPS_CHOICES.map((v) => (
                  <option key={v} value={v}>
                    {v}초당 1프레임
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={handleUpload}
              disabled={!file || uploading}
            >
              {uploading ? '추출 중…' : '업로드 & 프레임 추출'}
            </button>
          </div>
          {clip && (
            <p className="muted">
              프레임 {clip.frame_count}장 추출 · 영상{' '}
              {clip.duration_seconds}s · {clip.fps_interval}초 간격
            </p>
          )}
          {clip && clip.frame_count > 0 && (
            <div className="frame-picker">
              <p className="muted small">
                분석할 장면 1장을 고르세요
                {selectedFrame
                  ? ` · 선택: ${selectedFrame}번 프레임`
                  : ''}
              </p>
              <div className="frame-grid">
                {Array.from(
                  { length: clip.frame_count },
                  (_, i) => i + 1,
                ).map((n) => (
                  <button
                    type="button"
                    key={n}
                    className={`frame-thumb ${
                      selectedFrame === n ? 'active' : ''
                    }`}
                    onClick={() => {
                      setSelectedFrame(n)
                      setResult(null)
                    }}
                  >
                    <img
                      src={frameUrl(clip.clip_id, n)}
                      alt={`프레임 ${n}`}
                      loading="lazy"
                    />
                    <span>{n}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {/* 5단계: 질문 & 분석 */}
      {clip && (
        <section className="card">
          <h2>5. 코칭 질문</h2>
          <form onSubmit={handleAnalyze}>
            <textarea
              rows={3}
              placeholder="이 장면에서 내 선택이 맞았는지 봐줘"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
            />
            <div className="row wrap">
              <label className="inline">
                모델
                <select
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                >
                  {MODELS.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="submit"
                disabled={analyzing || !question.trim() || !selectedFrame}
              >
                {analyzing ? '올레가 보는 중…' : '분석 요청'}
              </button>
            </div>
            {!selectedFrame && (
              <p className="muted small">
                4단계에서 분석할 장면을 먼저 선택하세요.
              </p>
            )}
          </form>

          {result && (
            <div className="analysis">
              <p className="answer">{result.analysis}</p>
              <p className="muted small">
                {result.metadata.frame_number
                  ? `선택 장면 ${result.metadata.frame_number}번`
                  : `프레임 ${result.metadata.frames_analyzed}장`}{' '}
                · 미니맵 {result.metadata.minimaps_analyzed}장 · 코치노트{' '}
                {result.metadata.notes_referenced}개 ·{' '}
                {result.metadata.model} · ≈ $
                {result.metadata.estimated_cost_usd}
              </p>
            </div>
          )}
        </section>
      )}
    </div>
  )
}

export default App
