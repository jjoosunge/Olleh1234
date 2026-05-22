"""1·2단계 워크플로우 핵심 로직 회귀 테스트.

외부 네트워크/키 없이 순수 로직만 검증한다.
실행 (backend 디렉터리에서):
    venv/Scripts/python.exe -m unittest discover -s tests -t .
"""

import tempfile
import unittest
from pathlib import Path

from app.services.analyzer import (
    _find_user_desc,
    _format_notes,
    _load_frames_as_blocks,
    _load_single_frame_blocks,
    _norm_duration_seconds,
    _note_category,
    _region,
    _select_frame_indices,
    _summarize_match,
)
from app.services.cleanup import sweep_old_clips
from app.services.cv_processor import parse_game_time
from app.services.history import _is_exemplary
from app.services.riot_api import RiotAPIClient


class RiotQueueFilterTest(unittest.TestCase):
    """1단계: 솔로랭크 필터(queue=420)가 Riot 요청 파라미터로 전달되는지."""

    def _client(self) -> RiotAPIClient:
        return RiotAPIClient(api_key="dummy-key")

    def test_queue_passed_when_set(self):
        client = self._client()
        captured = {}

        def fake_request(url, params=None, max_retries=3):
            captured["url"] = url
            captured["params"] = params
            return []

        client._request = fake_request  # type: ignore[assignment]
        client.get_match_ids_by_puuid("puuid-1", count=20, queue=420)

        self.assertEqual(captured["params"]["queue"], 420)
        self.assertEqual(captured["params"]["count"], 20)

    def test_queue_absent_when_none(self):
        client = self._client()
        captured = {}

        def fake_request(url, params=None, max_retries=3):
            captured["params"] = params
            return []

        client._request = fake_request  # type: ignore[assignment]
        client.get_match_ids_by_puuid("puuid-1", count=10, queue=None)

        self.assertNotIn("queue", captured["params"])
        self.assertEqual(captured["params"]["count"], 10)


class NormDurationTest(unittest.TestCase):
    """2단계: gameDuration 초/ms 환산."""

    def test_seconds_passthrough(self):
        self.assertEqual(_norm_duration_seconds(1530), 1530)

    def test_milliseconds_converted(self):
        self.assertEqual(_norm_duration_seconds(900000), 900)

    def test_none_and_invalid(self):
        self.assertEqual(_norm_duration_seconds(None), 0)
        self.assertEqual(_norm_duration_seconds("abc"), 0)


class SummarizeMatchTest(unittest.TestCase):
    """2단계: 심화 매치 요약 — 필드·포지션 정렬·오브젝트."""

    def _raw(self) -> dict:
        return {
            "metadata": {"matchId": "KR_TEST"},
            "info": {
                "gameDuration": 1200,
                "queueId": 420,
                "teams": [
                    {
                        "teamId": 100,
                        "win": True,
                        "objectives": {
                            "dragon": {"kills": 3},
                            "baron": {"kills": 1},
                            "riftHerald": {"kills": 1},
                            "tower": {"kills": 7},
                            "inhibitor": {"kills": 1},
                        },
                    },
                    {"teamId": 200, "win": False, "objectives": {}},
                ],
                "participants": [
                    {
                        "teamId": 100,
                        "teamPosition": "UTILITY",
                        "championName": "Bard",
                        "riotIdGameName": "Sup",
                        "champLevel": 14,
                        "kills": 1,
                        "deaths": 2,
                        "assists": 20,
                        "totalMinionsKilled": 30,
                        "neutralMinionsKilled": 0,
                        "goldEarned": 9000,
                        "totalDamageDealtToChampions": 8000,
                        "visionScore": 66,
                        "win": True,
                    },
                    {
                        "teamId": 100,
                        "teamPosition": "TOP",
                        "championName": "Ornn",
                        "riotIdGameName": "TopLaner",
                        "champLevel": 16,
                        "kills": 4,
                        "deaths": 3,
                        "assists": 8,
                        "totalMinionsKilled": 200,
                        "neutralMinionsKilled": 12,
                        "goldEarned": 13000,
                        "totalDamageDealtToChampions": 20000,
                        "visionScore": 25,
                        "win": True,
                    },
                ],
            },
        }

    def test_contains_deep_fields(self):
        out = _summarize_match(self._raw())
        self.assertIn("매치 ID: KR_TEST", out)
        self.assertIn("20분 0초", out)  # 1200초
        self.assertIn("골드", out)
        self.assertIn("시야", out)
        self.assertIn("CS", out)
        self.assertIn("드래곤 3", out)
        self.assertIn("바론 1", out)
        self.assertIn("블루팀", out)

    def test_position_ordering(self):
        out = _summarize_match(self._raw())
        # TOP이 UTILITY보다 먼저 나와야 한다 (포지션 정렬)
        self.assertLess(out.index("Ornn"), out.index("Bard"))

    def test_cs_per_minute(self):
        out = _summarize_match(self._raw())
        # Ornn CS = 212, 20분 → 10.6/분
        self.assertIn("CS 212(10.6/분)", out)

    def test_ms_duration_in_summary(self):
        raw = self._raw()
        raw["info"]["gameDuration"] = 1200000  # ms
        out = _summarize_match(raw)
        self.assertIn("20분 0초", out)

    def test_refined_shape_graceful(self):
        # 얕은 refined 캐시 shape도 깨지지 않아야 함
        refined = {
            "matchId": "KR_R",
            "gameDuration": 900,
            "queueId": 420,
            "participants": [
                {
                    "teamId": 100,
                    "championName": "Lux",
                    "summonerName": "X",
                    "kills": 1,
                    "deaths": 1,
                    "assists": 1,
                    "win": True,
                }
            ],
        }
        out = _summarize_match(refined)
        self.assertIn("Lux", out)
        self.assertIn("매치 ID: KR_R", out)


class UserIdentityTest(unittest.TestCase):
    """사용자 본인(puuid) 식별 — 챔피언 오인 방지."""

    def _raw(self) -> dict:
        return {
            "metadata": {"matchId": "KR_U"},
            "info": {
                "gameDuration": 1200,
                "queueId": 420,
                "teams": [{"teamId": 100, "win": True, "objectives": {}}],
                "participants": [
                    {
                        "teamId": 100,
                        "teamPosition": "JUNGLE",
                        "championName": "Zac",
                        "riotIdGameName": "xio3o",
                        "puuid": "PUUID_JGL",
                        "kills": 1,
                        "deaths": 9,
                        "assists": 19,
                    },
                    {
                        "teamId": 100,
                        "teamPosition": "UTILITY",
                        "championName": "Nautilus",
                        "riotIdGameName": "Let me sup",
                        "puuid": "PUUID_ME",
                        "kills": 1,
                        "deaths": 5,
                        "assists": 22,
                    },
                ],
            },
        }

    def test_find_user_desc(self):
        self.assertEqual(
            _find_user_desc(self._raw(), "PUUID_ME"),
            "Nautilus (Let me sup, UTILITY)",
        )
        self.assertIsNone(_find_user_desc(self._raw(), None))
        self.assertIsNone(_find_user_desc(self._raw(), "NOPE"))

    def test_summary_marks_only_user_row(self):
        out = _summarize_match(self._raw(), "PUUID_ME")
        nautilus_line = next(
            ln for ln in out.splitlines() if "Nautilus" in ln
        )
        zac_line = next(ln for ln in out.splitlines() if "Zac" in ln)
        self.assertIn("← 사용자 본인", nautilus_line)
        self.assertNotIn("← 사용자 본인", zac_line)

    def test_no_marker_without_puuid(self):
        out = _summarize_match(self._raw())
        self.assertNotIn("← 사용자 본인", out)


class NotesSplitTest(unittest.TestCase):
    """2단계: RAG 결과 코치 노트 / 위키 출처 분리."""

    def test_note_category(self):
        self.assertEqual(_note_category("knowledge/x.md"), "coach")
        self.assertEqual(_note_category("general-lol-wiki/champions/a.md"), "wiki")
        self.assertEqual(_note_category("general-lol-wiki\\concepts\\b.md"), "wiki")
        self.assertEqual(_note_category(""), "coach")

    def test_format_splits_sources(self):
        notes = [
            {
                "source_file": "knowledge/elimination-thinking.md",
                "content": "코치 프레임",
                "tags": "topic:macro",
                "similarity": 0.8,
            },
            {
                "source_file": "general-lol-wiki/champions/bard.md",
                "content": "바드 정보",
                "tags": "champ:bard",
                "similarity": 0.6,
            },
        ]
        out = _format_notes(notes)
        self.assertIn("코치 노트", out)
        self.assertIn("위키", out)
        self.assertIn("코치 프레임", out)
        self.assertIn("바드 정보", out)
        # 코치 노트 섹션이 위키 섹션보다 먼저
        self.assertLess(out.index("코치 프레임"), out.index("바드 정보"))

    def test_empty_coach_emits_more_info_marker(self):
        out = _format_notes(
            [
                {
                    "source_file": "general-lol-wiki/x.md",
                    "content": "위키만",
                    "tags": "",
                    "similarity": 0.5,
                }
            ]
        )
        self.assertIn("더 정보 필요", out)
        self.assertIn("위키만", out)


class FrameSelectionTest(unittest.TestCase):
    """긴 클립 가드: 프레임 인덱스 선택·시간순 균등 샘플링."""

    def test_under_cap_passthrough(self):
        self.assertEqual(_select_frame_indices(5, max_pairs=40), [1, 2, 3, 4, 5])

    def test_at_cap_passthrough(self):
        self.assertEqual(
            _select_frame_indices(40, max_pairs=40), list(range(1, 41))
        )

    def test_over_cap_subsampled(self):
        idx = _select_frame_indices(100, max_pairs=40)
        self.assertEqual(len(idx), 40)          # 정확히 상한만큼
        self.assertEqual(len(set(idx)), 40)     # 중복 없음
        self.assertEqual(idx, sorted(idx))      # 시간 순서 유지
        self.assertGreaterEqual(idx[0], 1)
        self.assertLessEqual(idx[-1], 100)

    def test_zero_and_negative(self):
        self.assertEqual(_select_frame_indices(0), [])
        self.assertEqual(_select_frame_indices(-3), [])


class FrameLoadingTest(unittest.TestCase):
    """프레임 → [전체 화면]+[미니맵 확대] 블록 페어링."""

    def _make_clip(self, tmp: str, frames: int, minimaps: set) -> Path:
        clip_dir = Path(tmp)
        frames_dir = clip_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, frames + 1):
            (frames_dir / f"frame_{i:04d}.jpg").write_bytes(b"\xff\xd8stub")
            if i in minimaps:
                (frames_dir / f"minimap_{i:04d}.jpg").write_bytes(b"\xff\xd8stub")
        return clip_dir

    def test_pairs_full_and_minimap(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip_dir = self._make_clip(tmp, frames=3, minimaps={1, 2, 3})
            blocks, n_full, n_mini = _load_frames_as_blocks(clip_dir, 3)
            self.assertEqual((n_full, n_mini), (3, 3))
            # 프레임당 (텍스트+이미지) ×2(전체/미니맵) = 12블록
            self.assertEqual(len(blocks), 12)
            self.assertEqual(blocks[0]["text"], "[프레임 1 · 전체 화면]")
            self.assertEqual(blocks[1]["type"], "image")
            self.assertEqual(blocks[2]["text"], "[프레임 1 · 미니맵 확대]")

    def test_minimap_optional(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip_dir = self._make_clip(tmp, frames=3, minimaps={2})
            _, n_full, n_mini = _load_frames_as_blocks(clip_dir, 3)
            self.assertEqual((n_full, n_mini), (3, 1))

    def test_missing_frame_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 파일은 2개뿐인데 frame_count=5라 주장해도 실제 파일만 실린다
            clip_dir = self._make_clip(tmp, frames=2, minimaps=set())
            _, n_full, n_mini = _load_frames_as_blocks(clip_dir, 5)
            self.assertEqual((n_full, n_mini), (2, 0))

    def test_over_cap_limits_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip_dir = self._make_clip(tmp, frames=100, minimaps=set())
            _, n_full, _ = _load_frames_as_blocks(clip_dir, 100)
            self.assertEqual(n_full, 40)  # MAX_FRAME_PAIRS


class SingleFrameTest(unittest.TestCase):
    """단일 프레임 모드: 사용자가 고른 1장 + 미니맵 로딩."""

    def _make_clip(self, tmp: str, frames: int, minimaps: set) -> Path:
        clip_dir = Path(tmp)
        frames_dir = clip_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, frames + 1):
            (frames_dir / f"frame_{i:04d}.jpg").write_bytes(b"\xff\xd8stub")
            if i in minimaps:
                (frames_dir / f"minimap_{i:04d}.jpg").write_bytes(b"\xff\xd8stub")
        return clip_dir

    def test_with_minimap(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip_dir = self._make_clip(tmp, frames=5, minimaps={3})
            blocks, n_full, n_mini = _load_single_frame_blocks(clip_dir, 3)
            self.assertEqual((n_full, n_mini), (1, 1))
            self.assertEqual(len(blocks), 4)  # 텍스트+이미지 ×2
            self.assertEqual(blocks[0]["text"], "[선택 장면 · 전체 화면]")
            self.assertEqual(blocks[2]["text"], "[선택 장면 · 미니맵 확대]")

    def test_without_minimap(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip_dir = self._make_clip(tmp, frames=5, minimaps=set())
            blocks, n_full, n_mini = _load_single_frame_blocks(clip_dir, 2)
            self.assertEqual((n_full, n_mini), (1, 0))
            self.assertEqual(len(blocks), 2)

    def test_missing_frame_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip_dir = self._make_clip(tmp, frames=2, minimaps=set())
            with self.assertRaises(FileNotFoundError):
                _load_single_frame_blocks(clip_dir, 9)


class ExemplaryTest(unittest.TestCase):
    """학습 풀(vec_analyses) 편입 조건: 판독·코칭 모두 👍."""

    def test_both_up_is_exemplary(self):
        self.assertTrue(_is_exemplary("up", "up"))

    def test_partial_or_negative_not_exemplary(self):
        self.assertFalse(_is_exemplary("up", None))
        self.assertFalse(_is_exemplary(None, "up"))
        self.assertFalse(_is_exemplary("up", "down"))
        self.assertFalse(_is_exemplary("down", "down"))
        self.assertFalse(_is_exemplary(None, None))


class CvTimerTest(unittest.TestCase):
    """CV 타이머 문자열 파싱 — 형식·범위 검증."""

    def test_valid(self):
        self.assertEqual(parse_game_time("8:32"), 512)
        self.assertEqual(parse_game_time("12:05"), 725)
        self.assertEqual(parse_game_time("타이머 0:45 표시"), 45)

    def test_invalid(self):
        self.assertIsNone(parse_game_time(""))
        self.assertIsNone(parse_game_time("abc"))
        self.assertIsNone(parse_game_time("8:99"))    # 초 60 이상
        self.assertIsNone(parse_game_time("999:00"))  # 게임시간 범위 초과


class RegionTest(unittest.TestCase):
    """타임라인 정규화 좌표 → 대략적 구역명."""

    def test_bases(self):
        self.assertEqual(_region(0.05, 0.05), "블루 기지")
        self.assertEqual(_region(0.95, 0.95), "레드 기지")

    def test_lanes(self):
        self.assertEqual(_region(0.8, 0.2), "봇 쪽")
        self.assertEqual(_region(0.2, 0.8), "탑 쪽")
        self.assertEqual(_region(0.5, 0.5), "미드 쪽")


class ClipSweepTest(unittest.TestCase):
    """오래된 클립 자동 정리 — 수정 시각 기준, UUID 폴더만 대상."""

    def test_removes_old_keeps_new(self):
        import os
        import time

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            old = base / "11111111-1111-1111-1111-111111111111"
            new = base / "22222222-2222-2222-2222-222222222222"
            old.mkdir()
            new.mkdir()
            old_ts = time.time() - 10 * 86400  # 10일 전
            os.utime(old, (old_ts, old_ts))
            removed = sweep_old_clips(max_age_days=7, clips_dir=base)
            self.assertEqual(removed, [old.name])
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())

    def test_ignores_non_uuid_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "not-a-uuid").mkdir()
            removed = sweep_old_clips(max_age_days=0, clips_dir=base)
            self.assertEqual(removed, [])


if __name__ == "__main__":
    unittest.main()
