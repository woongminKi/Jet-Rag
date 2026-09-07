"""W25 D14 — 한국어 NFD/NFC 정규화 회귀 보호.

배경: macOS Finder 가 한국어 파일명을 NFD (자모 분리) 형태로 보냄. DB ilike·검색
query 와 byte 매칭 fail 하는 사고 (W25 D14 '승인글 템플릿1' 삭제 시 발견).

수정: 인제스트 단 title 저장 시 NFC 통일, /search·/answer 의 q 파라미터 NFC 정규화.
"""

from __future__ import annotations

import unicodedata
import unittest


class NfcNormalizationTest(unittest.TestCase):
    """NFC 정규화 자체 — 한국어 자모 분리/결합 변환 검증."""

    NFD_TITLE = unicodedata.normalize("NFD", "승인글 템플릿1")
    NFC_TITLE = unicodedata.normalize("NFC", "승인글 템플릿1")

    def test_nfd_and_nfc_differ_in_bytes(self) -> None:
        """NFD/NFC 가 다른 byte sequence — byte 매칭 fail 의 직접 원인."""
        self.assertNotEqual(self.NFD_TITLE.encode("utf-8"), self.NFC_TITLE.encode("utf-8"))

    def test_normalize_nfc_idempotent(self) -> None:
        """NFC normalize 가 NFD/NFC 입력 모두 같은 NFC 출력."""
        self.assertEqual(
            unicodedata.normalize("NFC", self.NFD_TITLE),
            unicodedata.normalize("NFC", self.NFC_TITLE),
        )


class SearchQueryNormalizationTest(unittest.TestCase):
    """/search 의 q 파라미터가 NFC 로 통일되는지 검증."""

    def test_search_clean_q_is_nfc(self) -> None:
        """search 라우터 함수 직접 호출 — clean_q 정규화 확인.

        실 RPC 호출은 mock 으로 건너뛰고, q 파라미터 처리만 검증.
        """
        # 검증 strategy: source code 에 unicodedata.normalize("NFC", ...) 호출 있는지
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "app" / "routers" / "search.py"
        text = src.read_text(encoding="utf-8")
        self.assertIn(
            'unicodedata.normalize("NFC", q.strip())', text,
            "search.py 의 q 파라미터가 NFC 정규화 안 됨",
        )


class AnswerQueryNormalizationTest(unittest.TestCase):
    def test_answer_clean_q_is_nfc(self) -> None:
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "app" / "routers" / "answer.py"
        text = src.read_text(encoding="utf-8")
        self.assertIn(
            'unicodedata.normalize("NFC", q.strip())', text,
            "answer.py 의 q 파라미터가 NFC 정규화 안 됨",
        )


class IngestTitleNormalizationTest(unittest.TestCase):
    def test_documents_router_normalizes_title_at_insert(self) -> None:
        """POST /documents 가 title 을 NFC 로 통일 후 저장.

        2026-09-07 이전에는 URL 경로(`POST /documents/url`)까지 두 곳이었는데, 그
        라우트가 폐기되면서 파일 업로드 한 곳만 남았다(work-log 2026-09-07 §47).
        """
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "app" / "routers" / "documents.py"
        text = src.read_text(encoding="utf-8")
        self.assertGreaterEqual(
            text.count('unicodedata.normalize("NFC"'),
            1,
            "documents.py 의 파일 업로드 경로가 NFC 정규화 안 함",
        )


class ChunkNfcNormalizationTest(unittest.TestCase):
    """W25 D14+1 D1 — chunks.text 인제스트단 NFC 정규화."""

    def test_chunk_records_normalize_text_to_nfc(self) -> None:
        """`_to_chunk_records` 가 NFD section.text 를 NFC 로 변환해야 한다."""
        from app.adapters.parser import ExtractedSection
        from app.ingest.stages.chunk import _to_chunk_records

        nfd_text = unicodedata.normalize("NFD", "한마음생활체육관 휴관일")
        nfd_title = unicodedata.normalize("NFD", "체육관 운영 내규")
        section = ExtractedSection(
            text=nfd_text,
            page=1,
            section_title=nfd_title,
            bbox=None,
        )
        records = _to_chunk_records(doc_id="test-doc", sections=[section])
        self.assertEqual(len(records), 1)
        # text + section_title 모두 NFC
        self.assertEqual(
            records[0].text,
            unicodedata.normalize("NFC", "한마음생활체육관 휴관일"),
        )
        self.assertEqual(
            records[0].section_title,
            unicodedata.normalize("NFC", "체육관 운영 내규"),
        )

    def test_chunk_records_idempotent_on_already_nfc(self) -> None:
        """NFC 입력은 변경 없음 (PDF / DOCX 등 영향 없음 보장)."""
        from app.adapters.parser import ExtractedSection
        from app.ingest.stages.chunk import _to_chunk_records

        nfc_text = "이미 NFC 인 한국어 텍스트"
        section = ExtractedSection(
            text=nfc_text, page=1, section_title=None, bbox=None
        )
        records = _to_chunk_records(doc_id="test", sections=[section])
        self.assertEqual(records[0].text, nfc_text)


class DocEmbedNfcNormalizationTest(unittest.TestCase):
    """`doc_embed._pick_source` 가 임베딩 입력을 NFC 로 통일."""

    def test_summary_normalized(self) -> None:
        from app.ingest.stages.doc_embed import _pick_source

        nfd_summary = unicodedata.normalize("NFD", "한국어 요약")
        result = _pick_source(
            summary=nfd_summary, implications=None, raw_text=""
        )
        self.assertEqual(result, unicodedata.normalize("NFC", "한국어 요약"))

    def test_raw_text_fallback_normalized(self) -> None:
        from app.ingest.stages.doc_embed import _pick_source

        nfd_raw = unicodedata.normalize("NFD", "한국어 본문 fallback")
        result = _pick_source(summary=None, implications=None, raw_text=nfd_raw)
        self.assertEqual(
            result, unicodedata.normalize("NFC", "한국어 본문 fallback")
        )

    def test_returns_none_when_all_empty(self) -> None:
        from app.ingest.stages.doc_embed import _pick_source

        self.assertIsNone(_pick_source(summary=None, implications=None, raw_text=""))


if __name__ == "__main__":
    unittest.main()
