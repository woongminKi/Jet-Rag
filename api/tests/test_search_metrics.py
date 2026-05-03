"""W3 Day 2 Phase 3 — `search_metrics` ring buffer 단위 테스트.

외부 의존성 0 (stdlib + app.services 만). Supabase env 없이도 실행됨.
"""

from __future__ import annotations

import os
import unittest

# W17 Day 4 — discover 시 tests/__init__.py 가 top-level-dir 미명시로 안 잡힐 때 보호.
os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"
os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"

from app.services import search_metrics


class SearchMetricsTest(unittest.TestCase):
    def setUp(self) -> None:
        # 모듈 레벨 ring 은 프로세스 전역 — 각 테스트 간 격리 위해 reset.
        search_metrics.reset()

    def tearDown(self) -> None:
        search_metrics.reset()

    def test_empty_ring_returns_none_percentiles(self) -> None:
        slo = search_metrics.get_search_slo()
        self.assertEqual(slo["sample_count"], 0)
        self.assertIsNone(slo["p50_ms"])
        self.assertIsNone(slo["p95_ms"])
        self.assertIsNone(slo["avg_dense_hits"])
        self.assertEqual(slo["fallback_count"], 0)
        # breakdown 은 항상 3개 키 노출 (0 이라도) — 프론트가 키 존재 가정 가능.
        self.assertEqual(
            set(slo["fallback_breakdown"].keys()),
            {"transient_5xx", "permanent_4xx", "none"},
        )
        self.assertEqual(slo["fallback_breakdown"]["none"], 0)

    def test_p50_p95_nearest_rank(self) -> None:
        # 100, 200, ..., 1000 (10건). p50 idx=int(0.5*9)=4 → 500, p95 idx=int(0.95*9)=8 → 900.
        for ms in (100, 200, 300, 400, 500, 600, 700, 800, 900, 1000):
            search_metrics.record_search(
                took_ms=ms,
                dense_hits=5,
                sparse_hits=3,
                fused=8,
                has_dense=True,
                fallback_reason=None,
            )
        slo = search_metrics.get_search_slo()
        self.assertEqual(slo["sample_count"], 10)
        self.assertEqual(slo["p50_ms"], 500)
        self.assertEqual(slo["p95_ms"], 900)
        self.assertAlmostEqual(slo["avg_dense_hits"], 5.0)
        self.assertAlmostEqual(slo["avg_sparse_hits"], 3.0)
        self.assertAlmostEqual(slo["avg_fused"], 8.0)
        self.assertEqual(slo["fallback_count"], 0)
        self.assertEqual(slo["fallback_breakdown"]["none"], 10)
        self.assertEqual(slo["fallback_breakdown"]["transient_5xx"], 0)

    def test_fallback_breakdown_counts(self) -> None:
        # 정상 2건 + transient 1건 + permanent 1건
        for _ in range(2):
            search_metrics.record_search(
                took_ms=100, dense_hits=1, sparse_hits=1, fused=1,
                has_dense=True, fallback_reason=None,
            )
        search_metrics.record_search(
            took_ms=500, dense_hits=0, sparse_hits=2, fused=2,
            has_dense=False, fallback_reason="transient_5xx",
        )
        search_metrics.record_search(
            took_ms=50, dense_hits=0, sparse_hits=0, fused=0,
            has_dense=False, fallback_reason="permanent_4xx",
        )
        slo = search_metrics.get_search_slo()
        self.assertEqual(slo["sample_count"], 4)
        self.assertEqual(slo["fallback_count"], 2)
        self.assertEqual(slo["fallback_breakdown"]["transient_5xx"], 1)
        self.assertEqual(slo["fallback_breakdown"]["permanent_4xx"], 1)
        self.assertEqual(slo["fallback_breakdown"]["none"], 2)

    def test_ring_overflow_keeps_only_recent(self) -> None:
        # maxlen=500 이므로 510건 record 후 마지막 500건만 유지되는지 — 그 중 가장 오래된 took_ms 가 11.
        for ms in range(1, 511):
            search_metrics.record_search(
                took_ms=ms, dense_hits=0, sparse_hits=0, fused=0,
                has_dense=True, fallback_reason=None,
            )
        slo = search_metrics.get_search_slo()
        self.assertEqual(slo["sample_count"], 500)
        # took_ms 11..510 → p50 idx=int(0.5*499)=249 → 11+249=260, p95 idx=int(0.95*499)=474 → 11+474=485
        self.assertEqual(slo["p50_ms"], 260)
        self.assertEqual(slo["p95_ms"], 485)


class ByModeSplitTest(unittest.TestCase):
    """W14 Day 3 (한계 #77) — mode 별 분리 측정 (hybrid/dense/sparse)."""

    def setUp(self) -> None:
        search_metrics.reset()

    def tearDown(self) -> None:
        search_metrics.reset()

    def test_by_mode_splits_samples(self) -> None:
        search_metrics.record_search(
            took_ms=100, dense_hits=10, sparse_hits=5, fused=10,
            has_dense=True, fallback_reason=None, mode="hybrid",
        )
        search_metrics.record_search(
            took_ms=200, dense_hits=10, sparse_hits=0, fused=10,
            has_dense=True, fallback_reason=None, mode="dense",
        )
        search_metrics.record_search(
            took_ms=300, dense_hits=0, sparse_hits=5, fused=5,
            has_dense=False, fallback_reason=None, mode="sparse",
        )

        slo = search_metrics.get_search_slo()
        self.assertEqual(slo["sample_count"], 3)
        self.assertEqual(slo["p50_ms"], 200)

        self.assertIn("by_mode", slo)
        self.assertEqual(
            set(slo["by_mode"].keys()), {"hybrid", "dense", "sparse"}
        )
        self.assertEqual(slo["by_mode"]["hybrid"]["p50_ms"], 100)
        self.assertEqual(slo["by_mode"]["dense"]["p50_ms"], 200)
        self.assertEqual(slo["by_mode"]["sparse"]["p50_ms"], 300)
        self.assertEqual(slo["by_mode"]["hybrid"]["sample_count"], 1)

    def test_invalid_mode_falls_back_to_hybrid(self) -> None:
        search_metrics.record_search(
            took_ms=100, dense_hits=1, sparse_hits=1, fused=1,
            has_dense=True, fallback_reason=None, mode="bogus",
        )
        slo = search_metrics.get_search_slo()
        self.assertEqual(slo["by_mode"]["hybrid"]["sample_count"], 1)
        self.assertEqual(slo["by_mode"]["dense"]["sample_count"], 0)
        self.assertEqual(slo["by_mode"]["sparse"]["sample_count"], 0)

    def test_default_mode_is_hybrid(self) -> None:
        """mode 인자 미전달 시 hybrid 기본 — backward compat."""
        search_metrics.record_search(
            took_ms=50, dense_hits=1, sparse_hits=1, fused=1,
            has_dense=True, fallback_reason=None,
        )
        slo = search_metrics.get_search_slo()
        self.assertEqual(slo["by_mode"]["hybrid"]["sample_count"], 1)

    def test_zero_samples_per_mode_renders_nulls(self) -> None:
        """비어있는 mode 의 by_mode entry 도 정상 schema."""
        search_metrics.record_search(
            took_ms=100, dense_hits=1, sparse_hits=1, fused=1,
            has_dense=True, fallback_reason=None, mode="hybrid",
        )
        slo = search_metrics.get_search_slo()
        # dense / sparse 는 sample 0
        self.assertEqual(slo["by_mode"]["dense"]["sample_count"], 0)
        self.assertIsNone(slo["by_mode"]["dense"]["p50_ms"])
        self.assertIsNone(slo["by_mode"]["sparse"]["cache_hit_rate"])


class QueryTextHashTest(unittest.TestCase):
    """W18 Day 2 한계 #87 — env JET_RAG_QUERY_TEXT_HASH 토글."""

    def setUp(self) -> None:
        import os as _os
        self._original = _os.environ.pop("JET_RAG_QUERY_TEXT_HASH", None)

    def tearDown(self) -> None:
        import os as _os
        if self._original is None:
            _os.environ.pop("JET_RAG_QUERY_TEXT_HASH", None)
        else:
            _os.environ["JET_RAG_QUERY_TEXT_HASH"] = self._original

    def test_default_returns_plaintext(self) -> None:
        self.assertEqual(
            search_metrics._maybe_hash_query("안녕하세요"), "안녕하세요"
        )

    def test_hash_env_returns_sha256_hex(self) -> None:
        import os as _os
        _os.environ["JET_RAG_QUERY_TEXT_HASH"] = "1"
        result = search_metrics._maybe_hash_query("안녕하세요")
        # SHA256 hex = 64자, 16진수
        self.assertEqual(len(result), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in result))
        # 동일 입력 동일 hash (deterministic)
        self.assertEqual(result, search_metrics._maybe_hash_query("안녕하세요"))
        # 다른 입력 다른 hash
        self.assertNotEqual(result, search_metrics._maybe_hash_query("hello"))

    def test_none_passes_through(self) -> None:
        import os as _os
        _os.environ["JET_RAG_QUERY_TEXT_HASH"] = "1"
        self.assertIsNone(search_metrics._maybe_hash_query(None))

    def test_empty_string_passes_through(self) -> None:
        import os as _os
        _os.environ["JET_RAG_QUERY_TEXT_HASH"] = "1"
        self.assertEqual(search_metrics._maybe_hash_query(""), "")


class SearchMetricsFirstWarnPatternTest(unittest.TestCase):
    """W17 Day 3 한계 #85 — search_metrics _persist_to_db 첫 실패만 warn."""

    def setUp(self) -> None:
        search_metrics.reset()  # _first_persist_warn_logged 도 False 로 reset

    def test_first_failure_logs_warning(self) -> None:
        from unittest.mock import patch
        import os as _os
        import datetime as _dt

        _os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "1"
        _os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"
        try:
            with patch(
                "app.db.get_supabase_client",
                side_effect=RuntimeError("DB down"),
            ), self.assertLogs("app.services.search_metrics", level="WARNING") as cm:
                search_metrics._persist_to_db(
                    recorded_at=_dt.datetime.now(_dt.timezone.utc),
                    event={
                        "took_ms": 100, "dense_hits": 1, "sparse_hits": 1,
                        "fused": 1, "has_dense": True, "fallback_reason": None,
                        "embed_cache_hit": False, "mode": "hybrid",
                    },
                    query_text=None,
                )
            self.assertEqual(len(cm.records), 1)
            self.assertIn("첫 실패", cm.records[0].getMessage())
        finally:
            _os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"


if __name__ == "__main__":
    unittest.main()
