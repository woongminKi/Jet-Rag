"""수익화 W4 — app.services.email_ingest 단위 테스트. MagicMock Supabase, 외부 I/O 0."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")


class TokenTest(unittest.TestCase):
    def test_generate_token_shape(self) -> None:
        from app.services.email_ingest import generate_token

        tok = generate_token()
        self.assertEqual(len(tok), 8)
        self.assertTrue(tok.isalnum())
        self.assertEqual(tok, tok.lower())

    def test_build_address(self) -> None:
        from app.services.email_ingest import build_address

        self.assertEqual(build_address("abc12345", "in.woong-s.com"), "u-abc12345@in.woong-s.com")


class ParseTokenTest(unittest.TestCase):
    def test_parses_token_from_to_address(self) -> None:
        from app.services.email_ingest import parse_token

        self.assertEqual(parse_token("u-abc12345@in.woong-s.com"), "abc12345")

    def test_handles_display_name_and_case(self) -> None:
        from app.services.email_ingest import parse_token

        self.assertEqual(parse_token("Jet-Rag <U-ABC12345@IN.WOONG-S.COM>"), "abc12345")

    def test_invalid_returns_none(self) -> None:
        from app.services.email_ingest import parse_token

        self.assertIsNone(parse_token("someone@example.com"))
        self.assertIsNone(parse_token("not-an-email"))


class LookupAddressTest(unittest.TestCase):
    def _client(self, rows: list[dict]) -> MagicMock:
        client = MagicMock()
        t = MagicMock()
        t.select.return_value = t
        t.eq.return_value = t
        t.limit.return_value = t
        t.execute.return_value.data = rows
        client.table.return_value = t
        return client

    def test_found(self) -> None:
        from app.services import email_ingest

        rows = [{"user_id": "uid-1", "token": "abc12345", "owner_email": "a@b.c"}]
        with patch.object(email_ingest, "get_supabase_client", return_value=self._client(rows)):
            rec = email_ingest.lookup_by_token("abc12345")
        self.assertEqual(rec["user_id"], "uid-1")

    def test_not_found_returns_none(self) -> None:
        from app.services import email_ingest

        with patch.object(email_ingest, "get_supabase_client", return_value=self._client([])):
            self.assertIsNone(email_ingest.lookup_by_token("zzzzzzzz"))


class SenderAllowedTest(unittest.TestCase):
    def test_match_case_insensitive(self) -> None:
        from app.services.email_ingest import sender_allowed

        self.assertTrue(sender_allowed("Kim <USER@Gmail.com>", "user@gmail.com"))

    def test_mismatch(self) -> None:
        from app.services.email_ingest import sender_allowed

        self.assertFalse(sender_allowed("other@gmail.com", "user@gmail.com"))

    def test_missing_owner_email_rejects(self) -> None:
        from app.services.email_ingest import sender_allowed

        self.assertFalse(sender_allowed("user@gmail.com", None))


class IngestAttachmentTest(unittest.TestCase):
    def test_disallowed_extension_skipped(self) -> None:
        from app.services import email_ingest

        result = email_ingest.ingest_email_attachment(
            user_id="uid-1",
            filename="note.txt",
            content_type="text/plain",
            raw=b"hello",
            background_tasks=MagicMock(),
        )
        self.assertEqual(result["status"], "skipped")
        self.assertIn("확장자", result["reason"])

    def test_oversize_skipped(self) -> None:
        from app.services import email_ingest

        with patch.object(email_ingest, "_MAX_SIZE_BYTES", 10):
            result = email_ingest.ingest_email_attachment(
                user_id="uid-1",
                filename="big.pdf",
                content_type="application/pdf",
                raw=b"%PDF-1.4 0123456789",
                background_tasks=MagicMock(),
            )
        self.assertEqual(result["status"], "skipped")

    def test_bad_magic_skipped(self) -> None:
        from app.services import email_ingest

        result = email_ingest.ingest_email_attachment(
            user_id="uid-1",
            filename="fake.pdf",
            content_type="application/pdf",
            raw=b"GIF89a not a pdf",
            background_tasks=MagicMock(),
        )
        self.assertEqual(result["status"], "skipped")

    def test_duplicate_skipped_without_insert(self) -> None:
        from app.services import email_ingest

        client = MagicMock()
        t = MagicMock()
        t.select.return_value = t
        t.eq.return_value = t
        t.is_.return_value = t
        t.limit.return_value = t
        t.execute.return_value.data = [{"id": "doc-1", "flags": {}}]
        client.table.return_value = t
        bg = MagicMock()
        with patch.object(email_ingest, "get_supabase_client", return_value=client):
            result = email_ingest.ingest_email_attachment(
                user_id="uid-1",
                filename="doc.pdf",
                content_type="application/pdf",
                raw=b"%PDF-1.4 test",
                background_tasks=bg,
            )
        self.assertEqual(result["status"], "duplicated")
        bg.add_task.assert_not_called()

    def test_new_attachment_queues_pipeline(self) -> None:
        from app.services import email_ingest

        client = MagicMock()
        dedup_t = MagicMock()
        dedup_t.select.return_value = dedup_t
        dedup_t.eq.return_value = dedup_t
        dedup_t.is_.return_value = dedup_t
        dedup_t.limit.return_value = dedup_t
        dedup_t.execute.return_value.data = []
        insert_t = MagicMock()
        insert_t.insert.return_value.execute.return_value.data = [{"id": "doc-new"}]
        # 1번째 table() 호출 = dedup select, 2번째 = insert
        client.table.side_effect = [dedup_t, insert_t]
        bg = MagicMock()
        fake_job = MagicMock()
        fake_job.id = "job-1"
        with patch.object(email_ingest, "get_supabase_client", return_value=client), \
             patch.object(email_ingest, "create_job", return_value=fake_job) as cj, \
             patch.object(email_ingest, "run_full_ingest") as rfi:
            result = email_ingest.ingest_email_attachment(
                user_id="uid-1",
                filename="보고서.pdf",
                content_type="application/pdf",
                raw=b"%PDF-1.4 test",
                background_tasks=bg,
            )
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["doc_id"], "doc-new")
        cj.assert_called_once_with(doc_id="doc-new")
        bg.add_task.assert_called_once()
        self.assertIs(bg.add_task.call_args.args[0], rfi)


if __name__ == "__main__":
    unittest.main()
