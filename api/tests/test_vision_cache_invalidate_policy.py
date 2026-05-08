"""S2 D5 phase 1 — vision_page_cache invalidate 정책 회귀 보호.

senior-planner v0.1 명세 §8.1 A — 3건의 신규 단위 테스트.

검증 포인트
- 같은 (sha256, page) 라도 prompt_version 미변경 시 hit (재호출 0)
- ENV `JETRAG_VISION_PROMPT_VERSION=v2` patch 시 같은 sha256+page → miss
  (caller fallback 으로 vision API 호출 발생)
- upsert 동일 키 2회 호출 시 ON CONFLICT DO NOTHING — 멱등 (race 안전)

기존 `test_vision_cache.py` 와 분리 운영
- 기존 파일 = ImageParser 통합 + lookup miss/db_failure 의 일반 동작
- 본 파일 = invalidate 정책 (prompt_version bump · ON CONFLICT 멱등) 만 격리
- S2 D5 phase 1 reingest 사전 보호 — phase 1 실 호출 시 cache hit/miss 판정에
  상기 3 케이스가 정확히 반영돼야 함 (prompt 변경 안 했는데 miss 면 비용 폭주).

stdlib unittest + mock only — Supabase 의존성 0.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# 모듈 import 단계의 ENV 요구 회피 (다른 테스트 파일과 동일 패턴).
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")
os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"


def _build_lookup_chain(client: MagicMock, *, return_data: list) -> None:
    """vision_cache.lookup 의 supabase-py 체인 mock 헬퍼.

    chain: client.table().select().eq().eq().eq().limit().execute()
    """
    chain = (
        client.table.return_value
        .select.return_value
        .eq.return_value
        .eq.return_value
        .eq.return_value
        .limit.return_value
    )
    chain.execute.return_value.data = return_data


class VisionCacheInvalidatePolicyTest(unittest.TestCase):
    """S2 D5 phase 1 명세 §8.1 A — invalidate 정책 3 케이스."""

    def setUp(self) -> None:
        from app.services import vision_cache

        vision_cache._reset_first_warn_for_test()
        # 환경 변수 baseline 복구 — 이전 테스트가 오염한 prompt_version 제거.
        os.environ.pop("JETRAG_VISION_PROMPT_VERSION", None)
        os.environ.pop("JETRAG_VISION_CACHE_ENABLED", None)

    def test_lookup_hits_when_prompt_version_unchanged(self) -> None:
        """같은 sha256+page+v1 키 → hit. caller 가 captioner 재호출 안 함.

        S2 D5 phase 1 의 핵심 절감 시나리오 — 기존 v1 캐시 row 가 있는 페이지에
        대해 reingest 발생 시 cache hit 으로 vision API 호출 0 보장.
        """
        from app.adapters.vision import VisionCaption
        from app.services import vision_cache

        client = MagicMock()
        _build_lookup_chain(
            client,
            return_data=[
                {
                    "result": {
                        "type": "표",
                        "ocr_text": "캐시된 ocr",
                        "caption": "캐시된 캡션",
                        "structured": {"rows": 5},
                    }
                }
            ],
        )

        with patch.object(vision_cache, "_VISION_PROMPT_VERSION", "v1"):
            with patch("app.db.get_supabase_client", return_value=client):
                cap = vision_cache.lookup("c" * 64, page=7)

        self.assertIsInstance(cap, VisionCaption)
        self.assertEqual(cap.type, "표")
        self.assertEqual(cap.caption, "캐시된 캡션")
        self.assertEqual(cap.ocr_text, "캐시된 ocr")
        self.assertEqual(cap.structured, {"rows": 5})

        # 마지막 eq 가 prompt_version="v1" 이었는지 — caller 가 prompt_version 매칭 행만 가져옴.
        last_eq = (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .eq.return_value
            .eq.call_args_list
        )
        self.assertTrue(
            any(call.args == ("prompt_version", "v1") for call in last_eq),
            f"prompt_version='v1' eq 필터가 적용되지 않음: {last_eq}",
        )

    def test_lookup_misses_when_prompt_version_bumped(self) -> None:
        """ENV bump 시 같은 sha256+page → miss → caller 가 fallback (vision API 호출).

        prompt 본질이 바뀌었으면 v1 row 의 결과는 더 이상 유효 아님 → invalidate.
        DB row 는 그대로 두되 lookup 이 v2 prompt_version 으로 필터하므로 매칭 0.
        """
        from app.services import vision_cache

        client = MagicMock()
        # v2 매칭 row 0 (DB 에는 v1 row 만 존재한다고 가정 — supabase-py 측에서 빈 결과 반환).
        _build_lookup_chain(client, return_data=[])

        os.environ["JETRAG_VISION_PROMPT_VERSION"] = "v2"
        try:
            # 모듈 상수도 함께 patch — vision_cache.get_prompt_version() 이 모듈 상수 참조.
            with patch.object(vision_cache, "_VISION_PROMPT_VERSION", "v2"):
                with patch("app.db.get_supabase_client", return_value=client):
                    cap = vision_cache.lookup("c" * 64, page=7)

            self.assertIsNone(cap, "prompt_version bump 후에는 같은 sha256+page 도 miss 여야 함")

            # eq("prompt_version", "v2") 가 마지막 eq 호출에 정확히 들어갔는지.
            last_eq = (
                client.table.return_value
                .select.return_value
                .eq.return_value
                .eq.return_value
                .eq.call_args_list
            )
            self.assertTrue(
                any(call.args == ("prompt_version", "v2") for call in last_eq),
                f"prompt_version='v2' eq 필터 적용 안 됨: {last_eq}",
            )
        finally:
            os.environ.pop("JETRAG_VISION_PROMPT_VERSION", None)

    def test_upsert_idempotent_on_conflict(self) -> None:
        """동일 (sha256, page, prompt_version) 키로 upsert 2회 — 둘 다 ON CONFLICT DO NOTHING.

        race 시 먼저 저장된 row 우선 — `ignore_duplicates=True` 로 supabase-py 가
        Prefer: resolution=ignore-duplicates 헤더 추가 → PostgreSQL ON CONFLICT
        DO NOTHING 동등. 두 호출 다 raise 없이 통과해야 함.

        본 테스트는 supabase-py 호출 시그니처만 검증 — 실 DB 의 ON CONFLICT 거동은
        Supabase 가 책임 (Postgres 표준).
        """
        from app.adapters.vision import VisionCaption
        from app.services import vision_cache

        cap = VisionCaption(
            type="표",
            ocr_text="멱등 테스트",
            caption="멱등 캡션",
            structured={"x": 1},
        )

        client = MagicMock()
        # 두 번 모두 정상 응답 — ON CONFLICT DO NOTHING 시 supabase-py 는 빈 데이터 반환.
        client.table.return_value.upsert.return_value.execute.return_value.data = []

        with patch("app.db.get_supabase_client", return_value=client):
            # 두 번 호출 — 둘 다 raise 안 함.
            vision_cache.upsert("d" * 64, page=2, caption=cap, estimated_cost=0.001)
            vision_cache.upsert("d" * 64, page=2, caption=cap, estimated_cost=0.001)

        # supabase-py upsert 호출 = 2회 (각 호출이 DB 까지 전달, ON CONFLICT 는 DB 측 처리).
        self.assertEqual(client.table.return_value.upsert.call_count, 2)

        # 두 호출 모두 on_conflict + ignore_duplicates 인자 일치.
        for call in client.table.return_value.upsert.call_args_list:
            self.assertEqual(
                call.kwargs.get("on_conflict"), "sha256,page,prompt_version",
                "on_conflict 키가 (sha256,page,prompt_version) 가 아니면 race 시 dup row 발생",
            )
            self.assertTrue(
                call.kwargs.get("ignore_duplicates"),
                "ignore_duplicates=True 가 아니면 ON CONFLICT DO NOTHING 동등 동작 X",
            )


if __name__ == "__main__":
    unittest.main()
