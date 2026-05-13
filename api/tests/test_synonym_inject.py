"""M1 W-2 (S4-D) — 인제스트 단계 동의어 후보 사전 단위 테스트.

검증 범위
- synonym_dict.lookup_synonyms — 키→값 / 양방향(값→키) / 미등록 빈
- synonym_inject.collect_synonym_candidates — ENV OFF→[] / ON+키→후보·dedupe·cap /
  이미 text 에 있는 동의어 제외 / doc_llm_pairs term 매칭
- synonym_inject.inject_marker / strip_synonym_marker — 포맷·NFC·왕복 복원·no-op
- synonym_inject.generate_doc_llm_pairs — ENV OFF→[] / mock provider JSON 파싱 / 실패→[]
- chunk._to_chunk_records 통합 — ENV OFF 시 기존 동작 보존 / ON 시 마커·metadata /
  키 없는 section 무변경 / entities 공존 / vision-derived 캡션 키 → `[표:...]...[검색어:...]`
- factory.get_llm_provider("synonym") — ValueError 없이 lite provider (lazy mock)
- chunk_filter 회귀 — ENV ON + 마커 붙은 일반 본문 → table_noise 오탐 안 함

stdlib unittest only — LLM/DB/HF 외부 호출 0 (mock·no-op).
"""

from __future__ import annotations

import os
import unittest
import unicodedata
from unittest.mock import patch

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")
os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from app.adapters.parser import ExtractedSection
from app.ingest.stages.chunk import _to_chunk_records
from app.services import synonym_inject
from app.services.synonym_dict import iter_dict_keys, lookup_synonyms

_ENV_ENABLED = "JETRAG_SYNONYM_INJECTION_ENABLED"
_ENV_LLM = "JETRAG_SYNONYM_INJECTION_LLM"


def _make_section(
    text: str,
    *,
    page: int = 1,
    section_title: str = "본문",
    metadata: dict | None = None,
) -> ExtractedSection:
    return ExtractedSection(
        text=text,
        page=page,
        section_title=section_title,
        bbox=None,
        metadata=metadata or {},
    )


class SynonymDictTest(unittest.TestCase):
    def test_lookup_key_to_values(self) -> None:
        syns = lookup_synonyms("쏘나타")
        self.assertIn("sonata", syns)
        self.assertIn("Sonata", syns)
        self.assertNotIn("쏘나타", syns)  # 자기 자신 제외

    def test_lookup_value_to_key_bidirectional(self) -> None:
        # "전체길이" 는 "전장" 의 값 → 키 "전장" + 같은 그룹 다른 값들 반환
        syns = lookup_synonyms("전체길이")
        self.assertIn("전장", syns)
        self.assertNotIn("전체길이", syns)

    def test_lookup_unregistered_returns_empty(self) -> None:
        self.assertEqual(lookup_synonyms("존재하지않는토큰"), [])

    def test_dict_keys_are_not_overly_generic(self) -> None:
        # senior-planner 명세 — 단독 일반 명사 키 금지 (2어절+ 또는 도메인 한정 어휘).
        banned = {"정보", "관리", "규정"}
        for key in iter_dict_keys():
            self.assertNotIn(key, banned, f"너무 일반적인 키: {key!r}")


class CollectCandidatesTest(unittest.TestCase):
    def test_env_off_returns_empty(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_ENV_ENABLED, None)
            self.assertEqual(
                synonym_inject.collect_synonym_candidates("쏘나타 제원 안내"), []
            )

    def test_env_on_collects_for_dict_key(self) -> None:
        with patch.dict(os.environ, {_ENV_ENABLED: "true"}):
            cands = synonym_inject.collect_synonym_candidates("쏘나타 제원 안내")
        self.assertIn("sonata", cands)
        self.assertIn("Sonata", cands)

    def test_excludes_synonym_already_in_text(self) -> None:
        with patch.dict(os.environ, {_ENV_ENABLED: "true"}):
            # text 에 'sonata' 가 이미 있으면 후보에서 제외
            cands = synonym_inject.collect_synonym_candidates("쏘나타 sonata 제원")
        self.assertNotIn("sonata", cands)
        self.assertIn("Sonata", cands)

    def test_dedupe_and_cap(self) -> None:
        # 여러 키가 동시에 매칭 → cap 적용 + dedupe
        with patch.dict(os.environ, {_ENV_ENABLED: "true"}):
            cands = synonym_inject.collect_synonym_candidates(
                "쏘나타 전장 전폭 전고 윤거 트림 공차중량 제원표", cap_per_chunk=3
            )
        self.assertLessEqual(len(cands), 3)
        self.assertEqual(len(cands), len(set(cands)))

    def test_doc_llm_pairs_term_match(self) -> None:
        with patch.dict(os.environ, {_ENV_ENABLED: "true"}):
            cands = synonym_inject.collect_synonym_candidates(
                "본 안내서는 특수용어A 를 다룬다",
                doc_llm_pairs=[("특수용어A", ["대체표현1", "대체표현2"])],
            )
        self.assertIn("대체표현1", cands)
        self.assertIn("대체표현2", cands)

    def test_doc_llm_pairs_term_not_in_text_skipped(self) -> None:
        with patch.dict(os.environ, {_ENV_ENABLED: "true"}):
            cands = synonym_inject.collect_synonym_candidates(
                "본 안내서는 일반 내용만 다룬다",
                doc_llm_pairs=[("등장하지않는용어", ["대체표현1"])],
            )
        self.assertEqual(cands, [])

    def test_llm_candidate_brackets_sanitized(self) -> None:
        # senior-qa P2 — LLM 후보에 `[` / `]` 가 섞여도 inject 전 제거 → 마커 구조 보호.
        with patch.dict(os.environ, {_ENV_ENABLED: "true"}):
            cands = synonym_inject.collect_synonym_candidates(
                "본 안내서는 특수용어A 를 다룬다",
                doc_llm_pairs=[("특수용어A", ["대[체]표현", "정상표현"])],
            )
        self.assertIn("대체표현", cands)
        self.assertIn("정상표현", cands)
        for c in cands:
            self.assertNotIn("[", c)
            self.assertNotIn("]", c)
        # sanitize 한 후보로 마커를 만들어도 왕복 복원이 깨지지 않음.
        injected = synonym_inject.inject_marker("본문.", cands)
        self.assertEqual(synonym_inject.strip_synonym_marker(injected), "본문.")


class InjectStripMarkerTest(unittest.TestCase):
    def test_inject_empty_candidates_returns_text(self) -> None:
        self.assertEqual(synonym_inject.inject_marker("본문 내용", []), "본문 내용")

    def test_inject_exact_format(self) -> None:
        out = synonym_inject.inject_marker("본문 내용", ["sonata", "Sonata"])
        self.assertEqual(out, "본문 내용\n\n[검색어: sonata Sonata]")

    def test_inject_result_is_nfc(self) -> None:
        # NFD 입력도 NFC 로 정규화되어 나옴
        nfd_text = unicodedata.normalize("NFD", "가나다")
        out = synonym_inject.inject_marker(nfd_text, ["sonata"])
        self.assertEqual(out, unicodedata.normalize("NFC", out))

    def test_strip_roundtrip(self) -> None:
        original = "본문 내용입니다."
        injected = synonym_inject.inject_marker(original, ["sonata", "Sonata"])
        self.assertEqual(synonym_inject.strip_synonym_marker(injected), original)

    def test_strip_no_op_when_no_marker(self) -> None:
        text = "마커 없는 일반 본문 [참고: 이건 마커 아님]"
        self.assertEqual(synonym_inject.strip_synonym_marker(text), text)

    def test_strip_empty_text(self) -> None:
        self.assertEqual(synonym_inject.strip_synonym_marker(""), "")


class GenerateDocLlmPairsTest(unittest.TestCase):
    def test_env_off_returns_empty(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_ENV_LLM, None)
            self.assertEqual(synonym_inject.generate_doc_llm_pairs("본문 텍스트"), [])

    def test_env_on_parses_provider_json(self) -> None:
        class _FakeProvider:
            def complete(self, messages, **kwargs):  # noqa: ANN001, ANN002, ANN003
                return (
                    '{"pairs":[{"term":"비식별화","synonyms":["가명처리","익명처리"]},'
                    '{"term":"동의서","synonyms":["informed consent"]}]}'
                )

        with patch.dict(os.environ, {_ENV_LLM: "true"}), patch(
            "app.adapters.factory.get_llm_provider", return_value=_FakeProvider()
        ):
            pairs = synonym_inject.generate_doc_llm_pairs("비식별화 동의서 관련 본문")
        self.assertEqual(
            pairs,
            [("비식별화", ["가명처리", "익명처리"]), ("동의서", ["informed consent"])],
        )

    def test_env_on_malformed_json_returns_empty(self) -> None:
        class _FakeProvider:
            def complete(self, messages, **kwargs):  # noqa: ANN001, ANN002, ANN003
                return "이건 JSON 이 아닙니다"

        with patch.dict(os.environ, {_ENV_LLM: "true"}), patch(
            "app.adapters.factory.get_llm_provider", return_value=_FakeProvider()
        ):
            self.assertEqual(synonym_inject.generate_doc_llm_pairs("본문"), [])

    def test_env_on_provider_raises_returns_empty(self) -> None:
        class _BoomProvider:
            def complete(self, messages, **kwargs):  # noqa: ANN001, ANN002, ANN003
                raise RuntimeError("quota exhausted")

        with patch.dict(os.environ, {_ENV_LLM: "true"}), patch(
            "app.adapters.factory.get_llm_provider", return_value=_BoomProvider()
        ):
            self.assertEqual(synonym_inject.generate_doc_llm_pairs("본문"), [])

    def test_env_on_non_dict_json_returns_empty(self) -> None:
        class _FakeProvider:
            def complete(self, messages, **kwargs):  # noqa: ANN001, ANN002, ANN003
                return "[1, 2, 3]"

        with patch.dict(os.environ, {_ENV_LLM: "true"}), patch(
            "app.adapters.factory.get_llm_provider", return_value=_FakeProvider()
        ):
            self.assertEqual(synonym_inject.generate_doc_llm_pairs("본문"), [])


class ToChunkRecordsIntegrationTest(unittest.TestCase):
    def test_env_off_no_marker_no_metadata(self) -> None:
        # 사전 키 포함 본문이라도 ENV OFF → 마커·metadata 키 없음 (기존 동작 보존)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_ENV_ENABLED, None)
            text = "쏘나타 제원 안내. 전장과 전폭을 명시한다."
            records = _to_chunk_records(doc_id="d", sections=[_make_section(text)])
        self.assertEqual(records[0].text, unicodedata.normalize("NFC", text))
        self.assertNotIn("synonym_candidates", records[0].metadata)
        self.assertNotIn("synonym_source", records[0].metadata)
        self.assertNotIn("[검색어:", records[0].text)

    def test_env_on_injects_marker_and_metadata(self) -> None:
        with patch.dict(os.environ, {_ENV_ENABLED: "true"}):
            text = "쏘나타 제원 안내입니다."
            records = _to_chunk_records(doc_id="d", sections=[_make_section(text)])
        rec = records[0]
        self.assertIn("[검색어:", rec.text)
        self.assertTrue(rec.text.startswith("쏘나타 제원 안내입니다."))
        self.assertIn("synonym_candidates", rec.metadata)
        self.assertEqual(rec.metadata["synonym_source"], "dict")
        # char_range 가 마커 부착 후 길이로 기록됨
        self.assertEqual(rec.char_range, (0, len(rec.text)))

    def test_env_on_section_without_dict_key_no_marker(self) -> None:
        with patch.dict(os.environ, {_ENV_ENABLED: "true"}):
            text = "이 단락에는 사전에 등록된 어휘가 전혀 없습니다."
            records = _to_chunk_records(doc_id="d", sections=[_make_section(text)])
        self.assertNotIn("[검색어:", records[0].text)
        self.assertNotIn("synonym_candidates", records[0].metadata)

    def test_env_on_coexists_with_entities(self) -> None:
        with patch.dict(os.environ, {_ENV_ENABLED: "true"}):
            # entities (날짜·금액) + 사전 키(쏘나타) 동시
            text = "쏘나타 안내. 2024년 4월 30일 시행, 회비 50,000원."
            records = _to_chunk_records(doc_id="d", sections=[_make_section(text)])
        meta = records[0].metadata
        self.assertIn("entities", meta)
        self.assertIn("synonym_candidates", meta)
        self.assertIn("[검색어:", records[0].text)

    def test_env_on_vision_derived_caption_key_order(self) -> None:
        # vision-derived (section_title `(vision)` prefix) + table_caption 에 사전 키 포함
        with patch.dict(os.environ, {_ENV_ENABLED: "true"}):
            sec = _make_section(
                "표의 본문 텍스트입니다.",
                section_title="(vision) p.1",
                metadata={"table_caption": "쏘나타 제원표"},
            )
            records = _to_chunk_records(doc_id="d", sections=[sec])
        txt = records[0].text
        self.assertIn("[표: 쏘나타 제원표]", txt)
        self.assertIn("[검색어:", txt)
        # 순서: [표:...] 가 [검색어:...] 보다 먼저
        self.assertLess(txt.index("[표:"), txt.index("[검색어:"))
        self.assertIn("synonym_candidates", records[0].metadata)

    def test_env_on_doc_llm_pairs_source_marked(self) -> None:
        with patch.dict(os.environ, {_ENV_ENABLED: "true"}):
            records = _to_chunk_records(
                doc_id="d",
                sections=[_make_section("특수용어A 를 다루는 본문입니다.")],
                doc_llm_pairs=[("특수용어A", ["대체표현1"])],
            )
        rec = records[0]
        self.assertIn("[검색어:", rec.text)
        self.assertEqual(rec.metadata["synonym_source"], "dict+llm")


class FactorySynonymPurposeTest(unittest.TestCase):
    def test_get_llm_provider_synonym_returns_lite(self) -> None:
        from app.adapters import factory

        # ENV override 없음 → default gemini-2.5-flash-lite
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JETRAG_LLM_MODEL_SYNONYM", None)
            os.environ.pop("JETRAG_LLM_PROVIDER", None)
            with patch(
                "app.adapters.impl.gemini_llm.GeminiLLMProvider"
            ) as mock_cls:
                factory.get_llm_provider("synonym")
        mock_cls.assert_called_once_with(model="gemini-2.5-flash-lite")

    def test_synonym_in_default_models(self) -> None:
        from app.adapters import factory

        self.assertEqual(
            factory._GEMINI_DEFAULT_MODELS["synonym"], "gemini-2.5-flash-lite"
        )


class ChunkFilterRegressionTest(unittest.TestCase):
    def test_marker_appended_body_not_table_noise(self) -> None:
        # ENV ON + 사전 키 포함 일반 본문 chunk → 마커 부착 후에도 table_noise 오탐 안 함.
        from app.ingest.stages.chunk_filter import _classify_chunk

        with patch.dict(os.environ, {_ENV_ENABLED: "true"}):
            text = (
                "쏘나타 차량은 한국에서 생산되는 중형 세단으로, 다양한 트림과 옵션을 "
                "제공합니다. 전장과 전폭 정보는 제원표에 명시되어 있으며, 연비와 안전 "
                "사양도 함께 안내됩니다. 본 안내서는 구매 검토에 참고하시기 바랍니다."
            )
            records = _to_chunk_records(doc_id="d", sections=[_make_section(text)])
        rec = records[0]
        self.assertIn("[검색어:", rec.text)
        # 마커 붙은 chunk 를 chunk_filter 가 table_noise 로 분류하지 않음
        self.assertIsNone(_classify_chunk(rec, header_footer_texts=set()))


class AnswerChunkMarkerStripTest(unittest.TestCase):
    """senior-qa P1 — `/answer` 의 chunk text 노출 지점(`_gather_chunks`·`_enrich_rows`)에서

    동의어 마커가 제거돼야 함 (LLM 컨텍스트·sources[].text·Ragas contexts 오염 방지).
    DB·HF 는 mock — 외부 호출 0. `search.py` snippet 경로와 동일 정책.
    """

    _MARKED = "비식별화 가이드 본문.\n\n[검색어: 가명처리 익명처리]"
    _PLAIN = "마커 없는 일반 본문 단락입니다."

    @staticmethod
    def _client_with_chunks(rows: list[dict], chunks: list[dict]) -> object:
        from unittest.mock import MagicMock

        client = MagicMock()
        rpc_resp = MagicMock()
        rpc_resp.data = rows
        rpc_call = MagicMock()
        rpc_call.execute.return_value = rpc_resp
        client.rpc.return_value = rpc_call

        def _table(name: str):  # noqa: ANN202
            chain = MagicMock()
            chain.select.return_value = chain
            chain.in_.return_value = chain
            if name == "chunks":
                chain.execute.return_value.data = chunks
            else:  # documents
                chain.execute.return_value.data = [{"id": "doc1", "title": "비식별화 가이드"}]
            return chain

        client.table.side_effect = _table
        return client

    @staticmethod
    def _provider_mock() -> object:
        from unittest.mock import MagicMock

        p = MagicMock()
        p.embed_query.return_value = [0.0] * 1024
        return p

    def _run_gather_chunks(self, chunk_text: str) -> list[dict]:
        from app.routers import answer as answer_module

        rows = [{"chunk_id": "c1", "doc_id": "doc1", "dense_rank": 1, "rrf_score": 0.5}]
        chunks = [
            {"id": "c1", "doc_id": "doc1", "chunk_idx": 0, "text": chunk_text, "page": 1, "section_title": "본문"}
        ]
        client = self._client_with_chunks(rows, chunks)
        with patch.object(answer_module, "get_supabase_client", return_value=client), patch.object(
            answer_module, "get_bgem3_provider", return_value=self._provider_mock()
        ):
            enriched, _ = answer_module._gather_chunks(
                query="비식별화", doc_id=None, top_k=5, user_id="u1"
            )
        return enriched

    def _run_enrich_rows(self, chunk_text: str) -> list[dict]:
        from app.routers import answer as answer_module

        rows = [{"chunk_id": "c1", "doc_id": "doc1", "rrf_score": 0.5}]
        chunks = [
            {"id": "c1", "doc_id": "doc1", "chunk_idx": 0, "text": chunk_text, "page": 1, "section_title": "본문"}
        ]
        client = self._client_with_chunks(rows, chunks)
        return answer_module._enrich_rows(client, rows)

    def test_gather_chunks_strips_marker(self) -> None:
        enriched = self._run_gather_chunks(self._MARKED)
        self.assertEqual(len(enriched), 1)
        self.assertNotIn("[검색어:", enriched[0]["text"])
        self.assertEqual(enriched[0]["text"], "비식별화 가이드 본문.")

    def test_gather_chunks_no_marker_is_noop(self) -> None:
        enriched = self._run_gather_chunks(self._PLAIN)
        self.assertEqual(enriched[0]["text"], self._PLAIN)

    def test_enrich_rows_strips_marker(self) -> None:
        enriched = self._run_enrich_rows(self._MARKED)
        self.assertEqual(len(enriched), 1)
        self.assertNotIn("[검색어:", enriched[0]["text"])
        self.assertEqual(enriched[0]["text"], "비식별화 가이드 본문.")

    def test_enrich_rows_no_marker_is_noop(self) -> None:
        enriched = self._run_enrich_rows(self._PLAIN)
        self.assertEqual(enriched[0]["text"], self._PLAIN)


if __name__ == "__main__":
    unittest.main()
