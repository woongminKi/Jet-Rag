"""S2 D1 (2026-05-08) — incremental vision sweep 의 needs_vision hook 회귀 보호.

`_vision_pages_with_sweep` 의 vision_need_score 통합 — needs_vision False 페이지는
ImageParser 호출 회피 + sweep retry 대상 X + ENV `JETRAG_VISION_NEED_SCORE_ENABLED=false`
시 모든 페이지 호출 (S1.5 이전 동작 100% 보존).

DB 의존성 0 — `_vision_pages_with_sweep` 는 fitz + ImageParser 만 사용. mock 으로 격리.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# 모듈 import 단계의 ENV 요구 회피 (다른 테스트 파일과 동일 패턴)
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

import fitz  # noqa: E402

from app.adapters.parser import ExtractedSection, ExtractionResult  # noqa: E402
from app.config import Settings  # noqa: E402
from app.ingest import incremental as inc_mod  # noqa: E402


def _make_pdf_bytes(num_pages: int = 3) -> bytes:
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} body. 본문.")
    out = doc.tobytes()
    doc.close()
    return out


def _stub_parser(per_page_sections: list[list[ExtractedSection]]) -> MagicMock:
    parser = MagicMock()
    parser.parse.side_effect = [
        ExtractionResult(
            source_type="image",
            sections=secs,
            raw_text=" ".join(s.text for s in secs),
            warnings=[],
        )
        for secs in per_page_sections
    ]
    return parser


class TestIncrementalVisionNeedScoreHook(unittest.TestCase):
    """`_vision_pages_with_sweep` 의 needs_vision hook 회귀 차단."""

    def test_needs_vision_false_skips_image_parser(self) -> None:
        # missing pages = [1, 2, 3]. page 1 = False (skip), page 2,3 = True (호출).
        data = _make_pdf_bytes(3)
        decisions = {1: False, 2: True, 3: True}
        per_page = [
            [ExtractedSection(text=f"vision p.{i + 2}", page=None, section_title=None)]
            for i in range(2)  # page 2,3 만 호출
        ]
        parser = _stub_parser(per_page)
        with patch.object(
            inc_mod, "_page_needs_vision",
            side_effect=lambda page, *, page_num, file_name: decisions.get(page_num, True),
        ):
            # S2 D2 — _vision_pages_with_sweep 시그니처 (sections, warnings, page_cap_status)
            sections, warnings, page_cap_status = inc_mod._vision_pages_with_sweep(
                data,
                pages=[1, 2, 3],
                file_name="test.pdf",
                image_parser=parser,
            )
        # ImageParser 호출 = page 2 + page 3 = 2회
        self.assertEqual(parser.parse.call_count, 2)
        # sections 도 page 2,3 만
        self.assertEqual(len(sections), 2)
        pages_seen = {s.page for s in sections}
        self.assertEqual(pages_seen, {2, 3})
        # warnings 에 누락 알림 X (skip 은 정상 동작)
        self.assertFalse(any("sweep 후에도 누락" in w for w in warnings))
        # page cap 도달 X (default 50 > 호출 2회)
        self.assertIsNone(page_cap_status)

    def test_needs_vision_false_not_in_sweep_retry(self) -> None:
        # page 1 = False (skip), page 2 = True (sweep 1 실패 → sweep 2 회복).
        data = _make_pdf_bytes(2)
        parser = MagicMock()
        parser.parse.side_effect = [
            RuntimeError("503 sweep 1 page 2"),  # sweep 1 page 2
            ExtractionResult(
                source_type="image",
                sections=[ExtractedSection(text="p2 ok", page=None, section_title=None)],
                raw_text="p2 ok", warnings=[],
            ),  # sweep 2 page 2
        ]
        decisions = {1: False, 2: True}
        with patch.object(
            inc_mod, "_page_needs_vision",
            side_effect=lambda page, *, page_num, file_name: decisions.get(page_num, True),
        ):
            sections, warnings, page_cap_status = inc_mod._vision_pages_with_sweep(
                data,
                pages=[1, 2],
                file_name="test.pdf",
                image_parser=parser,
            )
        # parser 호출 = page 2 sweep 1 (실패) + page 2 sweep 2 (회복) = 2회
        # page 1 은 sweep 1 에서 needs_vision False → sweep 2 retry 대상도 아님
        self.assertEqual(parser.parse.call_count, 2)
        # page 2 만 sections 추가
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].page, 2)
        # 누락 warning 없음 (sweep 2 회복)
        self.assertFalse(any("sweep 후에도 누락" in w for w in warnings))
        # page cap 도달 X
        self.assertIsNone(page_cap_status)

    def test_env_disabled_calls_all_pages(self) -> None:
        # ENV `JETRAG_VISION_NEED_SCORE_ENABLED=false` 시 모든 페이지 호출.
        data = _make_pdf_bytes(2)
        per_page = [
            [ExtractedSection(text=f"p{i + 1}", page=None, section_title=None)]
            for i in range(2)
        ]
        parser = _stub_parser(per_page)
        mock_settings = Settings(
            supabase_url="", supabase_key="", supabase_service_role_key="",
            supabase_storage_bucket="documents", gemini_api_key="", hf_api_token="",
            default_user_id="00000000-0000-0000-0000-000000000001",
            doc_budget_usd=0.10, daily_budget_usd=0.50,
            sliding_24h_budget_usd=0.50, budget_krw_per_usd=1380.0,
            vision_need_score_enabled=False,
            vision_page_cap_per_doc=50,  # S2 D2 — default
        )
        with patch.object(
            inc_mod, "_page_needs_vision", return_value=False,
        ), patch.object(inc_mod, "get_settings", return_value=mock_settings):
            sections, warnings, page_cap_status = inc_mod._vision_pages_with_sweep(
                data,
                pages=[1, 2],
                file_name="test.pdf",
                image_parser=parser,
            )
        # ENV false → needs_vision 영향 0, 모든 페이지 호출
        self.assertEqual(parser.parse.call_count, 2)
        self.assertEqual(len(sections), 2)
        # page cap (50) 미도달 (호출 2회)
        self.assertIsNone(page_cap_status)


class TestIncrementalVisionPageCap(unittest.TestCase):
    """S2 D2 (2026-05-08) — incremental sweep 의 page cap 회귀 보호.

    extract.py 의 `_enrich_pdf_with_vision` 와 동일 정책 — cap 도달 시 sweep
    즉시 break + page_cap_status 반환 (caller 가 flags 마킹 책임).
    """

    def test_page_cap_break_mid_sweep(self) -> None:
        """called_count >= cap 시 sweep break + page_cap_status 반환."""
        data = _make_pdf_bytes(5)
        per_page = [
            [ExtractedSection(text=f"p{i + 1}", page=None, section_title=None)]
            for i in range(5)
        ]
        parser = _stub_parser(per_page)
        mock_settings = Settings(
            supabase_url="", supabase_key="", supabase_service_role_key="",
            supabase_storage_bucket="documents", gemini_api_key="", hf_api_token="",
            default_user_id="00000000-0000-0000-0000-000000000001",
            doc_budget_usd=0.10, daily_budget_usd=0.50,
            sliding_24h_budget_usd=0.50, budget_krw_per_usd=1380.0,
            vision_need_score_enabled=False,
            vision_page_cap_per_doc=2,  # cap=2 — page 1,2 호출 후 break
        )
        with patch.object(inc_mod, "get_settings", return_value=mock_settings):
            sections, warnings, page_cap_status = inc_mod._vision_pages_with_sweep(
                data,
                pages=[1, 2, 3, 4, 5],
                file_name="test.pdf",
                image_parser=parser,
            )
        # cap=2 → page 1,2 호출 후 page 3 진입 시 break.
        self.assertEqual(parser.parse.call_count, 2)
        self.assertEqual(len(sections), 2)
        # page_cap_status 반환 — caller 가 flags 마킹.
        self.assertIsNotNone(page_cap_status)
        self.assertEqual(page_cap_status.scope, "page_cap")
        self.assertEqual(int(page_cap_status.cap_usd), 2)
        # warnings 에 page cap 도달 메시지
        self.assertTrue(any("page cap 도달" in w for w in warnings))

    def test_page_cap_zero_unlimited(self) -> None:
        """ENV cap=0 → 무한 모드 (회복 토글). 모든 누락 페이지 호출."""
        data = _make_pdf_bytes(3)
        per_page = [
            [ExtractedSection(text=f"p{i + 1}", page=None, section_title=None)]
            for i in range(3)
        ]
        parser = _stub_parser(per_page)
        mock_settings = Settings(
            supabase_url="", supabase_key="", supabase_service_role_key="",
            supabase_storage_bucket="documents", gemini_api_key="", hf_api_token="",
            default_user_id="00000000-0000-0000-0000-000000000001",
            doc_budget_usd=0.10, daily_budget_usd=0.50,
            sliding_24h_budget_usd=0.50, budget_krw_per_usd=1380.0,
            vision_need_score_enabled=False,
            vision_page_cap_per_doc=0,  # 무한
        )
        with patch.object(inc_mod, "get_settings", return_value=mock_settings):
            sections, warnings, page_cap_status = inc_mod._vision_pages_with_sweep(
                data,
                pages=[1, 2, 3],
                file_name="test.pdf",
                image_parser=parser,
            )
        # 모든 페이지 호출 — cap 영향 0
        self.assertEqual(parser.parse.call_count, 3)
        self.assertEqual(len(sections), 3)
        self.assertIsNone(page_cap_status)


class TestIncrementalMaxSweepsDefault(unittest.TestCase):
    """S2 D3 P1-1 — `_MAX_SWEEPS` 의 ENV fallback default 가 extract.py 와 통일됨을 보호.

    같은 ENV 키 (`JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS`) 를 공유하면서 fallback 이 갈리면
    ENV 미설정 시 incremental 흐름이 cost/latency 1.5배. master plan §7.3 정합 위반.
    """

    def test_max_sweeps_env_default_is_2(self) -> None:
        # ENV 미설정 상태로 incremental 모듈 재로드 → default 2 확인.
        # 동시에 extract.py 의 `_VISION_ENRICH_MAX_SWEEPS` 와 동일 값임도 확인 (단일 진실원).
        import importlib
        from unittest.mock import patch

        env_without_key = {
            k: v
            for k, v in os.environ.items()
            if k != "JETRAG_PDF_VISION_ENRICH_MAX_SWEEPS"
        }
        with patch.dict(os.environ, env_without_key, clear=True):
            # HF_API_TOKEN 은 모듈 import 단계 의존이라 보존.
            os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
            import app.ingest.incremental as inc_reload
            import app.ingest.stages.extract as extract_reload

            importlib.reload(inc_reload)
            importlib.reload(extract_reload)

            self.assertEqual(inc_reload._MAX_SWEEPS, 2)
            self.assertEqual(extract_reload._VISION_ENRICH_MAX_SWEEPS, 2)
            self.assertEqual(
                inc_reload._MAX_SWEEPS,
                extract_reload._VISION_ENRICH_MAX_SWEEPS,
                "incremental._MAX_SWEEPS 와 extract._VISION_ENRICH_MAX_SWEEPS 의 default 가 갈리면 "
                "ENV 미설정 운영자에게 cost/latency 차이 발생.",
            )


class TestIncrementalReingestSafetyForS2D5(unittest.TestCase):
    """S2 D5 phase 1 명세 §8.1 B — 데이터센터 PDF reingest 안전성 회귀 보호.

    신규 3 케이스:
    1. 기존 (vision) p.N section 인 page 는 missing 추정에서 제외 (중복 호출 방지)
    2. 신규 chunks 의 metadata.vision_incremental == True (UI/디버깅용 플래그)
    3. 다른 doc_id 의 chunks 는 sweep 영향 0 (cross-doc 격리)

    DB 의존성 0 — 모든 supabase 호출 mock.
    """

    def test_reingest_skips_pages_with_existing_vision_section(self) -> None:
        """`_vision_processed_pages` — section_title startswith `(vision) p.` 인 row 만 set 진입.

        같은 doc_id 의 chunks 중:
        - section_title="(vision) p.3 표" → page 3 processed
        - section_title="(vision) p.5"   → page 5 processed
        - section_title="본문"            → 제외 (text 청크는 missing 판정 영향 0)
        - section_title=None              → 제외
        """
        from app.ingest import incremental as inc_mod

        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"page": 3, "section_title": "(vision) p.3 표"},
            {"page": 5, "section_title": "(vision) p.5"},
            {"page": 1, "section_title": "본문"},  # 제외
            {"page": 2, "section_title": None},  # 제외
            {"page": 4, "section_title": "(vision) p.4 그래프"},
        ]

        processed = inc_mod._vision_processed_pages(client, doc_id="doc-x")

        # vision section 인 page (3, 4, 5) 만 processed.
        self.assertEqual(processed, {3, 4, 5})

        # 누락 페이지 추정 — 가령 total_pages=6 이면 missing = {1,2,6}.
        all_pages = set(range(1, 7))
        missing = sorted(all_pages - processed)
        self.assertEqual(missing, [1, 2, 6])

        # supabase.table 호출 인자 검증 — chunks 테이블, doc_id 필터.
        client.table.assert_called_with("chunks")
        client.table.return_value.select.return_value.eq.assert_called_with(
            "doc_id", "doc-x",
        )

    def test_reingest_inserts_chunks_with_metadata_flag(self) -> None:
        """`_sections_to_chunks` — 신규 ChunkRecord 의 metadata.vision_incremental == True.

        S2 D5 phase 1 의 디버깅·UI 용 플래그 — 다음 측정 SQL 에서
        `metadata->>'vision_incremental' = 'true'` 로 신규 청크만 카운트 가능.
        """
        from app.ingest import incremental as inc_mod

        sections = [
            ExtractedSection(
                text="신규 vision 텍스트 1",
                page=6,
                section_title="(vision) p.6",
                bbox=None,
            ),
            ExtractedSection(
                text="신규 vision 텍스트 2",
                page=7,
                section_title="(vision) p.7 표",
                bbox=None,
            ),
        ]

        chunks = inc_mod._sections_to_chunks(
            sections, doc_id="doc-x", start_chunk_idx=42,
        )

        self.assertEqual(len(chunks), 2)
        for chunk in chunks:
            self.assertEqual(chunk.doc_id, "doc-x")
            # metadata.vision_incremental 플래그 — 측정·롤백 분리 키.
            self.assertEqual(chunk.metadata, {"vision_incremental": True})

        # chunk_idx 순차 증가 — start_chunk_idx 부터.
        self.assertEqual(chunks[0].chunk_idx, 42)
        self.assertEqual(chunks[1].chunk_idx, 43)

        # page / section_title 보존.
        self.assertEqual(chunks[0].page, 6)
        self.assertEqual(chunks[1].page, 7)
        self.assertEqual(chunks[0].section_title, "(vision) p.6")
        self.assertEqual(chunks[1].section_title, "(vision) p.7 표")

    def test_reingest_preserves_other_doc_chunks(self) -> None:
        """`_vision_processed_pages` 가 doc_id 필터 정확 — cross-doc 청크 무영향.

        sweep 흐름 자체는 새 chunks insert 만 함 (기존 chunks DELETE X).
        그래도 `_vision_processed_pages` 의 eq("doc_id", target_doc) 가 정확해야
        다른 doc_id 의 chunks 가 missing page 판정에 잘못 들어가지 않음.
        """
        from app.ingest import incremental as inc_mod

        # supabase mock — eq("doc_id", X) 호출 인자에 따라 다른 결과 반환.
        client = MagicMock()
        responses_by_doc = {
            "doc-target": [
                {"page": 2, "section_title": "(vision) p.2"},
                {"page": 4, "section_title": "(vision) p.4"},
            ],
            "doc-other": [
                {"page": 1, "section_title": "(vision) p.1"},
                {"page": 9, "section_title": "(vision) p.9"},
            ],
        }

        def eq_side_effect(col, val):
            chain = MagicMock()
            chain.execute.return_value.data = responses_by_doc.get(val, [])
            return chain

        client.table.return_value.select.return_value.eq.side_effect = eq_side_effect

        # 1) target doc — 본인 vision page 만 추출.
        processed_target = inc_mod._vision_processed_pages(client, doc_id="doc-target")
        self.assertEqual(processed_target, {2, 4})

        # 2) other doc — 본인 page 만. cross-talk 없음.
        processed_other = inc_mod._vision_processed_pages(client, doc_id="doc-other")
        self.assertEqual(processed_other, {1, 9})

        # 두 doc 모두 같은 chunks 테이블에서 select — eq 필터로만 격리.
        # supabase-py eq 호출 = 2회 (각 doc 마다 1회), 각각 다른 doc_id 인자.
        eq_calls = client.table.return_value.select.return_value.eq.call_args_list
        self.assertEqual(len(eq_calls), 2)
        passed_doc_ids = sorted(call.args[1] for call in eq_calls)
        self.assertEqual(passed_doc_ids, ["doc-other", "doc-target"])


if __name__ == "__main__":
    unittest.main()
