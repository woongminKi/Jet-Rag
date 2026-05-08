"""S2 D3 (2026-05-09) — `routers/documents.py` 의 mode 처리 회귀 보호.

master plan §6 S2 D3. 사용자 결정 Q-S2-1c (UI 위치 = upload + reingest) +
Q-S2-1f (localStorage prefill — 백엔드는 비관여) + Q-S2-1h (doc-level mode).

T-B-08: invalid mode → 400
T-B-10: reingest mode 명시 → flags.ingest_mode 갱신 + page_cap_override 전달
T-B-11: reingest mode 미명시 → flags.ingest_mode prefill (이전 mode 보존)
T-B-12: _reset_doc_for_reingest 가 ingest_mode flags 보존
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.routers import documents as docs_module


def _mock_supabase_for_reingest(
    *,
    doc_exists: bool = True,
    doc_type: str = "pdf",
    existing_flags: dict | None = None,
    chunks_count: int = 0,
) -> MagicMock:
    """reingest 엔드포인트용 supabase chain mock.

    반환 객체의 `_updates` 리스트에 `.update({...}).eq(...).execute()` 호출 시
    전달된 dict 누적 (flags merge 검증용).
    """
    client = MagicMock()
    updates: list[dict] = []
    client._updates = updates

    def _table(name: str):
        m = MagicMock()
        if name == "documents":
            select_chain = MagicMock()
            # SELECT id, flags / id, doc_type, flags / storage_path 등 다양한 chain.
            # 모든 select 변형을 동일 row 로 응답.
            doc_row = {
                "id": "doc-1",
                "doc_type": doc_type,
                "flags": existing_flags or {},
                "storage_path": "abc123.pdf",
            }
            data = [doc_row] if doc_exists else []
            select_chain.eq.return_value.is_.return_value.limit.return_value.execute.return_value = MagicMock(data=data)
            select_chain.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=data)
            m.select.return_value = select_chain

            # update().eq().execute() — 전달된 dict 캡처.
            def _update(payload: dict):
                updates.append(payload)
                upd = MagicMock()
                upd.eq.return_value.execute.return_value = MagicMock(data=[])
                return upd

            m.update.side_effect = _update
        elif name == "chunks":
            select_chain = MagicMock()
            select_chain.eq.return_value.execute.return_value = MagicMock(
                data=[], count=chunks_count,
            )
            m.select.return_value = select_chain
            m.delete.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        elif name == "ingest_jobs":
            # create_job + get_latest_job_for_doc 가 호출되는데, 실제 호출은 patch 로 우회.
            m.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        return m

    client.table.side_effect = _table
    return client


class TestRejectInvalidMode(unittest.TestCase):
    """T-B-08 — invalid mode 입력 → 400 한국어 메시지."""

    def test_validate_ingest_mode_rejects_unknown(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            docs_module._validate_ingest_mode("turbo")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("지원되지 않는 모드", ctx.exception.detail)
        self.assertIn("turbo", ctx.exception.detail)

    def test_validate_ingest_mode_accepts_valid(self) -> None:
        for mode in ("fast", "default", "precise"):
            self.assertEqual(docs_module._validate_ingest_mode(mode), mode)

    def test_validate_ingest_mode_none_returns_default(self) -> None:
        self.assertEqual(docs_module._validate_ingest_mode(None), "default")
        self.assertEqual(docs_module._validate_ingest_mode(""), "default")


class TestReingestModeHandling(unittest.TestCase):
    """T-B-10 / T-B-11 — POST /documents/{id}/reingest 의 mode 처리."""

    def _call_reingest(
        self,
        *,
        mode_query: str | None,
        existing_flags: dict | None,
    ) -> tuple[MagicMock, MagicMock]:
        """공통 호출 — supabase mock + BG mock 반환."""
        supabase = _mock_supabase_for_reingest(existing_flags=existing_flags)
        bg = MagicMock()
        bg.add_task = MagicMock()
        # create_job / get_latest_job_for_doc 우회.
        fake_job = MagicMock(id="job-new")
        with patch.object(docs_module, "get_supabase_client", return_value=supabase), \
             patch.object(docs_module, "create_job", return_value=fake_job), \
             patch.object(docs_module, "get_latest_job_for_doc", return_value=None):
            docs_module.reingest_document(
                doc_id="doc-1", background_tasks=bg, mode=mode_query,
            )
        return supabase, bg

    def test_explicit_mode_updates_flags_and_page_cap(self) -> None:
        """T-B-10 — mode='fast' 명시 → flags.ingest_mode='fast' + page_cap_override=10."""
        supabase, bg = self._call_reingest(
            mode_query="fast", existing_flags={"ingest_mode": "default"},
        )
        # update payload 확인: flags 에 ingest_mode='fast' 들어가야 함 (마지막 update 가 새 mode set)
        update_payloads = supabase._updates
        self.assertTrue(
            any(
                isinstance(p.get("flags"), dict)
                and p["flags"].get("ingest_mode") == "fast"
                for p in update_payloads
            ),
            f"flags.ingest_mode='fast' update 없음: {update_payloads}",
        )
        # BG task 의 page_cap_override 가 10 (fast default)
        bg.add_task.assert_called_once()
        kwargs = bg.add_task.call_args.kwargs
        self.assertEqual(kwargs.get("page_cap_override"), 10)

    def test_no_mode_prefills_from_existing_flags(self) -> None:
        """T-B-11 — mode 미명시 → 이전 flags.ingest_mode='precise' 그대로 사용."""
        supabase, bg = self._call_reingest(
            mode_query=None, existing_flags={"ingest_mode": "precise"},
        )
        update_payloads = supabase._updates
        self.assertTrue(
            any(
                isinstance(p.get("flags"), dict)
                and p["flags"].get("ingest_mode") == "precise"
                for p in update_payloads
            )
        )
        # precise → page_cap_override = 0
        bg.add_task.assert_called_once()
        self.assertEqual(bg.add_task.call_args.kwargs.get("page_cap_override"), 0)

    def test_no_mode_no_prior_flags_uses_default(self) -> None:
        """T-B-11b — mode 미명시 + 이전 flags 없음 → default."""
        supabase, bg = self._call_reingest(
            mode_query=None, existing_flags={},
        )
        update_payloads = supabase._updates
        self.assertTrue(
            any(
                isinstance(p.get("flags"), dict)
                and p["flags"].get("ingest_mode") == "default"
                for p in update_payloads
            )
        )

    def test_invalid_mode_returns_400(self) -> None:
        """invalid mode query → 400."""
        bg = MagicMock()
        with patch.object(
            docs_module, "get_supabase_client",
            return_value=_mock_supabase_for_reingest(),
        ), patch.object(docs_module, "get_latest_job_for_doc", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                docs_module.reingest_document(
                    doc_id="doc-1", background_tasks=bg, mode="turbo",
                )
        self.assertEqual(ctx.exception.status_code, 400)


class TestResetDocPreservesIngestMode(unittest.TestCase):
    """T-B-12 — `_reset_doc_for_reingest` 가 flags.ingest_mode 만 보존하고 나머지는 reset."""

    def test_reset_preserves_ingest_mode(self) -> None:
        """기존 flags={ingest_mode:'fast', failed:true, scan:true} → reset 후 {ingest_mode:'fast'}."""
        supabase = _mock_supabase_for_reingest(
            existing_flags={
                "ingest_mode": "fast",
                "failed": True,
                "scan": True,
                "vision_budget_exceeded": True,
            },
            chunks_count=3,
        )
        deleted = docs_module._reset_doc_for_reingest(supabase, "doc-1")
        self.assertEqual(deleted, 3)

        # update payload 의 flags 가 {'ingest_mode': 'fast'} 만 (다른 시그널 reset)
        flags_updates = [
            p["flags"] for p in supabase._updates if "flags" in p
        ]
        self.assertEqual(len(flags_updates), 1)
        self.assertEqual(flags_updates[0], {"ingest_mode": "fast"})

    def test_reset_no_prior_mode_results_empty_flags(self) -> None:
        """기존 flags 에 ingest_mode 없으면 빈 dict 로 reset (이전 동작 유지)."""
        supabase = _mock_supabase_for_reingest(
            existing_flags={"failed": True, "scan": True},
            chunks_count=0,
        )
        docs_module._reset_doc_for_reingest(supabase, "doc-1")
        flags_updates = [
            p["flags"] for p in supabase._updates if "flags" in p
        ]
        self.assertEqual(flags_updates[0], {})


if __name__ == "__main__":
    unittest.main()
