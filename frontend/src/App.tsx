import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import './App.css'
import {
  analyzeStream,
  deleteAnalysis,
  durationSeconds,
  frameUrl,
  generateMetaReport,
  getFrameCv,
  getMatch,
  getMatchIds,
  getMetaStats,
  getSummoner,
  listAnalyses,
  listClips,
  mapLimit,
  rateAnalysis,
  uploadClip,
} from './api'
import type {
  AnalysisSummary,
  AnalyzeResult,
  ClipMeta,
  FrameCv,
  MatchDetail,
  MetaReport,
  Participant,
  Rating,
  RatingStats,
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

function fmtDateTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('ko-KR')
}

function ratingMark(r: Rating): string {
  return r === 'up' ? '👍' : r === 'down' ? '👎' : '·'
}

function RatingRow({
  label,
  value,
  onChange,
}: {
  label: string
  value: Rating
  onChange: (v: Rating) => void
}) {
  return (
    <div className="rating-row">
      <span className="rating-label">{label}</span>
      <button
        type="button"
        className={`rate-btn ${value === 'up' ? 'on up' : ''}`}
        onClick={() => onChange(value === 'up' ? null : 'up')}
      >
        👍
      </button>
      <button
        type="button"
        className={`rate-btn ${value === 'down' ? 'on down' : ''}`}
        onClick={() => onChange(value === 'down' ? null : 'down')}
      >
        👎
      </button>
    </div>
  )
}

function RatingBlock({
  reading,
  coaching,
  onRate,
}: {
  reading: Rating
  coaching: Rating
  onRate: (reading: Rating, coaching: Rating) => void
}) {
  return (
    <div className="rating-block">
      <RatingRow
        label="장면·미니맵 판독"
        value={reading}
        onChange={(v) => onRate(v, coaching)}
      />
      <RatingRow
        label="코칭"
        value={coaching}
        onChange={(v) => onRate(reading, v)}
      />
    </div>
  )
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
  const [clips, setClips] = useState<ClipMeta[]>([])
  const [uploading, setUploading] = useState(false)

  // 4-1단계: 분석할 단일 프레임 선택 + CV 미리보기
  const [selectedFrame, setSelectedFrame] = useState<number | null>(null)
  const [gameTime, setGameTime] = useState('')
  const [frameCv, setFrameCv] = useState<FrameCv | null>(null)
  const [frameCvLoading, setFrameCvLoading] = useState(false)

  // 5단계: 질문/분석
  const [question, setQuestion] = useState('')
  const [model, setModel] = useState<string>(MODELS[0])
  const [analyzing, setAnalyzing] = useState(false)
  const [result, setResult] = useState<AnalyzeResult | null>(null)
  const [streamingText, setStreamingText] = useState('')

  const [error, setError] = useState<string | null>(null)

  // 분석 히스토리 + 메타 코칭
  const [history, setHistory] = useState<AnalysisSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [metaStats, setMetaStats] = useState<RatingStats | null>(null)
  const [metaReport, setMetaReport] = useState<MetaReport | null>(null)
  const [metaLoading, setMetaLoading] = useState(false)

  const selectedMatch = matches.find((m) => m.matchId === selectedMatchId) ?? null
  const resultRecord =
    result?.analysis_id != null
      ? history.find((h) => h.id === result.analysis_id) ?? null
      : null

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
    setGameTime('')
    setFrameCv(null)
    setResult(null)
    try {
      const meta = await uploadClip(file, fpsInterval)
      setClip(meta)
      loadClips()
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
    setStreamingText('')
    try {
      const r = await analyzeStream(
        clip.clip_id,
        question.trim(),
        selectedMatchId,
        summoner?.puuid ?? null,
        selectedFrame,
        gameTime.trim() || null,
        model,
        (text) => setStreamingText((prev) => prev + text),
      )
      setResult(r)
      setStreamingText('')
      loadHistory()
    } catch (e) {
      setError(errMsg(e))
    } finally {
      setAnalyzing(false)
    }
  }

  async function loadHistory() {
    setHistoryLoading(true)
    try {
      setHistory(await listAnalyses(50))
      setMetaStats(await getMetaStats())
    } catch {
      // 히스토리 로드 실패가 메인 흐름을 막지 않도록 조용히 무시
    } finally {
      setHistoryLoading(false)
    }
  }

  async function loadClips() {
    try {
      setClips(await listClips())
    } catch {
      // 클립 목록 로드 실패는 조용히 무시
    }
  }

  useEffect(() => {
    loadHistory()
    loadClips()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleRate(id: number, reading: Rating, coaching: Rating) {
    try {
      const updated = await rateAnalysis(id, reading, coaching)
      setHistory((prev) => prev.map((h) => (h.id === id ? updated : h)))
      getMetaStats().then(setMetaStats).catch(() => {})
    } catch (e) {
      setError(errMsg(e))
    }
  }

  async function handleDeleteAnalysis(id: number) {
    try {
      await deleteAnalysis(id)
      setHistory((prev) => prev.filter((h) => h.id !== id))
    } catch (e) {
      setError(errMsg(e))
    }
  }

  async function handleGenerateReport() {
    setMetaLoading(true)
    try {
      setMetaReport(await generateMetaReport())
    } catch (e) {
      setError(errMsg(e))
    } finally {
      setMetaLoading(false)
    }
  }

  async function loadFrameCv(frameNumber: number) {
    if (!clip) return
    setFrameCv(null)
    setFrameCvLoading(true)
    try {
      const cv = await getFrameCv(clip.clip_id, frameNumber)
      setFrameCv(cv)
      setGameTime(cv.game_time ?? '')
    } catch {
      // CV 미리보기 실패 — 게임 시각은 사용자가 직접 입력
      setGameTime('')
    } finally {
      setFrameCvLoading(false)
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
    setGameTime('')
    setFrameCv(null)
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
          {clips.length > 0 && (
            <div className="clip-reuse">
              <p className="muted small">또는 이전에 올린 클립 재사용:</p>
              <ul className="clip-list">
                {clips.map((c) => (
                  <li key={c.clip_id}>
                    <button
                      type="button"
                      className={`clip-row ${
                        clip?.clip_id === c.clip_id ? 'active' : ''
                      }`}
                      onClick={() => {
                        setClip(c)
                        setSelectedFrame(null)
                        setGameTime('')
                        setFrameCv(null)
                        setResult(null)
                      }}
                    >
                      {c.original_filename || c.clip_id.slice(0, 8)} · 프레임{' '}
                      {c.frame_count}장 · {c.duration_seconds}s
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
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
                      loadFrameCv(n)
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
              {selectedFrame && (
                <div className="game-time-row">
                  <label className="inline">
                    게임 시각
                    <input
                      type="text"
                      className="time-input"
                      placeholder="예: 8:32"
                      value={gameTime}
                      onChange={(e) => setGameTime(e.target.value)}
                    />
                  </label>
                  {frameCvLoading && (
                    <span className="muted small">자동 감지 중…</span>
                  )}
                  {!frameCvLoading && frameCv && (
                    <span className="muted small">
                      {frameCv.game_time
                        ? `타이머 자동 감지: ${frameCv.game_time}`
                        : '타이머 자동 감지 실패 — 직접 입력하세요'}
                      {frameCv.frame_quality === 'low'
                        ? ' · ⚠ 프레임 화질 낮음'
                        : ''}
                    </span>
                  )}
                </div>
              )}
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

          {analyzing && streamingText && (
            <div className="analysis">
              <p className="answer">
                {streamingText}
                <span className="cursor">▌</span>
              </p>
            </div>
          )}
          {result && (
            <div className="analysis">
              <p className="answer">{result.analysis}</p>
              <p className="muted small">
                {result.metadata.frame_number
                  ? `선택 장면 ${result.metadata.frame_number}번`
                  : `프레임 ${result.metadata.frames_analyzed}장`}{' '}
                · 미니맵 {result.metadata.minimaps_analyzed}장 · 코치노트{' '}
                {result.metadata.notes_referenced}개 · 학습예시{' '}
                {result.metadata.good_examples_used}개 ·{' '}
                {result.metadata.model} · ≈ $
                {result.metadata.estimated_cost_usd}
              </p>
              <p className="muted small">
                게임 시각 {result.metadata.game_time ?? '판독 실패'}
                {result.metadata.timeline_used
                  ? ' · 타임라인 적용됨'
                  : ' · 타임라인 미적용'}
                {result.metadata.frame_quality === 'low'
                  ? ' · ⚠ 프레임 화질 낮음'
                  : ''}
              </p>
              {result.analysis_id != null ? (
                <RatingBlock
                  reading={resultRecord?.rating_reading ?? null}
                  coaching={resultRecord?.rating_coaching ?? null}
                  onRate={(r, c) =>
                    handleRate(result.analysis_id as number, r, c)
                  }
                />
              ) : (
                <p className="muted small">
                  이 분석은 히스토리 저장에 실패했습니다.
                </p>
              )}
            </div>
          )}
        </section>
      )}

      {/* 분석 히스토리 + 메타 코칭 */}
      <section className="card">
        <h2>분석 히스토리</h2>
        {metaStats && metaStats.total_analyses > 0 && (
          <div className="meta-panel">
            <p className="muted small">
              분석 {metaStats.total_analyses}건 · 판독 👍
              {metaStats.reading.up}/👎{metaStats.reading.down} · 코칭 👍
              {metaStats.coaching.up}/👎{metaStats.coaching.down}
            </p>
            <button
              type="button"
              className="small-btn"
              onClick={handleGenerateReport}
              disabled={metaLoading}
            >
              {metaLoading ? '리포트 생성 중…' : '누적 코칭 리포트 생성'}
            </button>
            {metaReport && (
              <div className="meta-report">
                <p className="answer">{metaReport.report}</p>
                <p className="muted small">
                  최근 분석 {metaReport.based_on}건 기준
                </p>
              </div>
            )}
          </div>
        )}
        {historyLoading && history.length === 0 && (
          <p className="muted">불러오는 중…</p>
        )}
        {!historyLoading && history.length === 0 && (
          <p className="muted">아직 저장된 분석이 없습니다.</p>
        )}
        <ul className="history-list">
          {history.map((h) => (
            <li key={h.id} className="history-item">
              <div className="history-head">
                <button
                  type="button"
                  className="history-q"
                  onClick={() =>
                    setExpandedId(expandedId === h.id ? null : h.id)
                  }
                >
                  <span className="hist-rating">
                    판독 {ratingMark(h.rating_reading)} · 코칭{' '}
                    {ratingMark(h.rating_coaching)}
                  </span>
                  <span className="hist-q-text">{h.user_question}</span>
                </button>
                <span className="muted small">
                  {fmtDateTime(h.created_at)}
                </span>
              </div>
              {expandedId === h.id && (
                <div className="history-body">
                  <p className="answer">{h.analysis_text}</p>
                  <p className="muted small">
                    {h.frame_number != null
                      ? `선택 장면 ${h.frame_number}번`
                      : '멀티프레임'}{' '}
                    · {h.model}
                  </p>
                  <RatingBlock
                    reading={h.rating_reading}
                    coaching={h.rating_coaching}
                    onRate={(r, c) => handleRate(h.id, r, c)}
                  />
                  <button
                    type="button"
                    className="ghost small-btn"
                    onClick={() => handleDeleteAnalysis(h.id)}
                  >
                    삭제
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

export default App
