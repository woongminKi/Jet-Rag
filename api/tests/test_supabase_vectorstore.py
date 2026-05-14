"""SupabasePgVectorStore — robustness fix 단위 테스트 (2026-05-14).

검증 범위
- `_strip_null_bytes` 재귀 처리 (str / dict / list / tuple / None / int)
- `_serialize_chunk` 가 NULL byte 정제 (text·section_title·metadata)
- `upsert_chunks` batch split (`settings.chunk_upsert_batch_size` 기준)
- `chunk_upsert_batch_size` config 기본값 + ENV override + clamp

stdlib unittest 만 사용 — Supabase client mock 으로 DB 호출 0.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from app.adapters.impl.supabase_vectorstore import SupabasePgVectorStore
from app.adapters.vectorstore import ChunkRecord
from app.config import get_settings


def _make_chunk(idx: int, **overrides) -> ChunkRecord:
    defaults = dict(
        doc_id="doc-A",
        chunk_idx=idx,
        text=f"chunk text {idx}",
        page=idx,
        section_title=f"section {idx}",
        sparse_json={},
        metadata={},
        flags={},
        bbox=None,
        dense_vec=None,
        char_range=None,
        chunk_id=None,
    )
    defaults.update(overrides)
    return ChunkRecord(**defaults)


class StripNullBytesTest(unittest.TestCase):
    """`_strip_null_bytes` 재귀 NULL byte 제거 검증."""

    def test_string_with_null_byte(self) -> None:
        self.assertEqual(
            SupabasePgVectorStore._strip_null_bytes("hello\x00world"), "helloworld"
        )

    def test_string_without_null_byte_unchanged(self) -> None:
        s = "정상 한국어 문자열 — no null byte"
        self.assertIs(SupabasePgVectorStore._strip_null_bytes(s), s)

    def test_dict_recursive(self) -> None:
        result = SupabasePgVectorStore._strip_null_bytes(
            {"a": "x\x00y", "b": {"c": "z\x00"}, "d": 1}
        )
        self.assertEqual(result, {"a": "xy", "b": {"c": "z"}, "d": 1})

    def test_list_recursive(self) -> None:
        result = SupabasePgVectorStore._strip_null_bytes(["x\x00", {"k": "v\x00"}, 3])
        self.assertEqual(result, ["x", {"k": "v"}, 3])

    def test_tuple_recursive(self) -> None:
        result = SupabasePgVectorStore._strip_null_bytes(("a\x00", "b"))
        self.assertEqual(result, ("a", "b"))

    def test_none_int_float_bool_passthrough(self) -> None:
        for v in (None, 0, 1.5, True, False):
            self.assertEqual(SupabasePgVectorStore._strip_null_bytes(v), v)

    def test_dense_vec_list_of_floats_unchanged(self) -> None:
        """list[float] 은 NULL byte 영역 아님 — 그대로."""
        vec = [0.1, -0.2, 1e-3, 0.0]
        result = SupabasePgVectorStore._strip_null_bytes(vec)
        self.assertEqual(result, vec)

    def test_multiple_null_bytes_in_single_string(self) -> None:
        self.assertEqual(
            SupabasePgVectorStore._strip_null_bytes("\x00a\x00b\x00"), "ab"
        )


class SerializeChunkNullByteTest(unittest.TestCase):
    """`_serialize_chunk` 의 NULL byte 정제 — text·section_title·metadata 통합."""

    def test_text_null_byte_stripped(self) -> None:
        chunk = _make_chunk(1, text="arXiv\x00\\u0000 mixed")
        row = SupabasePgVectorStore._serialize_chunk(chunk)
        self.assertEqual(row["text"], "arXiv\\u0000 mixed")

    def test_section_title_null_byte_stripped(self) -> None:
        chunk = _make_chunk(2, section_title="head\x00line")
        row = SupabasePgVectorStore._serialize_chunk(chunk)
        self.assertEqual(row["section_title"], "headline")

    def test_metadata_nested_null_byte_stripped(self) -> None:
        chunk = _make_chunk(
            3, metadata={"caption": "table\x00title", "page_label": "p.5"}
        )
        row = SupabasePgVectorStore._serialize_chunk(chunk)
        self.assertEqual(row["metadata"]["caption"], "tabletitle")
        self.assertEqual(row["metadata"]["page_label"], "p.5")

    def test_flags_nested_null_byte_stripped(self) -> None:
        chunk = _make_chunk(4, flags={"filter": "header\x00footer"})
        row = SupabasePgVectorStore._serialize_chunk(chunk)
        self.assertEqual(row["flags"]["filter"], "headerfooter")

    def test_clean_chunk_unchanged(self) -> None:
        chunk = _make_chunk(5, text="정상 한국어")
        row = SupabasePgVectorStore._serialize_chunk(chunk)
        self.assertEqual(row["text"], "정상 한국어")
        self.assertEqual(row["doc_id"], "doc-A")
        self.assertEqual(row["chunk_idx"], 5)


class UpsertChunksBatchSplitTest(unittest.TestCase):
    """`upsert_chunks` batch 분할 검증 — Supabase statement_timeout 회피."""

    def _make_store_with_mock(
        self, batch_size: int
    ) -> tuple[SupabasePgVectorStore, mock.MagicMock]:
        """Supabase client 를 MagicMock 으로 주입 + settings.chunk_upsert_batch_size patch."""
        store = SupabasePgVectorStore.__new__(SupabasePgVectorStore)
        store._client = mock.MagicMock()  # type: ignore[attr-defined]

        # settings cache 무시 — get_settings lru_cache patch
        fake_settings = mock.MagicMock()
        fake_settings.chunk_upsert_batch_size = batch_size
        return store, fake_settings

    def test_chunks_split_into_batches(self) -> None:
        """120 chunks + batch_size 50 → 3 회 upsert (50, 50, 20)."""
        store, fake_settings = self._make_store_with_mock(50)
        chunks = [_make_chunk(i) for i in range(120)]

        with mock.patch(
            "app.adapters.impl.supabase_vectorstore.get_settings",
            return_value=fake_settings,
        ):
            store.upsert_chunks(chunks)

        # client.table().upsert() 호출 횟수 3 (chain method 추적)
        table_calls = store._client.table.call_args_list  # type: ignore[attr-defined]
        self.assertEqual(len(table_calls), 3)
        # 각 호출이 chunks 테이블에 대한 것인지
        for call in table_calls:
            self.assertEqual(call.args[0], "chunks")

        # upsert payload 크기 50, 50, 20 검증
        upsert_calls = (
            store._client.table.return_value.upsert.call_args_list  # type: ignore[attr-defined]
        )
        self.assertEqual([len(c.args[0]) for c in upsert_calls], [50, 50, 20])

    def test_empty_chunks_no_call(self) -> None:
        """빈 chunks → upsert 호출 0."""
        store, fake_settings = self._make_store_with_mock(50)
        with mock.patch(
            "app.adapters.impl.supabase_vectorstore.get_settings",
            return_value=fake_settings,
        ):
            store.upsert_chunks([])
        store._client.table.assert_not_called()  # type: ignore[attr-defined]

    def test_single_batch_when_chunks_le_batch_size(self) -> None:
        """40 chunks + batch_size 50 → 1 회 upsert."""
        store, fake_settings = self._make_store_with_mock(50)
        chunks = [_make_chunk(i) for i in range(40)]
        with mock.patch(
            "app.adapters.impl.supabase_vectorstore.get_settings",
            return_value=fake_settings,
        ):
            store.upsert_chunks(chunks)
        upsert_calls = (
            store._client.table.return_value.upsert.call_args_list  # type: ignore[attr-defined]
        )
        self.assertEqual(len(upsert_calls), 1)
        self.assertEqual(len(upsert_calls[0].args[0]), 40)

    def test_batch_size_clamp_minimum_1(self) -> None:
        """batch_size <= 0 인 settings → 최소 1 로 clamp (chunk 1개씩 upsert)."""
        store, fake_settings = self._make_store_with_mock(0)
        chunks = [_make_chunk(i) for i in range(3)]
        with mock.patch(
            "app.adapters.impl.supabase_vectorstore.get_settings",
            return_value=fake_settings,
        ):
            store.upsert_chunks(chunks)
        upsert_calls = (
            store._client.table.return_value.upsert.call_args_list  # type: ignore[attr-defined]
        )
        self.assertEqual(len(upsert_calls), 3)

    def test_null_byte_sanitized_in_batch_payload(self) -> None:
        """batch 안 chunks 의 NULL byte 도 sanitize 되어 payload 에 반영."""
        store, fake_settings = self._make_store_with_mock(50)
        chunks = [_make_chunk(0, text="a\x00b"), _make_chunk(1, text="c\x00d")]
        with mock.patch(
            "app.adapters.impl.supabase_vectorstore.get_settings",
            return_value=fake_settings,
        ):
            store.upsert_chunks(chunks)
        payload = (
            store._client.table.return_value.upsert.call_args.args[0]  # type: ignore[attr-defined]
        )
        self.assertEqual(payload[0]["text"], "ab")
        self.assertEqual(payload[1]["text"], "cd")


class ChunkUpsertBatchSizeSettingsTest(unittest.TestCase):
    """`chunk_upsert_batch_size` settings — default + ENV override + clamp."""

    def setUp(self) -> None:
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()
        os.environ.pop("JETRAG_CHUNK_UPSERT_BATCH_SIZE", None)

    def test_default_50(self) -> None:
        s = get_settings()
        self.assertEqual(s.chunk_upsert_batch_size, 50)

    def test_env_override(self) -> None:
        os.environ["JETRAG_CHUNK_UPSERT_BATCH_SIZE"] = "20"
        s = get_settings()
        self.assertEqual(s.chunk_upsert_batch_size, 20)

    def test_env_invalid_falls_back_default(self) -> None:
        os.environ["JETRAG_CHUNK_UPSERT_BATCH_SIZE"] = "not-a-number"
        s = get_settings()
        self.assertEqual(s.chunk_upsert_batch_size, 50)

    def test_env_zero_clamped_to_1(self) -> None:
        os.environ["JETRAG_CHUNK_UPSERT_BATCH_SIZE"] = "0"
        s = get_settings()
        self.assertEqual(s.chunk_upsert_batch_size, 1)

    def test_env_negative_clamped_to_1(self) -> None:
        os.environ["JETRAG_CHUNK_UPSERT_BATCH_SIZE"] = "-5"
        s = get_settings()
        self.assertEqual(s.chunk_upsert_batch_size, 1)


if __name__ == "__main__":
    unittest.main()
