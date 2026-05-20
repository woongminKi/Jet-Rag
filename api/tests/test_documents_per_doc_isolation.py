"""D1 P1#1 — per-doc IDOR 격리 회귀 테스트 (senior-qa 리포트 P1#1).

QA 리포트 지목: list/search/upload 는 이미 app-layer 격리됐는데 documents 의 6개
per-doc 핸들러만 비격리 → reingest 데이터 파괴 IDOR 등 가능. 본 테스트는 6개 핸들러
전부에 본인 소유 검증(`documents.user_id == current_user.user_id`)이 들어갔는지
직접 함수 호출 + supabase mock 으로 검증한다.

대상 핸들러 (api/app/routers/documents.py):
- reingest_document (POST /documents/{doc_id}/reingest)
- reingest_missing_vision (POST /documents/{doc_id}/reingest-missing)
- list_active_documents (GET /documents/active)
- batch_status (GET /documents/batch-status)
- get_document (GET /documents/{doc_id})
- get_document_status (GET /documents/{doc_id}/status)

검증 패턴:
- auth_enabled=true 가정 — CurrentUser(user_id="user-A") + doc.user_id="user-B"
  → 4개 per-doc 핸들러 = 404 ("문서를 찾을 수 없습니다."),
    list_active/batch_status = 결과 제외.
- 본인 doc → 정상 응답.
- auth_enabled=false (default LEGACY) → 기존 무인증 동작 보존 (test_documents_*
  기존 회귀 게이트가 보장).

stdlib `unittest` 만 사용 — 외부 의존성 0. supabase 클라이언트는 MagicMock chain.
실행: `python -m unittest tests.test_documents_per_doc_isolation`
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

from fastapi import HTTPException

from app.auth import CurrentUser

_USER_A = "11111111-1111-1111-1111-111111111111"
_USER_B = "22222222-2222-2222-2222-222222222222"
_DOC_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"  # user_id=A 소유
_DOC_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"  # user_id=B 소유


# ============================================================
# reingest_document — per-doc 쓰기, IDOR 최우선
# ============================================================
class ReingestDocumentIsolationTest(unittest.TestCase):
    """L766 reingest_document — 타인 doc reingest 시 404 + chunks 무삭제."""

    def _build_supabase_mock(self, *, doc_owner: str) -> MagicMock:
        """documents SELECT → user_id 컬럼 포함된 1 row 반환."""
        client = MagicMock()
        existing = MagicMock()
        existing.data = [{
            "id": _DOC_B,
            "flags": {},
            "user_id": doc_owner,
        }]
        # documents.select.eq.is_.limit.execute chain
        (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .is_.return_value
            .limit.return_value
            .execute.return_value
        ) = existing
        return client

    def test_other_user_doc_returns_404(self) -> None:
        """user A 토큰 + user B 의 doc_id → 404 (존재 위장)."""
        from app.routers import documents as docs_module

        client = self._build_supabase_mock(doc_owner=_USER_B)
        bg = MagicMock()
        caller = CurrentUser(user_id=_USER_A)

        with patch.object(docs_module, "get_supabase_client", return_value=client), \
             patch.object(docs_module, "get_settings", return_value=MagicMock()):
            with self.assertRaises(HTTPException) as ctx:
                docs_module.reingest_document(
                    doc_id=_DOC_B,
                    background_tasks=bg,
                    mode=None,
                    current_user=caller,
                )
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "문서를 찾을 수 없습니다.")
        # chunks delete 호출이 없었어야 한다 — reset_doc 진입 차단
        chunks_table_calls = [
            call for call in client.table.call_args_list if call.args == ("chunks",)
        ]
        self.assertEqual(
            len(chunks_table_calls),
            0,
            "IDOR 차단 실패 — 타인 doc 인데 chunks 테이블에 접근함",
        )


# ============================================================
# reingest_missing_vision — per-doc 쓰기, vision 호출 IDOR 차단
# ============================================================
class ReingestMissingVisionIsolationTest(unittest.TestCase):
    """L850 reingest_missing_vision — 타인 PDF 의 vision 호출 차단."""

    def test_other_user_doc_returns_404(self) -> None:
        from app.routers import documents as docs_module

        client = MagicMock()
        existing = MagicMock()
        existing.data = [{
            "id": _DOC_B,
            "doc_type": "pdf",
            "flags": {},
            "user_id": _USER_B,
        }]
        (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .is_.return_value
            .limit.return_value
            .execute.return_value
        ) = existing

        caller = CurrentUser(user_id=_USER_A)
        bg = MagicMock()

        with patch.object(docs_module, "get_supabase_client", return_value=client), \
             patch.object(docs_module, "get_settings", return_value=MagicMock()):
            with self.assertRaises(HTTPException) as ctx:
                docs_module.reingest_missing_vision(
                    doc_id=_DOC_B,
                    background_tasks=bg,
                    mode=None,
                    current_user=caller,
                )
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "문서를 찾을 수 없습니다.")


# ============================================================
# get_document — per-doc 읽기
# ============================================================
class GetDocumentIsolationTest(unittest.TestCase):
    """L1204 get_document — 타인 doc 메타 노출 차단."""

    def test_other_user_doc_returns_404(self) -> None:
        from app.routers import documents as docs_module

        client = MagicMock()
        doc_resp = MagicMock()
        doc_resp.data = [{
            "id": _DOC_B,
            "title": "타인 문서",
            "doc_type": "pdf",
            "source_channel": "api",
            "size_bytes": 1000,
            "content_type": "application/pdf",
            "tags": [],
            "summary": None,
            "flags": {},
            "created_at": "2026-05-20T00:00:00Z",
            "received_ms": 100,
            "user_id": _USER_B,
        }]
        (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .is_.return_value
            .limit.return_value
            .execute.return_value
        ) = doc_resp

        caller = CurrentUser(user_id=_USER_A)

        with patch.object(docs_module, "get_supabase_client", return_value=client):
            with self.assertRaises(HTTPException) as ctx:
                docs_module.get_document(doc_id=_DOC_B, current_user=caller)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "문서를 찾을 수 없습니다.")

    def test_own_doc_returns_200_payload(self) -> None:
        """본인 doc → 정상 응답. chunks/job 보조 chain 도 mock."""
        from app.routers import documents as docs_module

        client = MagicMock()
        doc_resp = MagicMock()
        doc_resp.data = [{
            "id": _DOC_A,
            "title": "내 문서",
            "doc_type": "pdf",
            "source_channel": "api",
            "size_bytes": 2000,
            "content_type": "application/pdf",
            "tags": ["tag1"],
            "summary": "요약",
            "flags": {"source_url": "https://example.com"},
            "created_at": "2026-05-20T00:00:00Z",
            "received_ms": 150,
            "user_id": _USER_A,
        }]
        chunks_resp = MagicMock()
        chunks_resp.count = 3

        def _table_dispatch(name: str):
            m = MagicMock()
            if name == "documents":
                m.select.return_value.eq.return_value.is_.return_value.limit.return_value.execute.return_value = doc_resp
            else:  # chunks
                m.select.return_value.eq.return_value.execute.return_value = chunks_resp
            return m

        client.table.side_effect = _table_dispatch
        caller = CurrentUser(user_id=_USER_A)

        with patch.object(docs_module, "get_supabase_client", return_value=client), \
             patch.object(docs_module, "get_latest_job_for_doc", return_value=None):
            resp = docs_module.get_document(doc_id=_DOC_A, current_user=caller)

        self.assertEqual(resp.id, _DOC_A)
        self.assertEqual(resp.title, "내 문서")
        self.assertEqual(resp.chunks_count, 3)
        self.assertEqual(resp.source_url, "https://example.com")


# ============================================================
# get_document_status — per-doc 읽기
# ============================================================
class GetDocumentStatusIsolationTest(unittest.TestCase):
    """L1276 get_document_status — 타인 doc status 노출 차단."""

    def test_other_user_doc_returns_404(self) -> None:
        from app.routers import documents as docs_module

        client = MagicMock()
        existing = MagicMock()
        existing.data = [{"id": _DOC_B, "user_id": _USER_B}]
        (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .limit.return_value
            .execute.return_value
        ) = existing

        caller = CurrentUser(user_id=_USER_A)

        with patch.object(docs_module, "get_supabase_client", return_value=client):
            with self.assertRaises(HTTPException) as ctx:
                docs_module.get_document_status(
                    doc_id=_DOC_B, include_logs=False, current_user=caller
                )
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "문서를 찾을 수 없습니다.")

    def test_own_doc_returns_status(self) -> None:
        from app.routers import documents as docs_module

        client = MagicMock()
        existing = MagicMock()
        existing.data = [{"id": _DOC_A, "user_id": _USER_A}]
        (
            client.table.return_value
            .select.return_value
            .eq.return_value
            .limit.return_value
            .execute.return_value
        ) = existing
        caller = CurrentUser(user_id=_USER_A)

        with patch.object(docs_module, "get_supabase_client", return_value=client), \
             patch.object(docs_module, "get_latest_job_for_doc", return_value=None):
            resp = docs_module.get_document_status(
                doc_id=_DOC_A, include_logs=False, current_user=caller
            )

        self.assertEqual(resp.doc_id, _DOC_A)
        self.assertIsNone(resp.job)


# ============================================================
# list_active_documents — documents JOIN 단계에서 user_id 필터
# ============================================================
class ListActiveDocumentsIsolationTest(unittest.TestCase):
    """L1016 list_active_documents — 타인 doc 의 active job 자연 제외 (doc_meta miss)."""

    def test_other_user_active_job_excluded(self) -> None:
        """ingest_jobs 가 타인 doc 의 running job 을 가져와도, documents
        조회가 user_id 필터로 0건 → doc_meta lookup miss → 응답 0건.
        """
        from app.routers import documents as docs_module

        client = MagicMock()
        jobs_resp = MagicMock()
        jobs_resp.data = [{
            "id": "job-foreign",
            "doc_id": _DOC_B,  # user B 소유
            "status": "running",
            "current_stage": "embed",
            "attempts": 1,
            "error_msg": None,
            "queued_at": "2026-05-20T08:00:00Z",
            "started_at": "2026-05-20T08:00:01Z",
            "finished_at": None,
            "stage_progress": None,
        }]
        # documents.in_().eq("user_id").execute → 0 rows (필터에 의해)
        docs_resp = MagicMock()
        docs_resp.data = []

        def _table_dispatch(name: str):
            m = MagicMock()
            if name == "ingest_jobs":
                m.select.return_value.gte.return_value.order.return_value.execute.return_value = jobs_resp
            else:
                m.select.return_value.in_.return_value.eq.return_value.execute.return_value = docs_resp
            return m

        client.table.side_effect = _table_dispatch
        caller = CurrentUser(user_id=_USER_A)

        with patch.object(docs_module, "get_supabase_client", return_value=client):
            resp = docs_module.list_active_documents(hours=24, current_user=caller)

        self.assertEqual(resp.items, [])

    def test_own_active_job_included(self) -> None:
        from app.routers import documents as docs_module

        client = MagicMock()
        jobs_resp = MagicMock()
        jobs_resp.data = [{
            "id": "job-own",
            "doc_id": _DOC_A,
            "status": "queued",
            "current_stage": None,
            "attempts": 0,
            "error_msg": None,
            "queued_at": "2026-05-20T09:00:00Z",
            "started_at": None,
            "finished_at": None,
            "stage_progress": None,
        }]
        docs_resp = MagicMock()
        docs_resp.data = [{"id": _DOC_A, "title": "내 문서", "size_bytes": 100}]

        def _table_dispatch(name: str):
            m = MagicMock()
            if name == "ingest_jobs":
                m.select.return_value.gte.return_value.order.return_value.execute.return_value = jobs_resp
            else:
                m.select.return_value.in_.return_value.eq.return_value.execute.return_value = docs_resp
            return m

        client.table.side_effect = _table_dispatch
        caller = CurrentUser(user_id=_USER_A)

        with patch.object(docs_module, "get_supabase_client", return_value=client):
            resp = docs_module.list_active_documents(hours=24, current_user=caller)

        self.assertEqual(len(resp.items), 1)
        self.assertEqual(resp.items[0].doc_id, _DOC_A)


# ============================================================
# batch_status — 미소유 doc_id 결과 제외
# ============================================================
class BatchStatusIsolationTest(unittest.TestCase):
    """L1117 batch_status — 본인 소유만 items, 미소유는 응답에서 누락 (404 아님)."""

    def _build_client(self, *, owned_doc_ids: list[str], jobs_rows: list[dict]) -> MagicMock:
        client = MagicMock()
        owned_resp = MagicMock()
        owned_resp.data = [{"id": d} for d in owned_doc_ids]
        jobs_resp = MagicMock()
        jobs_resp.data = jobs_rows

        def _table_dispatch(name: str):
            m = MagicMock()
            if name == "documents":
                # documents.select.in_.eq.execute (ownership filter)
                m.select.return_value.in_.return_value.eq.return_value.execute.return_value = owned_resp
            else:  # ingest_jobs
                m.select.return_value.in_.return_value.order.return_value.execute.return_value = jobs_resp
            return m

        client.table.side_effect = _table_dispatch
        return client

    def test_foreign_doc_id_excluded_from_items(self) -> None:
        """입력 ?ids=A,B 에서 A 만 본인 소유 → 응답 items 에 B 누락."""
        from app.routers import documents as docs_module

        jobs_rows = [{
            "id": "job-a",
            "doc_id": _DOC_A,
            "status": "completed",
            "current_stage": "done",
            "attempts": 1,
            "error_msg": None,
            "queued_at": "2026-05-20T08:00:00Z",
            "started_at": "2026-05-20T08:00:01Z",
            "finished_at": "2026-05-20T08:30:00Z",
            "stage_progress": None,
        }]
        client = self._build_client(owned_doc_ids=[_DOC_A], jobs_rows=jobs_rows)
        caller = CurrentUser(user_id=_USER_A)

        with patch.object(docs_module, "get_supabase_client", return_value=client):
            resp = docs_module.batch_status(
                ids=f"{_DOC_A},{_DOC_B}", current_user=caller
            )

        self.assertEqual(len(resp.items), 1)
        self.assertEqual(resp.items[0].doc_id, _DOC_A)
        # B 가 응답에 없어야 함 — 미소유 누락
        ids_returned = {item.doc_id for item in resp.items}
        self.assertNotIn(_DOC_B, ids_returned)

    def test_all_foreign_returns_empty_items(self) -> None:
        """입력 전부 미소유 → 빈 items (404 아님 — 배치는 부분 응답이 자연)."""
        from app.routers import documents as docs_module

        client = self._build_client(owned_doc_ids=[], jobs_rows=[])
        caller = CurrentUser(user_id=_USER_A)

        with patch.object(docs_module, "get_supabase_client", return_value=client):
            resp = docs_module.batch_status(
                ids=f"{_DOC_B},cccccccc-cccc-cccc-cccc-cccccccccccc",
                current_user=caller,
            )

        self.assertEqual(resp.items, [])


# ============================================================
# auth_enabled=false 회귀 — LEGACY_DEFAULT_USER 통과 (production 무중단)
# ============================================================
class AuthDisabledFallbackTest(unittest.TestCase):
    """auth_enabled=false (default) → 기존 default_user_id 컨텍스트에서 무회귀.

    핸들러 직접 호출 시 CurrentUserDep 의 default = LEGACY_DEFAULT_USER
    (user_id="00000000-0000-0000-0000-000000000001"). 기존 데이터가 전부
    default_user_id 소유면 정상 통과.
    """

    def test_get_document_passes_for_default_user(self) -> None:
        from app.auth import LEGACY_DEFAULT_USER
        from app.routers import documents as docs_module

        client = MagicMock()
        doc_resp = MagicMock()
        doc_resp.data = [{
            "id": _DOC_A,
            "title": "default 문서",
            "doc_type": "pdf",
            "source_channel": "api",
            "size_bytes": 100,
            "content_type": "application/pdf",
            "tags": [],
            "summary": None,
            "flags": {},
            "created_at": "2026-05-20T00:00:00Z",
            "received_ms": 50,
            "user_id": LEGACY_DEFAULT_USER.user_id,
        }]
        chunks_resp = MagicMock(); chunks_resp.count = 0

        def _table_dispatch(name: str):
            m = MagicMock()
            if name == "documents":
                m.select.return_value.eq.return_value.is_.return_value.limit.return_value.execute.return_value = doc_resp
            else:
                m.select.return_value.eq.return_value.execute.return_value = chunks_resp
            return m

        client.table.side_effect = _table_dispatch

        with patch.object(docs_module, "get_supabase_client", return_value=client), \
             patch.object(docs_module, "get_latest_job_for_doc", return_value=None):
            # 인자 명시 안 함 → LEGACY_DEFAULT_USER default 사용
            resp = docs_module.get_document(doc_id=_DOC_A)

        self.assertEqual(resp.id, _DOC_A)


if __name__ == "__main__":
    unittest.main()
