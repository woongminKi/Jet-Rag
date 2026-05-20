"""D2 — SupabaseBlobStorage path helper + run_full_ingest user_id 분기 (plan §8).

검증:
- `build_user_path` / `build_pending_path` 케이스 (확장자 있음/없음)
- `run_full_ingest(user_id="u1")` → put_at path = `user/u1/<sha>{ext}`
- `run_full_ingest(user_id=None)` → legacy path `<sha>{ext}` (회귀 0)

전략: SupabaseBlobStorage 인스턴스 _client 를 mock 으로 교체 + put_at 인자 캡처.
외부 의존성 0.

실행: `python -m unittest tests.test_storage_per_user_prefix`
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.adapters.impl.supabase_storage import SupabaseBlobStorage


class BuildPathHelpersTest(unittest.TestCase):
    """static helper — pure function 검증."""

    def test_build_user_path_with_pdf(self) -> None:
        path = SupabaseBlobStorage.build_user_path(
            user_id="u1", sha256="abc123", file_name="report.pdf"
        )
        self.assertEqual(path, "user/u1/abc123.pdf")

    def test_build_user_path_with_uppercase_extension(self) -> None:
        # 확장자 소문자 정규화
        path = SupabaseBlobStorage.build_user_path(
            user_id="u1", sha256="abc", file_name="X.PDF"
        )
        self.assertEqual(path, "user/u1/abc.pdf")

    def test_build_user_path_without_extension_uses_mimetypes_fallback(self) -> None:
        # mimetypes.guess_extension('application/octet-stream') = '' (대부분 환경).
        # 빈 문자열로 fallback — sha256 만 노출.
        path = SupabaseBlobStorage.build_user_path(
            user_id="u1", sha256="abc", file_name="noext"
        )
        # ext 가 빈 문자열일 수 있음 (환경 의존). 'user/u1/abc' prefix 만 검증.
        self.assertTrue(
            path.startswith("user/u1/abc"),
            f"path 가 user prefix + sha 로 시작해야 함: {path!r}",
        )

    def test_build_pending_path_with_html(self) -> None:
        path = SupabaseBlobStorage.build_pending_path(
            user_id="u1", doc_uuid="deadbeef", ext=".html"
        )
        self.assertEqual(path, "user/u1/pending/deadbeef.html")

    def test_build_pending_path_with_empty_ext(self) -> None:
        path = SupabaseBlobStorage.build_pending_path(
            user_id="u1", doc_uuid="x", ext=""
        )
        self.assertEqual(path, "user/u1/pending/x")

    def test_build_user_path_uid_isolation(self) -> None:
        """다른 uid 는 자연 분리."""
        p_a = SupabaseBlobStorage.build_user_path(
            user_id="a", sha256="sha", file_name="x.pdf"
        )
        p_b = SupabaseBlobStorage.build_user_path(
            user_id="b", sha256="sha", file_name="x.pdf"
        )
        self.assertNotEqual(p_a, p_b)
        self.assertTrue(p_a.startswith("user/a/"))
        self.assertTrue(p_b.startswith("user/b/"))


class RunFullIngestUserIdTest(unittest.TestCase):
    """`run_full_ingest(user_id=...)` 분기에 따른 final_path 결정."""

    def _run(
        self, *, user_id: str | None,
    ) -> str:
        """run_full_ingest 호출 + SupabaseBlobStorage.put_at 의 path 인자 캡처.

        전략: 클래스 자체는 patch 하지 않고 (build_user_path/build_pending_path 같은
        staticmethod 가 원본 그대로 동작해야 함), 인스턴스 메서드 `__init__` /
        `_upload` / `put_at` 만 우회.
        """
        from app.ingest.upload import run_full_ingest

        captured_path: dict[str, str] = {}

        def _fake_put_at(self, *, path, data, content_type, sha256=None):  # noqa: ARG001
            captured_path["path"] = path
            return MagicMock(
                blob_id=path, path=path, content_type=content_type,
                size_bytes=len(data), sha256=sha256,
            )

        fake_supabase = MagicMock()
        fake_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = (
            MagicMock(data=[{"id": "doc"}])
        )

        with patch.object(
            SupabaseBlobStorage, "__init__", lambda self, bucket: None,
        ), patch.object(
            SupabaseBlobStorage, "put_at", _fake_put_at,
        ), patch(
            "app.ingest.upload.get_supabase_client", return_value=fake_supabase
        ), patch(
            "app.ingest.upload.run_pipeline"
        ) as mock_pipeline:
            run_full_ingest(
                job_id="job1",
                doc_id="doc1",
                raw=b"hello",
                sha256="sha123",
                ext=".pdf",
                content_type="application/pdf",
                user_id=user_id,
            )
            mock_pipeline.assert_called_once()

        return captured_path["path"]

    def test_with_user_id_uses_user_prefix(self) -> None:
        path = self._run(user_id="u1")
        self.assertEqual(path, "user/u1/sha123.pdf")

    def test_without_user_id_uses_legacy_path(self) -> None:
        # 회귀 0 — 기존 호출처 / 단위 테스트가 user_id 인자 없이 호출해도 동작.
        path = self._run(user_id=None)
        self.assertEqual(path, "sha123.pdf")


if __name__ == "__main__":
    unittest.main()
