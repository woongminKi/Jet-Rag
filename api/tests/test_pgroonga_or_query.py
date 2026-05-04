"""W25 D10 차수 D-a — `_build_pgroonga_query` 헬퍼 회귀 차단.

마이그레이션 0 / 의존성 0 — 순수 문자열 변환. PGroonga `&@~` query 모드의
multi-token AND 매칭 문제를 OR 변환으로 우회.

근거 (W25 D9 진단 직접 검증):
    '소나타 전장' → 0 hits (AND 매칭, vocab '소나타' 부재로 전체 0)
    '소나타 OR 전장' → 2 hits (OR — '전장' 매칭으로 sparse 회복)
"""
from __future__ import annotations

import unittest

from app.routers.search import _build_pgroonga_query, _strip_korean_particle


class TestBuildPgroongaQuery(unittest.TestCase):
    def test_single_token_passthrough(self):
        # 단일 토큰은 OR 변환 무의미 → 그대로 반환.
        self.assertEqual(_build_pgroonga_query("소나타"), "소나타")
        self.assertEqual(_build_pgroonga_query("Sonata"), "Sonata")

    def test_two_tokens_or_join(self):
        self.assertEqual(_build_pgroonga_query("소나타 전장"), "소나타 OR 전장")
        self.assertEqual(
            _build_pgroonga_query("소나타 디스플레이"), "소나타 OR 디스플레이"
        )

    def test_three_or_more_tokens(self):
        # 자연어 query (3~5 단어) 가 일반적 — 모두 OR 로 결합.
        # D-a-2 적용 — "길이가" 의 조사 "가" strip 되어 "길이" 로.
        self.assertEqual(
            _build_pgroonga_query("소나타 전장 길이가 얼마나 돼"),
            "소나타 OR 전장 OR 길이 OR 얼마나 OR 돼",
        )

    def test_extra_whitespace_normalized(self):
        # 사용자 입력의 leading/trailing/내부 공백 정상화.
        self.assertEqual(_build_pgroonga_query("  소나타  전장  "), "소나타 OR 전장")
        self.assertEqual(_build_pgroonga_query("\t소나타\t전장\n"), "소나타 OR 전장")

    def test_empty_query(self):
        # 빈 문자열은 빈 문자열 반환 — 호출 직전 단계에서 검증된 값이라 방어 코드 최소.
        self.assertEqual(_build_pgroonga_query(""), "")
        self.assertEqual(_build_pgroonga_query("   "), "")

    def test_mixed_korean_english(self):
        # SONATA 카탈로그 같은 이중 언어 자료 대응.
        self.assertEqual(
            _build_pgroonga_query("Sonata 디스플레이"), "Sonata OR 디스플레이"
        )

    def test_user_typed_or_idempotent(self):
        # 사용자가 직접 'OR' 입력 시도 — 토큰화 후 재 join 해도 결과 정상 (semantic 동일).
        # PGroonga 가 'OR' 토큰을 query expression operator 로 해석.
        # 단, OR 토큰은 길이 2 라 조사 strip 미적용. "OR" 끝 "R" 은 한글 조사 아님.
        self.assertEqual(
            _build_pgroonga_query("회사 OR 매출"), "회사 OR OR OR 매출"
        )


class TestStripKoreanParticle(unittest.TestCase):
    """W25 D11 D-a-2 — 한국어 조사 strip whitelist."""

    def test_strip_simple_particles(self):
        # 흔한 1자 조사 — 토큰 길이 >= 3 일 때만 strip.
        self.assertEqual(_strip_korean_particle("전폭은"), "전폭")
        self.assertEqual(_strip_korean_particle("전고는"), "전고")
        self.assertEqual(_strip_korean_particle("길이가"), "길이")
        self.assertEqual(_strip_korean_particle("디스플레이는"), "디스플레이")
        self.assertEqual(_strip_korean_particle("회사가"), "회사")
        self.assertEqual(_strip_korean_particle("매출은"), "매출")

    def test_loanword_with_i_ending_preserved(self):
        # "이" 는 외래어 명사 끝 충돌로 strip 대상 제외.
        # "디스플레이/알고리즘/오디오" 류 보호.
        self.assertEqual(_strip_korean_particle("디스플레이"), "디스플레이")
        self.assertEqual(_strip_korean_particle("프로토타이"), "프로토타이")

    def test_short_token_not_stripped(self):
        # 토큰 길이 < 3 보호 — false positive 회피.
        self.assertEqual(_strip_korean_particle("회사"), "회사")  # 명사 자체
        self.assertEqual(_strip_korean_particle("우리"), "우리")
        self.assertEqual(_strip_korean_particle("는"), "는")  # 조사 단독 (길이 1)
        self.assertEqual(_strip_korean_particle("가가"), "가가")  # 길이 2

    def test_non_particle_ending_preserved(self):
        # 조사 whitelist 외 끝 글자는 보존.
        self.assertEqual(_strip_korean_particle("얼마나"), "얼마나")  # '나' 비조사
        self.assertEqual(_strip_korean_particle("종류야"), "종류야")  # '야' 비조사 (어미)
        self.assertEqual(_strip_korean_particle("Sonata"), "Sonata")  # 영문
        self.assertEqual(_strip_korean_particle("ccNC"), "ccNC")

    def test_build_pgroonga_query_with_particles(self):
        # 격차 4건 query 시뮬 (W25 D9 진단).
        # punctuation strip 까지 적용 — '전폭은?' → '전폭'.
        self.assertEqual(
            _build_pgroonga_query("소나타 전폭은?"),
            "소나타 OR 전폭",
        )
        self.assertEqual(
            _build_pgroonga_query("소나타 전폭은"),
            "소나타 OR 전폭",
        )
        self.assertEqual(
            _build_pgroonga_query("소나타 디스플레이는 어떤 종류야"),
            "소나타 OR 디스플레이 OR 어떤 OR 종류야",
        )

    def test_trailing_punctuation_stripped(self):
        # 의문문/문장부호 정리.
        self.assertEqual(_strip_korean_particle("전폭은?"), "전폭")
        self.assertEqual(_strip_korean_particle("디스플레이는!"), "디스플레이")
        self.assertEqual(_strip_korean_particle("회사"), "회사")  # punctuation 없음
        self.assertEqual(_strip_korean_particle("회사."), "회사")  # 길이 2 보호 (cleaned)

    def test_pure_korean_passthrough_when_short(self):
        # 한국어 단일 명사 (길이 2) — 단일 토큰 + strip 미적용.
        self.assertEqual(_build_pgroonga_query("회사"), "회사")


if __name__ == "__main__":
    unittest.main()
