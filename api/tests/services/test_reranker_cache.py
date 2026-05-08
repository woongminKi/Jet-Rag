"""S3 D4 — `app.services.reranker_cache` 단위 테스트 (planner v0.1 §G #1~#4).

검증 범위
---------
1. cache hit — 동일 (query, chunk_ids) 2회 호출 시 두 번째 lookup hit.
2. LRU 500 초과 → oldest evict.
3. cache key — chunk_ids 순서 무관 (sorted) 같은 키.
4. ``JETRAG_RERANKER_CACHE_DISABLE=1`` → cache bypass (lookup/store no-op).

stdlib unittest only — 의존성 추가 0.
"""

from __future__ import annotations

import os
import unittest

# 환경 변수 stub — 다른 테스트와 동일 패턴 (HF / Gemini).
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")
os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"


class _BaseRerankerCacheTest(unittest.TestCase):
    """공통 setup — cache reset + ENV 정리 (다른 테스트의 잔존 ENV 제거)."""

    def setUp(self) -> None:
        from app.services import reranker_cache

        reranker_cache._reset_for_test()
        os.environ.pop("JETRAG_RERANKER_CACHE_DISABLE", None)

    def tearDown(self) -> None:
        os.environ.pop("JETRAG_RERANKER_CACHE_DISABLE", None)


class CacheHitTest(_BaseRerankerCacheTest):
    """#1 — 동일 (query, chunk_ids) 2회 lookup → 두 번째도 hit, 같은 score 반환."""

    def test_second_lookup_hits_with_same_scores(self) -> None:
        from app.services import reranker_cache

        query = "전기차 보조금"
        chunk_ids = ["c-1", "c-2", "c-3"]
        scores = {"c-1": 0.91, "c-2": 0.42, "c-3": 0.55}

        # 첫 store 후 lookup hit.
        self.assertIsNone(reranker_cache.lookup(query, chunk_ids))
        reranker_cache.store(query, chunk_ids, scores)

        first_hit = reranker_cache.lookup(query, chunk_ids)
        self.assertEqual(first_hit, scores)

        # 두 번째 lookup 도 hit — store 추가 호출 없이 동일 dict.
        second_hit = reranker_cache.lookup(query, chunk_ids)
        self.assertEqual(second_hit, scores)

        # mutation safety — 호출자가 결과 변경해도 다음 lookup 보존.
        assert first_hit is not None
        first_hit["c-1"] = 0.0
        third_hit = reranker_cache.lookup(query, chunk_ids)
        self.assertEqual(third_hit, scores)


class CacheLRUEvictTest(_BaseRerankerCacheTest):
    """#2 — 500 건 초과 시 oldest 제거 + 최신 hit 유지."""

    def test_overflow_evicts_oldest_entry(self) -> None:
        from app.services import reranker_cache

        # 첫 entry 저장 — eviction 후 lookup miss 검증 대상.
        reranker_cache.store("oldest", ["x"], {"x": 0.1})
        self.assertEqual(reranker_cache.lookup("oldest", ["x"]), {"x": 0.1})

        # cache_max_size 만큼 추가 — oldest 가 밀려나야 한다.
        for i in range(reranker_cache._CACHE_MAX_SIZE):
            reranker_cache.store(f"q-{i}", [f"c-{i}"], {f"c-{i}": float(i)})

        self.assertIsNone(reranker_cache.lookup("oldest", ["x"]))
        # 최신 entry 는 보존.
        last = reranker_cache._CACHE_MAX_SIZE - 1
        self.assertEqual(
            reranker_cache.lookup(f"q-{last}", [f"c-{last}"]),
            {f"c-{last}": float(last)},
        )


class CacheKeyOrderInvariantTest(_BaseRerankerCacheTest):
    """#3 — chunk_ids 순서 무관 (sorted) 같은 키 → 다른 순서로 lookup 도 hit."""

    def test_lookup_with_reversed_order_still_hits(self) -> None:
        from app.services import reranker_cache

        query = "공통 키"
        scores = {"a": 0.5, "b": 0.7, "c": 0.3}
        reranker_cache.store(query, ["a", "b", "c"], scores)

        # 역순 / 셔플 모두 같은 key → hit.
        self.assertEqual(reranker_cache.lookup(query, ["c", "b", "a"]), scores)
        self.assertEqual(reranker_cache.lookup(query, ["b", "a", "c"]), scores)


class CacheDisableEnvTest(_BaseRerankerCacheTest):
    """#4 — ``JETRAG_RERANKER_CACHE_DISABLE=1`` 시 lookup/store 모두 no-op."""

    def test_env_disable_bypasses_cache(self) -> None:
        from app.services import reranker_cache

        os.environ["JETRAG_RERANKER_CACHE_DISABLE"] = "1"

        # store no-op — lookup 은 항상 None.
        reranker_cache.store("q", ["c"], {"c": 0.9})
        self.assertIsNone(reranker_cache.lookup("q", ["c"]))

        # ENV 해제 후에는 정상 — 직전 store 가 no-op 였으므로 여전히 miss.
        os.environ.pop("JETRAG_RERANKER_CACHE_DISABLE", None)
        self.assertIsNone(reranker_cache.lookup("q", ["c"]))

        # 정상 store → hit.
        reranker_cache.store("q", ["c"], {"c": 0.9})
        self.assertEqual(reranker_cache.lookup("q", ["c"]), {"c": 0.9})


if __name__ == "__main__":
    unittest.main()
