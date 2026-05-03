"""W3 Day 2 — 마이그레이션 004 (PGroonga 교체) 의 코드 측 회귀 차단.

검증 범위 (마이그레이션 적용 전 가능한 부분만):
    - `_sparse_only_fallback` 가 새 RPC `search_sparse_only_pgroonga` 를 호출 (Mock 검증)
    - `search_hybrid_rrf` 호출 시그니처 (인자 키) 가 003/004 사이 변경되지 않음
    - `compute_chunk_metrics` 휴리스틱이 표 노이즈 패턴을 정확히 식별

라이브 DB 검증 (마이그레이션 004 적용 후 메인 스레드 책임):
    - v0.5 §3.A AC: q="공사대금 합의해지" → sparse_hits > 0
    - search_sparse_only_pgroonga RPC 가 실제 PGroonga 인덱스로 매칭
    - flags.filtered_reason 이 있는 청크는 dense/sparse path 모두 제외

stdlib unittest 만 사용 — 외부 의존성 0.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch


# import 단계에서 HF 토큰 필요 — dummy 주입으로 충족.
# (SUPABASE_URL / SERVICE_ROLE_KEY 는 setdefault 하지 말 것 — test_search_user_isolation
#  의 _has_supabase_env() 가 truthy 로 판정해 dummy 키로 라이브 DB 호출 시도 → JWT 오류)
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")


class SparseOnlyFallbackUsesPgroongaRpcTest(unittest.TestCase):
    """`_sparse_only_fallback` 가 새 RPC `search_sparse_only_pgroonga` 를 호출하는지.

    배경: 마이그레이션 004 이전에는 PostgREST 의 `filter("fts", "plfts(simple)", ...)`
    로 chunks.fts 컬럼 직접 매칭. 004 적용 후 RPC 호출로 전환 — 이 변경이 회귀해
    옛 PostgREST 호출이 살아남으면 sparse-only path 가 chunks.fts (DROP 된 컬럼) 참조
    오류 발생 → 본 테스트가 차단.
    """

    def test_calls_new_pgroonga_rpc_with_correct_arguments(self) -> None:
        from app.routers.search import _RPC_TOP_K, _sparse_only_fallback

        client = MagicMock()
        rpc_returned = MagicMock()
        rpc_returned.execute.return_value.data = [
            {
                "chunk_id": "11111111-1111-1111-1111-111111111111",
                "doc_id": "22222222-2222-2222-2222-222222222222",
                "sparse_rank": 1,
            },
            {
                "chunk_id": "33333333-3333-3333-3333-333333333333",
                "doc_id": "22222222-2222-2222-2222-222222222222",
                "sparse_rank": 2,
            },
        ]
        client.rpc.return_value = rpc_returned

        rows = _sparse_only_fallback(
            client,
            q="공사대금 합의해지",
            user_id="00000000-0000-0000-0000-000000000001",
            top_k=_RPC_TOP_K,
        )

        # 1) RPC 이름이 새 함수여야 한다
        client.rpc.assert_called_once()
        rpc_name, rpc_args = client.rpc.call_args[0]
        self.assertEqual(
            rpc_name,
            "search_sparse_only_pgroonga",
            "_sparse_only_fallback 이 옛 PostgREST 경로를 호출하면 안 된다 — "
            "마이그레이션 004 의 chunks.fts DROP 회귀 위험.",
        )

        # 2) RPC 인자 키 검증 (004 SQL 시그니처와 1:1)
        self.assertEqual(
            set(rpc_args.keys()),
            {"query_text", "user_id_arg", "top_k"},
            f"RPC 인자 키 불일치: {sorted(rpc_args.keys())}",
        )
        self.assertEqual(rpc_args["query_text"], "공사대금 합의해지")
        self.assertEqual(rpc_args["top_k"], _RPC_TOP_K)
        # user_id 는 str 캐스트 (PostgREST UUID 직렬화 — qa C-1 회귀 차단)
        self.assertIsInstance(rpc_args["user_id_arg"], str)

        # 3) 변환된 dict 형식 — search() 본문 흐름과 동일 키 셋
        self.assertEqual(len(rows), 2)
        self.assertEqual(set(rows[0].keys()), {
            "chunk_id", "doc_id", "rrf_score", "dense_rank", "sparse_rank",
        })
        self.assertIsNone(rows[0]["dense_rank"])
        self.assertEqual(rows[0]["sparse_rank"], 1)
        # rrf_score = 1 / (60 + rank)
        self.assertAlmostEqual(rows[0]["rrf_score"], 1.0 / (60 + 1), places=6)
        self.assertAlmostEqual(rows[1]["rrf_score"], 1.0 / (60 + 2), places=6)

    def test_empty_rpc_response_returns_empty_list(self) -> None:
        """RPC 가 빈 결과 반환 시 빈 list — 호출부 (search()) 가 0건 처리 분기 사용."""
        from app.routers.search import _sparse_only_fallback

        client = MagicMock()
        rpc_returned = MagicMock()
        rpc_returned.execute.return_value.data = []
        client.rpc.return_value = rpc_returned

        rows = _sparse_only_fallback(
            client, q="없는단어", user_id="00000000-0000-0000-0000-000000000001",
            top_k=50,
        )
        self.assertEqual(rows, [])


class SearchHybridRpcSignatureUnchangedTest(unittest.TestCase):
    """search.py 가 RPC `search_hybrid_rrf` 를 003 ↔ 004 사이 동일 시그니처로 호출.

    004 SQL 의 함수 시그니처 (인자 + 반환 컬럼) 가 003 과 100% 같다는 명세 위반을
    코드 측에서도 차단 — search() 가 새 인자를 추가하면 즉시 실패.
    """

    def test_search_uses_expected_rpc_argument_keys(self) -> None:
        # 라우터 함수 내부 흐름을 mock 으로 격리 — Supabase 실제 호출 안 함.
        from app.routers import search as search_module

        captured: dict = {}

        def _capture_rpc(name, args):
            captured["name"] = name
            captured["args"] = args
            mock_resp = MagicMock()
            mock_resp.execute.return_value.data = []
            return mock_resp

        provider_mock = MagicMock()
        provider_mock.embed_query.return_value = [0.0] * 1024

        client_mock = MagicMock()
        client_mock.rpc.side_effect = _capture_rpc
        # documents 메타 fetch 도 호출되지만 빈 결과 분기 (rpc_rows=[]) 라
        # 실제 .table() 체인은 도달하지 않음 — 안전.

        with patch.object(search_module, "get_bgem3_provider",
                          return_value=provider_mock), \
             patch.object(search_module, "get_supabase_client",
                          return_value=client_mock):
            search_module.search(
                q="테스트쿼리",
                limit=10,
                offset=0,
                tags=None,
                doc_type=None,
                from_date=None,
                to_date=None,
                doc_id=None,
            )

        self.assertEqual(captured.get("name"), "search_hybrid_rrf")
        self.assertEqual(
            set(captured["args"].keys()),
            {"query_text", "query_dense", "k_rrf", "top_k", "user_id_arg"},
            "RPC 인자 키 변경 — 003 ↔ 004 시그니처 호환성 위반.",
        )


class ChunkQualityHeuristicsTest(unittest.TestCase):
    """G(1) 진단 도구의 휴리스틱 검증 — DB 의존성 0.

    diagnose_chunk_quality.compute_chunk_metrics 가 표 노이즈 패턴을 정확히 식별,
    일반 산문은 false positive 안 내는지.
    """

    def test_table_like_text_is_flagged_as_noise(self) -> None:
        from scripts.diagnose_chunk_quality import compute_chunk_metrics

        # 표 형태 — 짧은 라인 + 숫자/특수문자 비중 높음
        table_text = "\n".join([
            "1 | 100 | 200",
            "2 | 150 | 300",
            "3 | 175 | 400",
            "4 | 200 | 500",
            "5 | 225 | 600",
        ])
        m = compute_chunk_metrics(table_text)
        self.assertTrue(
            m["is_potential_table_noise"],
            f"표 형태가 노이즈로 판정 안 됨: {m}",
        )

    def test_prose_text_is_not_flagged(self) -> None:
        from scripts.diagnose_chunk_quality import compute_chunk_metrics

        prose = (
            "이 계약은 갑과 을 사이에 체결된 공사 도급 계약으로, "
            "공사 대금의 지급 및 합의 해지에 관한 사항을 명확히 규정한다. "
            "본 계약의 효력은 양 당사자가 서명한 날로부터 발생하며, "
            "별도의 합의가 없는 한 공사 완료일까지 유효하다."
        )
        m = compute_chunk_metrics(prose)
        self.assertFalse(
            m["is_potential_table_noise"],
            f"산문이 노이즈로 잘못 판정됨 (false positive): {m}",
        )

    def test_empty_text_is_safe(self) -> None:
        from scripts.diagnose_chunk_quality import compute_chunk_metrics

        m = compute_chunk_metrics("")
        self.assertEqual(m["length"], 0)
        self.assertFalse(m["is_potential_table_noise"])

    def test_header_footer_detection_repeats_threshold(self) -> None:
        from scripts.diagnose_chunk_quality import (
            detect_header_footer_candidates,
        )

        # 같은 doc 안에서 동일 짧은 텍스트 3회 등장 → flagged
        chunks_by_doc = {
            "doc-1": [
                {"id": "c1", "text": "Page 1 of 5"},
                {"id": "c2", "text": "본문 내용 1"},
                {"id": "c3", "text": "Page 1 of 5"},
                {"id": "c4", "text": "본문 내용 2"},
                {"id": "c5", "text": "Page 1 of 5"},
            ],
        }
        out = detect_header_footer_candidates(chunks_by_doc)
        self.assertIn("doc-1", out)
        self.assertEqual(out["doc-1"], {"c1", "c3", "c5"})

    def test_header_footer_below_threshold_not_flagged(self) -> None:
        from scripts.diagnose_chunk_quality import (
            detect_header_footer_candidates,
        )

        # 같은 텍스트 2회 — 임계값 (3) 미달
        chunks_by_doc = {
            "doc-1": [
                {"id": "c1", "text": "Header A"},
                {"id": "c2", "text": "본문"},
                {"id": "c3", "text": "Header A"},
            ],
        }
        out = detect_header_footer_candidates(chunks_by_doc)
        self.assertNotIn("doc-1", out)


if __name__ == "__main__":
    unittest.main()
