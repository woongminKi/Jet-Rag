"""S4-A D1 — `_VISION_PROMPT_VERSION` v1 → v2 bump 검증.

검증 포인트
- v1 prompt_version 으로 저장된 row 는 v2 lookup 에서 miss → captioner 재호출 유도
- v1 row 자체는 DELETE X (감사용 보존) — DB 측 보존 책임은 마이그/운영, 본 테스트는
  cache 헬퍼가 v1 row 를 강제 삭제하지 않음만 확인 (lookup eq 필터로 자연 격리)
- 기본값 'v2' + upsert 시 v2 적재 + table/figure_caption 직렬화 정합성도 부수 검증

stdlib unittest + mock only — Supabase 의존성 0.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# 환경 변수 stub
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")
os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"


class VisionCachePromptVersionV2Test(unittest.TestCase):
    """v1 → v2 bump 후 v1 row 가 lookup miss 되는지 종합 검증."""

    def setUp(self) -> None:
        from app.services import vision_cache
        vision_cache._reset_first_warn_for_test()
        os.environ.pop("JETRAG_VISION_PROMPT_VERSION", None)

    def test_v1_row_miss_after_bump(self) -> None:
        """v1 → v2 bump 종합 — 기본값 v2 + v1 row 격리 + upsert 시 v2 적재 + 새 2필드 직렬화.

        시뮬레이션: DB 에 prompt_version='v1' row 만 존재한다고 가정. v2 lookup 의
        .eq("prompt_version","v2") 단계에서 빈 결과 반환 → caller 는 None 받고 재호출.
        새 caption 적재 시 row 의 prompt_version='v2' + table_caption/figure_caption 보존.
        """
        from app.adapters.vision import VisionCaption
        from app.services import vision_cache

        # 1) 기본값이 'v2' (ENV override 없는 상태)
        self.assertEqual(vision_cache._VISION_PROMPT_VERSION, "v2")
        self.assertEqual(vision_cache.get_prompt_version(), "v2")

        # 2) v1 row 가 있어도 v2 lookup 에서 miss
        client = MagicMock()
        chain = (
            client.table.return_value
            .select.return_value
            .eq.return_value  # sha256
            .eq.return_value  # page
            .eq.return_value  # prompt_version → v2 매칭 0 (v1 row 는 격리됨)
            .limit.return_value
        )
        chain.execute.return_value.data = []

        with patch("app.db.get_supabase_client", return_value=client):
            cap = vision_cache.lookup("a" * 64, page=1)

        self.assertIsNone(cap)
        # 마지막 .eq 가 prompt_version='v2' 임을 검증
        last_eq = client.table.return_value.select.return_value.eq.return_value.eq.return_value.eq
        last_eq.assert_called_with("prompt_version", "v2")

        # 3) 새 caption upsert 시 prompt_version='v2' + 신규 2필드 직렬화
        new_cap = VisionCaption(
            type="표",
            ocr_text="셀A\t셀B",
            caption="비용 분석 표",
            structured={"cols": 2},
            table_caption="비용 분석",
            figure_caption=None,
        )
        client2 = MagicMock()
        with patch("app.db.get_supabase_client", return_value=client2):
            vision_cache.upsert("a" * 64, page=3, caption=new_cap, estimated_cost=0.001)

        upsert_args = client2.table.return_value.upsert.call_args
        row = upsert_args.args[0]
        self.assertEqual(row["prompt_version"], "v2")
        # S4-A D1 — table_caption / figure_caption 직렬화 (None 도 키 보존)
        self.assertEqual(row["result"]["table_caption"], "비용 분석")
        self.assertIsNone(row["result"]["figure_caption"])
        # ON CONFLICT DO NOTHING — v1 row 와 별개 키로 적재 (sha256,page,prompt_version)
        self.assertEqual(upsert_args.kwargs.get("on_conflict"), "sha256,page,prompt_version")
        self.assertTrue(upsert_args.kwargs.get("ignore_duplicates"))


if __name__ == "__main__":
    unittest.main()
