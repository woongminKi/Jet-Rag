"""S3 D4 — `app.services.mmr` 단위 테스트 (planner v0.1 §G #5·#7 등).

검증 범위
---------
5. cap 적용 — candidates 50 입력 시 reranker pair 20 만 전달
   (cap 자체는 search.py 의 `_resolve_reranker_cap()` — 본 파일 #cap 케이스로 검증).
6. degrade 분기 — usage_log SUM ≥ 80% → HF mock 호출 0 (본 파일 #degrade 케이스).
7. MMR 다양성 — 같은 doc 의 chunk 3개 입력 시 top-3 distinct doc_id ≥ 0.66.

본 파일은 **MMR 모듈 단독 단위 테스트** (#7 + 보조 2건). #5 cap / #6 degrade /
#8 헤더는 `tests/test_search_reranker_path.py` 에서 검증.

stdlib unittest only — 의존성 추가 0.
"""

from __future__ import annotations

import os
import unittest

# 환경 변수 stub.
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")
os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"


def _orthogonal_embeddings(dim: int = 8) -> dict[str, list[float]]:
    """결정성 있는 직교 (또는 거의 직교) 단위벡터 — sim 계산 안정성."""
    return {
        "doc-a": [1.0] + [0.0] * (dim - 1),
        "doc-b": [0.0, 1.0] + [0.0] * (dim - 2),
        "doc-c": [0.0, 0.0, 1.0] + [0.0] * (dim - 3),
    }


class _BaseMmrTest(unittest.TestCase):
    """공통 setup — MMR 관련 ENV 정리."""

    def setUp(self) -> None:
        for k in ("JETRAG_MMR_LAMBDA", "JETRAG_MMR_DISABLE"):
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k in ("JETRAG_MMR_LAMBDA", "JETRAG_MMR_DISABLE"):
            os.environ.pop(k, None)


class MmrDiversityTest(_BaseMmrTest):
    """#7 — 같은 doc 의 chunk 3개 입력 → top-3 distinct doc_id 비율 ≥ 0.66.

    `chunk_ids` 가 prefix 로 doc 식별되도록 설계해 다양성 측정 가능.
    같은 doc 의 chunk 들은 거의 동일한 embedding (sim≈1) 을 부여 → MMR 이
    diversity term 으로 다른 doc 의 chunk 를 끌어올린다.
    """

    def test_same_doc_chunks_get_diversified(self) -> None:
        from app.services import mmr

        # 같은 doc-A 청크 3개는 거의 동일한 embedding (sim ≈ 1) → MMR 이 분산.
        # 다른 doc-B / doc-C 청크는 직교 → diversity term 0.
        same_a = [1.0, 0.001, 0.0, 0.0]
        embeddings = {
            "a-1": same_a,
            "a-2": [v + 0.0005 for v in same_a],
            "a-3": [v + 0.001 for v in same_a],
            "b-1": [0.0, 1.0, 0.0, 0.0],
            "c-1": [0.0, 0.0, 1.0, 0.0],
        }
        # relevance 만 보면 a-* 가 top 3 — pure relevance 라면 distinct doc=1.
        relevance = {
            "a-1": 0.95,
            "a-2": 0.93,
            "a-3": 0.91,
            "b-1": 0.7,
            "c-1": 0.6,
        }

        selected = mmr.rerank(
            list(relevance.keys()),
            relevance=relevance,
            embeddings_by_id=embeddings,
            top_k=3,
            lambda_=0.5,  # diversity 가산 강하게.
        )

        self.assertEqual(len(selected), 3)
        prefixes = {cid.split("-")[0] for cid in selected}
        ratio = len(prefixes) / len(selected)
        self.assertGreaterEqual(ratio, 0.66)


class MmrLambdaOneIsRelevanceOnlyTest(_BaseMmrTest):
    """λ=1.0 → diversity term 0 → pure relevance 정렬 (회귀 가드)."""

    def test_lambda_one_returns_relevance_order(self) -> None:
        from app.services import mmr

        embeddings = _orthogonal_embeddings()
        relevance = {"doc-a": 0.3, "doc-b": 0.9, "doc-c": 0.5}
        selected = mmr.rerank(
            list(relevance.keys()),
            relevance=relevance,
            embeddings_by_id=embeddings,
            top_k=3,
            lambda_=1.0,
        )
        # 순수 relevance 정렬 — b > c > a.
        self.assertEqual(selected, ["doc-b", "doc-c", "doc-a"])


class MmrEnvDisableTest(_BaseMmrTest):
    """`is_disabled()` / `resolve_lambda()` ENV 처리 — 호출자 진입 가드용."""

    def test_disable_env_and_lambda_resolution(self) -> None:
        from app.services import mmr

        # default — disabled=False, lambda=0.7
        self.assertFalse(mmr.is_disabled())
        self.assertAlmostEqual(mmr.resolve_lambda(), 0.7, places=4)

        # ENV ON
        os.environ["JETRAG_MMR_DISABLE"] = "1"
        self.assertTrue(mmr.is_disabled())

        # 잘못된 lambda → default
        os.environ["JETRAG_MMR_LAMBDA"] = "not-a-number"
        self.assertAlmostEqual(mmr.resolve_lambda(), 0.7, places=4)

        # 정상 lambda
        os.environ["JETRAG_MMR_LAMBDA"] = "0.4"
        self.assertAlmostEqual(mmr.resolve_lambda(), 0.4, places=4)

        # 범위 밖 → default
        os.environ["JETRAG_MMR_LAMBDA"] = "1.5"
        self.assertAlmostEqual(mmr.resolve_lambda(), 0.7, places=4)


if __name__ == "__main__":
    unittest.main()
