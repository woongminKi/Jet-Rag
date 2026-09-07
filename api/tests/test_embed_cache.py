"""W4-Q-3 — `BGEM3HFEmbeddingProvider.embed_query` LRU cache 단위 테스트.

설계 (work-log/2026-05-02 W4 스프린트 명세 v0.1.md §3.W4-Q-3):
    - cache 자료구조: `OrderedDict` + `threading.Lock` (stdlib only)
    - cache key: text 단독 (model_id 모듈 상수)
    - cache hit 시 HF API 호출 0회, `_last_cache_hit=True`
    - LRU eviction: maxsize 초과 시 가장 오래된 entry 제거
    - thread safety: ThreadPoolExecutor 4 worker × 100 호출 시 KeyError·race 무발생

stdlib `unittest` 만 사용 — 외부 의존성 0 (CLAUDE.md "의존성 추가 금지" 준수).
실행: `uv run python -m unittest tests.test_embed_cache`
"""

from __future__ import annotations

import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import httpx


def _make_dense_response(seed: float = 0.1) -> httpx.Response:
    """1024 dim float vector 응답 픽스처 — `_parse_single_response` 와 호환."""
    request = httpx.Request(
        "POST",
        "https://router.huggingface.co/hf-inference/models/BAAI/bge-m3/pipeline/feature-extraction",
    )
    vec = [seed] * 1024
    import json

    return httpx.Response(
        200, request=request, content=json.dumps(vec).encode("utf-8")
    )


class EmbedCacheBaseTest(unittest.TestCase):
    """공통 setUp — HF_API_TOKEN dummy 주입 + 싱글톤 격리 + **영구 캐시 차단**.

    `embed_query` 는 2단 캐시다: ① in-process LRU ② DB `embed_query_cache`.
    이 파일이 검증하는 건 ①뿐인데 ②를 막지 않으면 **단독 실행은 통과하고 전체 실행은
    실패한다** — 다른 테스트가 Supabase 자격증명을 올려놓으면 ②가 hit 을 내서
    `_last_cache_hit` 가 True 가 되기 때문이다(실측: `unittest discover` 에서만 4건 실패).

    그래서 ②를 setUp 에서 통째로 끊는다. 이 테스트의 관심사가 아니다.
    """

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

    def setUp(self) -> None:
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider

        get_bgem3_provider.cache_clear()

        # 영구 캐시(DB)는 항상 miss + write 무시 — LRU 만 남긴다.
        for target, repl in (("lookup", lambda *_a, **_k: None),
                             ("upsert", lambda *_a, **_k: None)):
            patcher = patch(f"app.services.embed_query_cache.{target}", repl)
            patcher.start()
            self.addCleanup(patcher.stop)


class CacheHitTest(EmbedCacheBaseTest):
    def test_repeated_query_skips_http_call(self) -> None:
        """같은 query 두 번 호출 — 두 번째는 HF API 호출 0회."""
        from app.adapters.impl.bgem3_hf_embedding import (
            get_bgem3_provider,
        )

        provider = get_bgem3_provider()

        # httpx.Client.post 를 mock — 첫 호출만 실제 응답 반환.
        with patch.object(
            provider._client, "post", return_value=_make_dense_response()
        ) as mock_post:
            vec1 = provider.embed_query("동일한 쿼리")
            self.assertFalse(provider._last_cache_hit, "첫 호출은 miss")
            vec2 = provider.embed_query("동일한 쿼리")
            self.assertTrue(provider._last_cache_hit, "두 번째 호출은 hit")

        # 두 결과 동일 (defensive copy 라 다른 객체이지만 값 같음).
        self.assertEqual(vec1, vec2)
        self.assertEqual(len(vec1), 1024)
        # HF API 호출은 1회만 (cache hit 시 skip).
        self.assertEqual(mock_post.call_count, 1)

    def test_defensive_copy_caller_mutation_does_not_leak(self) -> None:
        """caller 가 반환 vector 를 mutate 해도 cache 내부 보존."""
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider

        provider = get_bgem3_provider()
        with patch.object(
            provider._client, "post", return_value=_make_dense_response(seed=0.5)
        ):
            vec1 = provider.embed_query("mutation 테스트")
            vec1[0] = 999.0  # caller mutation
            vec2 = provider.embed_query("mutation 테스트")  # cache hit

        self.assertNotEqual(vec1[0], vec2[0])
        self.assertEqual(vec2[0], 0.5, "cache 내부는 원본 값 유지")


class CacheMissTest(EmbedCacheBaseTest):
    def test_different_queries_both_call_http(self) -> None:
        """서로 다른 두 query — 둘 다 cache miss, HF API 2회 호출."""
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider

        provider = get_bgem3_provider()
        with patch.object(
            provider._client, "post", return_value=_make_dense_response()
        ) as mock_post:
            provider.embed_query("query A")
            self.assertFalse(provider._last_cache_hit)
            provider.embed_query("query B")
            self.assertFalse(provider._last_cache_hit)

        self.assertEqual(mock_post.call_count, 2)


class CacheEvictionTest(EmbedCacheBaseTest):
    def test_lru_eviction_when_maxsize_exceeded(self) -> None:
        """maxsize=2 인 임시 instance 로 3건 호출 — 첫 query 가 evict 되어 재호출 시 miss."""
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider

        provider = get_bgem3_provider()
        provider.clear_embed_cache()
        provider._embed_cache_maxsize = 2  # 테스트용 축소

        try:
            with patch.object(
                provider._client, "post", return_value=_make_dense_response()
            ) as mock_post:
                provider.embed_query("Q1")  # cache=[Q1]
                provider.embed_query("Q2")  # cache=[Q1, Q2]
                provider.embed_query("Q3")  # cache=[Q2, Q3] — Q1 evicted
                # Q1 재호출 — miss 여야 함.
                provider.embed_query("Q1")
                self.assertFalse(
                    provider._last_cache_hit,
                    "Q1 은 evict 되었으므로 miss",
                )
                # Q3 는 여전히 hit.
                provider.embed_query("Q3")
                self.assertTrue(provider._last_cache_hit, "Q3 는 cache 잔존")

            # 호출 횟수: Q1, Q2, Q3, Q1 재 = 4회 (Q3 두 번째는 hit).
            self.assertEqual(mock_post.call_count, 4)
        finally:
            provider.clear_embed_cache()
            provider._embed_cache_maxsize = 512  # 원복

    def test_lru_move_to_end_keeps_recently_used(self) -> None:
        """maxsize=2, Q1 → Q2 → Q1 재호출 (MRU 갱신) → Q3 추가 시 Q2 가 evict 되어야."""
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider

        provider = get_bgem3_provider()
        provider.clear_embed_cache()
        provider._embed_cache_maxsize = 2

        try:
            with patch.object(
                provider._client, "post", return_value=_make_dense_response()
            ):
                provider.embed_query("Q1")  # cache=[Q1]
                provider.embed_query("Q2")  # cache=[Q1, Q2]
                provider.embed_query("Q1")  # hit, MRU=Q1 → cache=[Q2, Q1]
                self.assertTrue(provider._last_cache_hit)
                provider.embed_query("Q3")  # cache=[Q1, Q3] — Q2 evicted

                provider.embed_query("Q1")
                self.assertTrue(provider._last_cache_hit, "Q1 은 MRU 라 잔존")
                provider.embed_query("Q2")
                self.assertFalse(
                    provider._last_cache_hit, "Q2 는 evict 되어 miss"
                )
        finally:
            provider.clear_embed_cache()
            provider._embed_cache_maxsize = 512


class ThreadSafetyTest(EmbedCacheBaseTest):
    def test_concurrent_access_no_race_error(self) -> None:
        """ThreadPoolExecutor 4 worker × 100 호출 — KeyError·race 발생 안 함.

        OrderedDict 는 GIL 외 race 가 없어 단순 access 는 안전하지만,
        `move_to_end` + `popitem` 동시 진행은 race 가능. Lock 으로 보호 검증.

        W5 mock 보강 (W4 Day 2 P3 flaky 해소): patch 를 worker 안이 아닌 단일
        with 블록으로 래핑해 thread 간 patch race 차단. 이전엔 worker 별 patch 가
        thread 간섭 + 워밍업 전 실제 HTTP 호출 가능성 → 401 cold path race.
        """
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider

        provider = get_bgem3_provider()
        provider.clear_embed_cache()
        provider._embed_cache_maxsize = 8  # 작은 maxsize 로 eviction 빈발 유도

        errors: list[BaseException] = []
        lock = threading.Lock()

        def worker(idx: int) -> None:
            try:
                # 10 종 query 가 4 worker 각 100회 → 총 4000 호출, 빈번 hit + eviction.
                for i in range(100):
                    q = f"query-{i % 10}"
                    provider.embed_query(q)
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        try:
            # patch 를 단일 with 로 — worker 시작 전에 patch 적용, 모든 thread 가 mock 사용.
            with patch.object(
                provider._client,
                "post",
                return_value=_make_dense_response(seed=1.0),
            ):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = [pool.submit(worker, i) for i in range(4)]
                    for f in futures:
                        f.result()
        finally:
            provider.clear_embed_cache()
            provider._embed_cache_maxsize = 512

        self.assertEqual(
            errors, [], f"동시성 race 발생 — 첫 에러: {errors[:1]}"
        )
        # cache 는 maxsize 한도 내에 머물러야 함.
        self.assertLessEqual(len(provider._embed_cache), 8)


class SearchMetricsCacheHitFieldTest(unittest.TestCase):
    """`search_metrics.record_search` + `get_search_slo` 의 새 필드 노출."""

    def setUp(self) -> None:
        from app.services import search_metrics

        search_metrics.reset()

    def tearDown(self) -> None:
        from app.services import search_metrics

        search_metrics.reset()

    def test_cache_hit_count_and_rate(self) -> None:
        """3건 hit + 2건 miss → cache_hit_count=3, cache_hit_rate=0.6."""
        from app.services import search_metrics

        for _ in range(3):
            search_metrics.record_search(
                took_ms=50,
                dense_hits=5,
                sparse_hits=0,
                fused=5,
                has_dense=True,
                fallback_reason=None,
                embed_cache_hit=True,
            )
        for _ in range(2):
            search_metrics.record_search(
                took_ms=600,
                dense_hits=5,
                sparse_hits=0,
                fused=5,
                has_dense=True,
                fallback_reason=None,
                embed_cache_hit=False,
            )

        slo = search_metrics.get_search_slo()
        self.assertEqual(slo["cache_hit_count"], 3)
        self.assertAlmostEqual(slo["cache_hit_rate"], 0.6)

    def test_empty_ring_cache_fields(self) -> None:
        """ring 비었을 때 cache_hit_count=0, cache_hit_rate=None."""
        from app.services import search_metrics

        slo = search_metrics.get_search_slo()
        self.assertEqual(slo["cache_hit_count"], 0)
        self.assertIsNone(slo["cache_hit_rate"])

    def test_default_arg_backward_compat(self) -> None:
        """`embed_cache_hit` 인자 생략 시 default False 로 적재."""
        from app.services import search_metrics

        # 기존 호출 시그니처 (embed_cache_hit 미지정).
        search_metrics.record_search(
            took_ms=100,
            dense_hits=1,
            sparse_hits=1,
            fused=1,
            has_dense=True,
            fallback_reason=None,
        )
        slo = search_metrics.get_search_slo()
        self.assertEqual(slo["cache_hit_count"], 0)
        self.assertEqual(slo["sample_count"], 1)


if __name__ == "__main__":
    unittest.main()
