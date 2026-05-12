"""S4-B 후속 (2026-05-12) — `embed_query` 영구 캐시(embed_query_cache, 마이그 016) 단위 테스트.

검증 포인트
- helper `embed_query_cache`:
    - lookup hit → list[float] 복원 / miss → None / dim 불일치 row → None
    - upsert → supabase upsert(on_conflict, ignore_duplicates) 호출 + row 형식
    - DB 실패 graceful (lookup None / upsert no-raise) + 첫 1회만 warn
    - ENV "0" 시 DB 접근 0
- `BGEM3HFEmbeddingProvider.embed_query` 영구 캐시 레이어:
    - miss → HF 호출 → upsert 호출 (best-effort)
    - persistent hit → HF 호출 0 + 저장값 반환 + LRU 채워짐
    - 재현성 — 1회차 HF 반환 벡터 캐시 후, 2회차 HF mock 을 다른 값으로 바꿔도
      `embed_query(같은 text)` 가 1회차 캐시 값 반환 + HF mock call count 미증가
    - graceful — lookup/upsert 예외 시 HF 직호출 정상
    - ENV "0" 우회 — 영구 캐시 read/write 0, 기존 LRU 동작과 동일
    - NFC/NFD 혼용 → 같은 캐시 키

stdlib unittest + mock only — Supabase 의존성 0 (CLAUDE.md "의존성 추가 금지").
실행: `uv run python -m unittest tests.test_embed_query_cache`
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
import unittest
from unittest.mock import MagicMock, patch

import httpx

# tests/__init__.py 가 JETRAG_EMBED_QUERY_CACHE="0" 으로 강제 — 영구 캐시 검증 테스트는
# 자기 안에서 "1" 로 override + supabase mock 으로 격리, tearDown 에서 "0" 복원.
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")


_HF_URL = (
    "https://router.huggingface.co/hf-inference/"
    "models/BAAI/bge-m3/pipeline/feature-extraction"
)


def _dense_response(seed: float = 0.1) -> httpx.Response:
    """1024 dim float vector 응답 — `_parse_single_response` 호환."""
    request = httpx.Request("POST", _HF_URL)
    vec = [seed] * 1024
    return httpx.Response(200, request=request, content=json.dumps(vec).encode("utf-8"))


def _sha256_nfc(text: str) -> str:
    return hashlib.sha256(unicodedata.normalize("NFC", text.strip()).encode("utf-8")).hexdigest()


# ============================================================
# helper 모듈 — embed_query_cache.lookup / upsert
# ============================================================


class HelperEnvToggleTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.services import embed_query_cache

        embed_query_cache._reset_first_warn_for_test()

    def test_disabled_via_env_skips_db(self) -> None:
        from app.services import embed_query_cache

        os.environ["JETRAG_EMBED_QUERY_CACHE"] = "0"
        try:
            client = MagicMock()
            with patch("app.db.get_supabase_client", return_value=client):
                self.assertIsNone(embed_query_cache.lookup("a" * 64, "BAAI/bge-m3"))
                embed_query_cache.upsert("a" * 64, "BAAI/bge-m3", 1024, [0.1] * 1024)
            client.table.assert_not_called()
        finally:
            os.environ["JETRAG_EMBED_QUERY_CACHE"] = "0"

    def test_enabled_default_when_unset(self) -> None:
        from app.services import embed_query_cache

        os.environ.pop("JETRAG_EMBED_QUERY_CACHE", None)
        try:
            self.assertTrue(embed_query_cache.is_enabled())
        finally:
            os.environ["JETRAG_EMBED_QUERY_CACHE"] = "0"


class HelperLookupTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.services import embed_query_cache

        embed_query_cache._reset_first_warn_for_test()
        os.environ["JETRAG_EMBED_QUERY_CACHE"] = "1"

    def tearDown(self) -> None:
        os.environ["JETRAG_EMBED_QUERY_CACHE"] = "0"

    def _chain(self, client: MagicMock):
        return (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .eq.return_value
            .limit.return_value
        )

    def test_lookup_hit_returns_vector(self) -> None:
        from app.services import embed_query_cache

        client = MagicMock()
        self._chain(client).execute.return_value.data = [
            {"vector": [0.25] * 1024, "dim": 1024}
        ]
        with patch("app.db.get_supabase_client", return_value=client):
            vec = embed_query_cache.lookup("a" * 64, "BAAI/bge-m3")
        self.assertIsNotNone(vec)
        self.assertEqual(len(vec), 1024)
        self.assertEqual(vec[0], 0.25)

    def test_lookup_miss_returns_none(self) -> None:
        from app.services import embed_query_cache

        client = MagicMock()
        self._chain(client).execute.return_value.data = []
        with patch("app.db.get_supabase_client", return_value=client):
            self.assertIsNone(embed_query_cache.lookup("a" * 64, "BAAI/bge-m3"))

    def test_lookup_dim_mismatch_row_ignored(self) -> None:
        """저장된 벡터 길이가 1024 아니면 무시 → None (호출자가 HF 재호출)."""
        from app.services import embed_query_cache

        client = MagicMock()
        self._chain(client).execute.return_value.data = [
            {"vector": [0.1, 0.2, 0.3], "dim": 3}
        ]
        with patch("app.db.get_supabase_client", return_value=client):
            self.assertIsNone(embed_query_cache.lookup("a" * 64, "BAAI/bge-m3"))

    def test_lookup_db_failure_graceful_first_warn(self) -> None:
        from app.services import embed_query_cache

        client = MagicMock()
        client.table.side_effect = RuntimeError('relation "embed_query_cache" does not exist')
        with patch("app.db.get_supabase_client", return_value=client):
            with self.assertLogs("app.services.embed_query_cache", level="WARNING") as cm:
                self.assertIsNone(embed_query_cache.lookup("a" * 64, "BAAI/bge-m3"))
            # 두 번째는 debug — warn 추가 안 됨.
            self.assertIsNone(embed_query_cache.lookup("a" * 64, "BAAI/bge-m3"))
        self.assertEqual(len(cm.output), 1)
        self.assertIn("016", cm.output[0])


class HelperUpsertTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.services import embed_query_cache

        embed_query_cache._reset_first_warn_for_test()
        os.environ["JETRAG_EMBED_QUERY_CACHE"] = "1"

    def tearDown(self) -> None:
        os.environ["JETRAG_EMBED_QUERY_CACHE"] = "0"

    def test_upsert_calls_supabase_with_correct_row(self) -> None:
        from app.services import embed_query_cache

        client = MagicMock()
        vec = [0.5] * 1024
        with patch("app.db.get_supabase_client", return_value=client):
            embed_query_cache.upsert("b" * 64, "BAAI/bge-m3", 1024, vec)

        client.table.assert_called_with("embed_query_cache")
        args = client.table.return_value.upsert.call_args
        row = args.args[0]
        self.assertEqual(row["text_sha256"], "b" * 64)
        self.assertEqual(row["model_id"], "BAAI/bge-m3")
        self.assertEqual(row["dim"], 1024)
        self.assertEqual(len(row["vector"]), 1024)
        self.assertEqual(row["vector"][0], 0.5)
        self.assertEqual(args.kwargs.get("on_conflict"), "text_sha256,model_id")
        self.assertTrue(args.kwargs.get("ignore_duplicates"))

    def test_upsert_db_failure_graceful_no_raise(self) -> None:
        from app.services import embed_query_cache

        client = MagicMock()
        client.table.side_effect = RuntimeError("relation does not exist")
        with patch("app.db.get_supabase_client", return_value=client):
            embed_query_cache.upsert("b" * 64, "BAAI/bge-m3", 1024, [0.1] * 1024)  # no raise

    def test_upsert_empty_vector_skipped(self) -> None:
        from app.services import embed_query_cache

        client = MagicMock()
        with patch("app.db.get_supabase_client", return_value=client):
            embed_query_cache.upsert("b" * 64, "BAAI/bge-m3", 1024, [])
        client.table.assert_not_called()


# ============================================================
# provider — embed_query 영구 캐시 레이어
# ============================================================


class ProviderPersistentCacheTest(unittest.TestCase):
    """`BGEM3HFEmbeddingProvider.embed_query` 의 영구 캐시 통합."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

    def setUp(self) -> None:
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider
        from app.services import embed_query_cache

        get_bgem3_provider.cache_clear()
        embed_query_cache._reset_first_warn_for_test()
        os.environ["JETRAG_EMBED_QUERY_CACHE"] = "1"

    def tearDown(self) -> None:
        os.environ["JETRAG_EMBED_QUERY_CACHE"] = "0"

    def test_miss_calls_hf_then_upserts(self) -> None:
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider
        from app.services import embed_query_cache

        provider = get_bgem3_provider()
        with patch.object(embed_query_cache, "lookup", return_value=None) as lk, \
             patch.object(embed_query_cache, "upsert") as up, \
             patch.object(provider._client, "post", return_value=_dense_response(0.3)) as post:
            vec = provider.embed_query("미스 쿼리")

        self.assertEqual(len(vec), 1024)
        self.assertFalse(provider._last_cache_hit)
        self.assertEqual(provider._last_cache_source, "miss")
        post.assert_called_once()
        lk.assert_called_once()
        up.assert_called_once()
        # upsert 인자 — (text_sha256, model_id, dim, vector)
        a = up.call_args.args
        self.assertEqual(a[0], _sha256_nfc("미스 쿼리"))
        self.assertEqual(a[1], "BAAI/bge-m3")
        self.assertEqual(a[2], 1024)
        self.assertEqual(len(a[3]), 1024)

    def test_persistent_hit_skips_hf_and_fills_lru(self) -> None:
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider
        from app.services import embed_query_cache

        provider = get_bgem3_provider()
        provider.clear_embed_cache()
        canonical = [0.7] * 1024
        with patch.object(embed_query_cache, "lookup", return_value=list(canonical)) as lk, \
             patch.object(embed_query_cache, "upsert") as up, \
             patch.object(provider._client, "post", side_effect=AssertionError("HF 호출 금지")) as post:
            vec1 = provider.embed_query("히트 쿼리")
            self.assertTrue(provider._last_cache_hit)
            self.assertEqual(provider._last_cache_source, "persistent")
            # 2회차 — LRU 에서 (lookup 호출 안 됨)
            vec2 = provider.embed_query("히트 쿼리")
            self.assertEqual(provider._last_cache_source, "lru")

        self.assertEqual(vec1, canonical)
        self.assertEqual(vec2, canonical)
        post.assert_not_called()
        up.assert_not_called()
        lk.assert_called_once()  # 2회차는 LRU hit 이라 lookup 미호출

    def test_reproducibility_second_hf_value_change_ignored(self) -> None:
        """DoD (c-1) — 1회차 HF 벡터를 캐시(LRU)한 뒤, 2회차 HF mock 을 다른 값으로 바꿔도
        같은 text 의 `embed_query` 는 1회차 값 반환 + HF call count 미증가.
        (영구 캐시 upsert 는 mock — 1회차에만 호출. LRU 가 2회차를 처리.)
        """
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider
        from app.services import embed_query_cache

        provider = get_bgem3_provider()
        provider.clear_embed_cache()
        with patch.object(embed_query_cache, "lookup", return_value=None), \
             patch.object(embed_query_cache, "upsert"):
            with patch.object(provider._client, "post", return_value=_dense_response(0.11)) as post:
                first = provider.embed_query("재현성 쿼리")
            # HF mock 반환을 다른 값으로 — 그러나 LRU hit 이라 호출 안 됨.
            with patch.object(provider._client, "post", return_value=_dense_response(0.99)) as post2:
                second = provider.embed_query("재현성 쿼리")

        self.assertEqual(first, second)
        self.assertEqual(first[0], 0.11)
        post.assert_called_once()
        post2.assert_not_called()

    def test_reproducibility_via_persistent_after_lru_cleared(self) -> None:
        """LRU 가 비워져도(프로세스 재시작 모사) 영구 캐시 hit → 같은 canonical 벡터.

        1회차: lookup miss → HF(0.42) → upsert 로 canonical 저장. LRU clear (재시작 모사).
        2회차: lookup 이 저장된 canonical(0.42) 반환 → HF mock 을 0.99 로 바꿔도 0.42 유지.
        """
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider
        from app.services import embed_query_cache

        provider = get_bgem3_provider()
        provider.clear_embed_cache()

        store: dict[str, list[float]] = {}

        def fake_lookup(sha: str, model: str):
            return list(store[sha]) if sha in store else None

        def fake_upsert(sha: str, model: str, dim: int, vec: list[float]) -> None:
            store.setdefault(sha, list(vec))

        with patch.object(embed_query_cache, "lookup", side_effect=fake_lookup), \
             patch.object(embed_query_cache, "upsert", side_effect=fake_upsert):
            with patch.object(provider._client, "post", return_value=_dense_response(0.42)) as p1:
                v1 = provider.embed_query("영구 재현성")
            provider.clear_embed_cache()  # 프로세스 재시작 모사
            with patch.object(provider._client, "post", return_value=_dense_response(0.99)) as p2:
                v2 = provider.embed_query("영구 재현성")

        self.assertEqual(v1, v2)
        self.assertEqual(v1[0], 0.42)
        p1.assert_called_once()
        p2.assert_not_called()

    def test_graceful_lookup_and_upsert_exception_falls_back_to_hf(self) -> None:
        """DoD (d) — 영구 캐시 lookup/upsert 가 예외를 던져도 `embed_query` 는 HF 직호출로
        정상 동작 (provider 가 belt-and-suspenders 로 흡수). 검색 가용성 > 결정성.
        """
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider
        from app.services import embed_query_cache

        provider = get_bgem3_provider()
        provider.clear_embed_cache()
        with patch.object(embed_query_cache, "lookup", side_effect=RuntimeError("lookup boom")), \
             patch.object(embed_query_cache, "upsert", side_effect=RuntimeError("upsert boom")):
            with patch.object(provider._client, "post", return_value=_dense_response(0.2)) as post:
                vec = provider.embed_query("graceful 케이스")

        self.assertEqual(len(vec), 1024)
        self.assertEqual(vec[0], 0.2)
        self.assertFalse(provider._last_cache_hit)
        post.assert_called_once()

    def test_env_off_bypasses_persistent_cache(self) -> None:
        """JETRAG_EMBED_QUERY_CACHE=0 — 영구 캐시 read/write 0, 기존 LRU 동작 유지."""
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider
        from app.services import embed_query_cache

        os.environ["JETRAG_EMBED_QUERY_CACHE"] = "0"
        provider = get_bgem3_provider()
        provider.clear_embed_cache()
        try:
            # helper 의 is_enabled() 가 False → lookup/upsert 즉시 return (DB 미접근).
            client = MagicMock()
            with patch("app.db.get_supabase_client", return_value=client):
                with patch.object(provider._client, "post", return_value=_dense_response(0.4)) as post:
                    v1 = provider.embed_query("env off")
                    self.assertFalse(provider._last_cache_hit)
                    v2 = provider.embed_query("env off")  # LRU hit
                    self.assertTrue(provider._last_cache_hit)
                    self.assertEqual(provider._last_cache_source, "lru")
            self.assertEqual(v1, v2)
            self.assertEqual(post.call_count, 1)
            client.table.assert_not_called()  # 영구 캐시 우회
        finally:
            os.environ["JETRAG_EMBED_QUERY_CACHE"] = "0"

    def test_nfc_nfd_same_cache_key(self) -> None:
        """NFC / NFD 혼용 query → 같은 영구 캐시 키 (text_sha256)."""
        from app.adapters.impl.bgem3_hf_embedding import BGEM3HFEmbeddingProvider

        # "한국어" 의 NFD (자모 분리) vs NFC (완성형)
        nfc = unicodedata.normalize("NFC", "한국어 쿼리")
        nfd = unicodedata.normalize("NFD", "한국어 쿼리")
        self.assertNotEqual(nfc, nfd)  # 바이트 수준으로 다름
        k_nfc = BGEM3HFEmbeddingProvider._cache_key(nfc)
        k_nfd = BGEM3HFEmbeddingProvider._cache_key("  " + nfd + "  ")  # strip 도 확인
        self.assertEqual(k_nfc, k_nfd)
        self.assertEqual(k_nfc[0], _sha256_nfc("한국어 쿼리"))
        self.assertEqual(k_nfc[1], "BAAI/bge-m3")


if __name__ == "__main__":
    unittest.main()
