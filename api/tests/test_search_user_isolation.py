"""1-1 회귀 테스트 — 다른 사용자의 chunk 가 검색 결과에 섞이지 않는지 검증.

배경 (qa 리포트 §1-1):
    search.py 가 RPC `search_hybrid_rrf` 의 `user_id_arg` 에 `str(user_id)` 를 전달.
    PostgREST 의 str→UUID 캐스트가 정상 동작하는지 단일-사용자 환경에선 검증 불가.
    캐스트 실패 시 NULL 로 떨어지면 모든 사용자 doc 가 결과에 섞임 → W5+ RLS 우회.

검증 절차:
    1. 2번 user_id (synthetic) 로 documents 1건 + chunks 3건 인서트
    2. 1번 user (default) 의 컨텍스트로 `search(q=...)` 호출
    3. 응답 items 에 2번 user 의 doc_id 가 0건인지 assert
    4. cleanup — synthetic doc/chunks 삭제 (addClassCleanup 보장)

W3 Day 2 Phase 3 추가 케이스:
    - C-1: RPC 직접 호출 — `user_id_arg=default` 시 synthetic chunks 가 0건
    - E-4: `_sparse_only_fallback` 이 deleted_at IS NOT NULL 문서를 제외

HF API 호출 회피 — `get_bgem3_provider` 를 monkeypatch 로 dummy 1024-dim zeros 반환.

stdlib `unittest` 만 사용 — 외부 의존성 0.
실행: `python -m unittest tests.test_search_user_isolation`
"""

from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import MagicMock, patch


# Supabase 환경이 없으면 전체 모듈 skip — CI/오프라인에서 무해.
# config.py 가 .env 를 load_dotenv 로 자동 로드하므로 settings 경유로 확인.
def _has_supabase_env() -> bool:
    try:
        from app.config import get_settings

        s = get_settings()
        return bool(s.supabase_url and (s.supabase_service_role_key or s.supabase_key))
    except Exception:  # noqa: BLE001
        return False


_SYNTHETIC_USER_ID = "00000000-0000-0000-0000-000000000099"
_DENSE_DIM = 1024


@unittest.skipUnless(
    _has_supabase_env(), "Supabase env (SUPABASE_URL + KEY) 미설정 — skip"
)
class SearchUserIsolationTest(unittest.TestCase):
    """RPC 가 user_id_arg 에 따라 다른 사용자 chunks 를 격리하는지 회귀 검증."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

        from app.config import get_settings
        from app.db import get_supabase_client

        cls.client = get_supabase_client()
        cls.default_user_id = get_settings().default_user_id

        # 격리 보장 — synthetic user 가 default user 와 같으면 테스트 의미 없음.
        assert _SYNTHETIC_USER_ID != cls.default_user_id, (
            "synthetic user id 가 default user id 와 같아선 안 됨"
        )

        # 고유 마커 텍스트 — 다른 doc 의 본문과 충돌 않도록 nonce 포함.
        cls.nonce = uuid.uuid4().hex[:12]
        cls.marker_phrase = f"신테틱격리테스트{cls.nonce}"

        # synthetic doc + 3 chunks 인서트.
        # chunks.dense_vec 는 NULL 로 두어 dense path 매칭 회피 (sparse 만으로 검증).
        cls.synthetic_doc_id = str(uuid.uuid4())
        cls._inserted_chunk_ids: list[str] = []

        # cleanup 을 setUpClass 진입 직후 등록 — insert 도중 예외나도 정리 보장.
        # (C-3 통합) `addClassCleanup` 으로 마이그레이션 — 기존 try/finally tearDownClass 보다 안전.
        cls.addClassCleanup(_cleanup_synthetic_doc, cls.client, cls.synthetic_doc_id)

        # 001_init.sql 의 documents 스키마에 맞춤:
        # source_channel CHECK = ('drag-drop','os-share','clipboard','url','camera','api')
        # status 컬럼은 documents 에 없음 (ingest_jobs 전용).
        cls.client.table("documents").insert(
            {
                "id": cls.synthetic_doc_id,
                "user_id": _SYNTHETIC_USER_ID,
                "title": f"synthetic-isolation-{cls.nonce}",
                "doc_type": "txt",
                "source_channel": "api",
                "storage_path": f"synthetic/test/{cls.nonce}.txt",
                "size_bytes": 100,
                "sha256": uuid.uuid4().hex + uuid.uuid4().hex,  # 64 hex
                "content_type": "text/plain",
            }
        ).execute()

        for idx in range(3):
            chunk_id = str(uuid.uuid4())
            cls.client.table("chunks").insert(
                {
                    "id": chunk_id,
                    "doc_id": cls.synthetic_doc_id,
                    "chunk_idx": idx,
                    "text": f"{cls.marker_phrase} 청크 {idx} 본문 텍스트",
                }
            ).execute()
            cls._inserted_chunk_ids.append(chunk_id)

    def test_other_user_doc_does_not_appear_in_results(self) -> None:
        """1번 user 컨텍스트에서 검색 → 2번 user 의 doc_id 가 0건이어야 한다."""
        from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider
        from app.routers import search as search_module

        # HF API 비용 회피 — dummy zeros vector. RPC 의 dense path 는
        # dense_vec NULL 인 synthetic chunks 와 매칭 안 됨, sparse path 로만 후보됨.
        provider_mock = MagicMock()
        provider_mock.embed_query.return_value = [0.0] * _DENSE_DIM
        get_bgem3_provider.cache_clear()

        with patch.object(
            search_module, "get_bgem3_provider", return_value=provider_mock
        ):
            # 라우터를 직접 호출 시 FastAPI Query() default 가 그대로 들어와
            # 검증 오작동 — 모든 optional 인자를 명시 전달.
            resp = search_module.search(
                q=self.marker_phrase,
                limit=50,
                offset=0,
                tags=None,
                doc_type=None,
                from_date=None,
                to_date=None,
                doc_id=None,
                mode="hybrid",
            )

        synthetic_appearances = [
            item for item in resp.items if item.doc_id == self.synthetic_doc_id
        ]
        self.assertEqual(
            len(synthetic_appearances),
            0,
            f"다른 사용자({_SYNTHETIC_USER_ID}) 의 doc 가 검색 결과에 섞임 — "
            f"RPC user_id_arg 캐스트 실패 의심. items={resp.items}",
        )

    def test_synthetic_doc_is_visible_when_using_synthetic_user_id(self) -> None:
        """대조군 — synthetic user_id 로 RPC 호출 시 synthetic doc 가 매칭되어야 한다.

        만약 이 케이스에서도 0건이라면 데이터 인서트 자체 실패 또는 fts 백필 미적용
        등 환경 문제. 위 테스트의 0건이 RLS 격리 효과인지 환경 문제인지 구분.
        """
        from app.db import get_supabase_client

        client = get_supabase_client()
        rpc_resp = client.rpc(
            "search_hybrid_rrf",
            {
                "query_text": self.marker_phrase,
                "query_dense": [0.0] * _DENSE_DIM,
                "k_rrf": 60,
                "top_k": 50,
                "user_id_arg": _SYNTHETIC_USER_ID,
            },
        ).execute()
        rows = rpc_resp.data or []
        synthetic_chunks = [
            r for r in rows if r["doc_id"] == self.synthetic_doc_id
        ]
        self.assertGreater(
            len(synthetic_chunks),
            0,
            "대조군 실패 — synthetic user_id 로도 매칭 0건. "
            "fts 백필 미적용 또는 마이그레이션 003 미실행 의심.",
        )

    def test_rpc_returns_only_default_user_chunks_when_user_id_arg_provided(
        self,
    ) -> None:
        """C-1 — RPC 를 라우터 우회로 직접 호출. user_id_arg=default 시 synthetic 0건.

        본 케이스는 search.py 의 doc_id 화이트리스트 (4단계 documents JOIN) 에 의존하지 않고
        RPC SQL 의 `WHERE user_id = user_id_arg` 절 자체를 검증. PostgREST 의 str→UUID 캐스트
        실패 시 RPC 가 NULL 비교로 떨어져 모든 user 결과 반환되는 회귀 차단.
        """
        rpc_resp = self.client.rpc(
            "search_hybrid_rrf",
            {
                "query_text": self.marker_phrase,
                "query_dense": [0.0] * _DENSE_DIM,
                "k_rrf": 60,
                "top_k": 50,
                "user_id_arg": str(self.default_user_id),
            },
        ).execute()
        rows = rpc_resp.data or []
        synthetic_in_rpc = [
            r for r in rows if r.get("doc_id") == self.synthetic_doc_id
        ]
        self.assertEqual(
            len(synthetic_in_rpc),
            0,
            f"RPC 가 default user_id_arg 로 호출됐는데 synthetic doc chunks "
            f"{len(synthetic_in_rpc)}건 반환 — user_id_arg 캐스트 또는 SQL WHERE 절 회귀.",
        )


@unittest.skipUnless(
    _has_supabase_env(), "Supabase env (SUPABASE_URL + KEY) 미설정 — skip"
)
class SparseFallbackSoftDeleteTest(unittest.TestCase):
    """E-4 — `_sparse_only_fallback` 이 soft-deleted documents 의 chunks 를 노출하지 않는지.

    배경: search.py 의 dense path 는 RPC `search_hybrid_rrf` 의 SQL 에서 deleted_at IS NULL
    필터가 걸리지만, sparse-only fallback 은 PostgREST 의 inner join 으로 처리됨
    (`documents!inner(user_id, deleted_at)` + `is_("documents.deleted_at", "null")`).
    이 필터가 회귀 시 삭제된 문서의 본문이 검색에 노출되는 데이터 유출.
    """

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

        from app.config import get_settings
        from app.db import get_supabase_client

        cls.client = get_supabase_client()
        cls.default_user_id = get_settings().default_user_id

        cls.nonce = uuid.uuid4().hex[:12]
        cls.marker_phrase = f"소프트삭제테스트{cls.nonce}"
        cls.deleted_doc_id = str(uuid.uuid4())

        # 정리는 등록을 먼저 — insert 도중 예외 시에도 보장.
        cls.addClassCleanup(_cleanup_synthetic_doc, cls.client, cls.deleted_doc_id)

        # default user 의 doc + chunks — soft-delete 이전에 인서트.
        cls.client.table("documents").insert(
            {
                "id": cls.deleted_doc_id,
                "user_id": cls.default_user_id,
                "title": f"soft-deleted-{cls.nonce}",
                "doc_type": "txt",
                "source_channel": "api",
                "storage_path": f"synthetic/deleted/{cls.nonce}.txt",
                "size_bytes": 100,
                "sha256": uuid.uuid4().hex + uuid.uuid4().hex,
                "content_type": "text/plain",
            }
        ).execute()

        for idx in range(2):
            cls.client.table("chunks").insert(
                {
                    "id": str(uuid.uuid4()),
                    "doc_id": cls.deleted_doc_id,
                    "chunk_idx": idx,
                    "text": f"{cls.marker_phrase} 청크 {idx} 본문",
                }
            ).execute()

        # 즉시 soft-delete — UTC ISO 직렬화. deleted_at IS NOT NULL 만 의미 있고 시각은 무관.
        from datetime import datetime, timezone

        cls.client.table("documents").update(
            {"deleted_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", cls.deleted_doc_id).execute()

    def test_sparse_only_fallback_excludes_soft_deleted_docs(self) -> None:
        """`_sparse_only_fallback` 직접 호출 — soft-deleted doc 의 chunks 가 0건이어야 한다."""
        from app.routers.search import _RPC_TOP_K, _sparse_only_fallback

        rows = _sparse_only_fallback(
            self.client, self.marker_phrase, self.default_user_id, _RPC_TOP_K
        )
        leaked = [r for r in rows if r.get("doc_id") == self.deleted_doc_id]
        self.assertEqual(
            len(leaked),
            0,
            f"soft-deleted doc 의 chunks {len(leaked)}건이 sparse-only fallback 결과에 노출됨 — "
            "documents!inner JOIN 의 deleted_at IS NULL 필터 회귀.",
        )


def _cleanup_synthetic_doc(client, doc_id: str) -> None:
    """테스트 데이터 정리 — chunks → documents 순서. CASCADE 가 있어도 명시적으로 호출."""
    try:
        client.table("chunks").delete().eq("doc_id", doc_id).execute()
    finally:
        client.table("documents").delete().eq("id", doc_id).execute()


if __name__ == "__main__":
    unittest.main()
