"""W7 Day 5 — chunk → chunk_filter → load → embed 4 stage e2e mock 테스트.

배경 (work-log/2026-05-02 ~ 03 17 Day 핸드오프)
- 단위 테스트 160 개는 모두 stage 개별 함수 단위. stage chain 전체 흐름은 검증 부재.
- W4·W5·W6 누적 변경 (DE-65 chunks 555→1256, DOCX 추가, chunk_filter 마킹) 후
  통합 회귀 보호가 비어있어 다음 변경에서 어느 stage 가 깨질지 가시성 0.

본 테스트는 4 stage (chunk·chunk_filter·load·embed) 의 협력 흐름을 검증한다.
extract 는 기존 parser 단위 테스트로 충분 / content_gate·tag_summarize·doc_embed·dedup
은 graceful 또는 외부 LLM 의존이라 우선순위 낮음.

설계 결정 (트레이드오프)
- stage 함수 시그니처 변경 NO — 모든 stage 가 `get_supabase_client` / `get_bgem3_provider`
  를 모듈 namespace 에서 호출하므로, `unittest.mock.patch` 로 각 모듈의 import 바인딩
  을 가로챈다. 비즈니스 로직 코드 손대지 않고 회귀 보호 확보가 목적.
- FakeSupabaseClient 는 명시적 dict 기반 — `_tables` 가 직접 inspectable 해서
  assertion 이 곧 "DB 에 무엇이 들어갔는지" 확인.
- EmbeddingResult / ExtractionResult / ChunkRecord 등 실제 dataclass 를 쓰고,
  fake provider 는 `embed_batch` 만 재정의 (HF token / httpx 의존 회피).

stdlib unittest + mock 만 사용 — 의존성 추가 0 (CLAUDE.md 준수).
"""

from __future__ import annotations

import os
import unittest
from typing import Any
from unittest.mock import patch

# import 단계에서 HF_API_TOKEN 체크하는 모듈이 있어 dummy 주입 (실 호출은 mock 으로 차단).
# SUPABASE_URL / KEY 는 setdefault 하지 말 것 — `test_search_user_isolation` 등이
# `_has_supabase_env()` 로 skip 결정하는데 dummy 값이 있으면 skip 되지 않고 실 호출 시도하여
# ConnectError 발생. 본 테스트는 `get_supabase_client` 자체를 patch 하므로 settings 평가 X.
os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")


# ====================================================================
# FakeSupabaseClient — chained API mock + 명시적 state dict
# ====================================================================


class _FakeQueryResponse:
    """supabase-py 의 `APIResponse` 흉내 — `.data` + `.count` (W10 Day 1 한계 #20)."""

    __slots__ = ("data", "count")

    def __init__(
        self, data: list[dict[str, Any]], count: int | None = None
    ) -> None:
        self.data = data
        # PostgREST `select(count="exact")` 응답 시 채워짐. None = count 미요청.
        self.count = count


class _FakeTableQuery:
    """supabase-py table chain (`.table().select().eq().execute()`) 시뮬레이터.

    동작 모델
    - 모든 chain 메서드는 self 반환해 chaining 지원
    - `_op` 에 현재 연산 (`select` / `insert` / `update` / `upsert` / `delete`) 기록
    - `_filters` 에 `eq` / `is_` / `in_` 누적
    - `_payload` 에 insert/update/upsert 페이로드
    - `execute()` 시점에 `_apply()` 가 부모 client 의 `_tables` 에 실제 mutation 적용
    """

    def __init__(self, client: "FakeSupabaseClient", name: str) -> None:
        self._client = client
        self._name = name
        self._op: str | None = None
        self._payload: list[dict[str, Any]] | dict[str, Any] | None = None
        self._filters: list[tuple[str, str, Any]] = []  # (op, col, value)
        self._on_conflict: str | None = None
        self._select_cols: str = "*"
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None
        self._count_mode: str | None = None  # PostgREST `count="exact"` 등

    # ---------------------- chain 빌더 ----------------------

    def select(
        self, cols: str = "*", *, count: str | None = None
    ) -> "_FakeTableQuery":
        self._op = "select"
        self._select_cols = cols
        # supabase-py 의 `select(cols, count="exact")` — count 모드 기록.
        self._count_mode = count
        return self

    def insert(
        self, payload: dict[str, Any] | list[dict[str, Any]]
    ) -> "_FakeTableQuery":
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload: dict[str, Any]) -> "_FakeTableQuery":
        self._op = "update"
        self._payload = payload
        return self

    def upsert(
        self,
        payload: list[dict[str, Any]],
        *,
        on_conflict: str | None = None,
    ) -> "_FakeTableQuery":
        self._op = "upsert"
        self._payload = payload
        self._on_conflict = on_conflict
        return self

    def delete(self) -> "_FakeTableQuery":
        self._op = "delete"
        return self

    def eq(self, col: str, value: Any) -> "_FakeTableQuery":
        self._filters.append(("eq", col, value))
        return self

    def is_(self, col: str, value: Any) -> "_FakeTableQuery":
        # supabase-py 는 `.is_("col", "null")` 형태로 NULL 검사.
        self._filters.append(("is", col, value))
        return self

    def in_(self, col: str, values: list[Any]) -> "_FakeTableQuery":
        self._filters.append(("in", col, values))
        return self

    def neq(self, col: str, value: Any) -> "_FakeTableQuery":
        self._filters.append(("neq", col, value))
        return self

    @property
    def not_(self) -> "_FakeTableQueryNot":
        """supabase-py 의 `.not_.is_(col, val)` 체인 흉내 — 다음 호출을 negate 한다."""
        return _FakeTableQueryNot(self)

    def order(self, col: str, *, desc: bool = False) -> "_FakeTableQuery":
        self._order = (col, desc)
        return self

    def limit(self, n: int) -> "_FakeTableQuery":
        self._limit = n
        return self

    # ---------------------- terminal ----------------------

    def execute(self) -> _FakeQueryResponse:
        rows = self._client._tables.setdefault(self._name, [])
        if self._op == "select":
            return self._exec_select(rows)
        if self._op == "insert":
            return self._exec_insert(rows)
        if self._op == "update":
            return self._exec_update(rows)
        if self._op == "upsert":
            return self._exec_upsert(rows)
        if self._op == "delete":
            return self._exec_delete(rows)
        raise RuntimeError(f"FakeTableQuery: unsupported op={self._op}")

    # ---------------------- op 구현 ----------------------

    def _exec_select(self, rows: list[dict[str, Any]]) -> _FakeQueryResponse:
        out = [r for r in rows if self._matches_filters(r)]
        if self._order is not None:
            col, desc = self._order
            out.sort(key=lambda r: r.get(col) or 0, reverse=desc)
        # count 모드: limit 적용 *전* 의 매칭 row 수 (PostgREST 동작).
        count_value = len(out) if self._count_mode else None
        if self._limit is not None:
            out = out[: self._limit]
        return _FakeQueryResponse(out, count=count_value)

    def _exec_insert(self, rows: list[dict[str, Any]]) -> _FakeQueryResponse:
        payload = self._payload
        items = payload if isinstance(payload, list) else [payload]
        inserted: list[dict[str, Any]] = []
        for item in items:
            new_row = dict(item)
            # ingest_logs 는 INTEGER PK serial — 실제 시퀀스 흉내.
            if self._name == "ingest_logs" and "id" not in new_row:
                new_row["id"] = self._client._next_log_id()
            rows.append(new_row)
            inserted.append(new_row)
        return _FakeQueryResponse(inserted)

    def _exec_update(self, rows: list[dict[str, Any]]) -> _FakeQueryResponse:
        assert isinstance(self._payload, dict)
        updated: list[dict[str, Any]] = []
        for r in rows:
            if not self._matches_filters(r):
                continue
            r.update(self._payload)
            updated.append(r)
        return _FakeQueryResponse(updated)

    def _exec_upsert(self, rows: list[dict[str, Any]]) -> _FakeQueryResponse:
        assert isinstance(self._payload, list)
        on_conflict_keys = (
            [k.strip() for k in self._on_conflict.split(",")]
            if self._on_conflict
            else ["id"]
        )
        upserted: list[dict[str, Any]] = []
        for item in self._payload:
            existing = next(
                (
                    r
                    for r in rows
                    if all(r.get(k) == item.get(k) for k in on_conflict_keys)
                ),
                None,
            )
            if existing is not None:
                existing.update(item)
                upserted.append(existing)
            else:
                new_row = dict(item)
                # chunks 는 UUID PK — 테스트 환경에서 단조 증가 id 부여.
                if self._name == "chunks" and "id" not in new_row:
                    new_row["id"] = f"chunk-{self._client._next_chunk_id()}"
                rows.append(new_row)
                upserted.append(new_row)
        return _FakeQueryResponse(upserted)

    def _exec_delete(self, rows: list[dict[str, Any]]) -> _FakeQueryResponse:
        kept: list[dict[str, Any]] = []
        deleted: list[dict[str, Any]] = []
        for r in rows:
            if self._matches_filters(r):
                deleted.append(r)
            else:
                kept.append(r)
        rows[:] = kept
        return _FakeQueryResponse(deleted)

    # ---------------------- filter 평가 ----------------------

    def _matches_filters(self, row: dict[str, Any]) -> bool:
        for op, col, value in self._filters:
            actual = self._resolve_column(row, col)
            if op == "eq":
                if actual != value:
                    return False
            elif op == "neq":
                if actual == value:
                    return False
            elif op == "is":
                # supabase-py 는 `.is_("col", "null")` 또는 `.is_("col", None)` 둘 다 사용.
                if value in ("null", None):
                    if actual is not None:
                        return False
                else:
                    if actual != value:
                        return False
            elif op == "not_is":
                # `.not_.is_("col", "null")` — IS NOT NULL
                if value in ("null", None):
                    if actual is None:
                        return False
                else:
                    if actual == value:
                        return False
            elif op == "in":
                if actual not in value:
                    return False
        return True

    @staticmethod
    def _resolve_column(row: dict[str, Any], col: str) -> Any:
        """JSONB path (`flags->>key`) 해석 — Supabase PostgREST 호환.

        예: `flags->>filtered_reason` → `row["flags"]["filtered_reason"]`.
        nested 한 단계만 — 본 fake 가 다루는 모든 케이스 충족.
        """
        if "->>" in col:
            top, key = col.split("->>", 1)
            container = row.get(top)
            if isinstance(container, dict):
                return container.get(key)
            return None
        return row.get(col)


class _FakeTableQueryNot:
    """supabase-py 의 `.not_` accessor — 다음 메서드 호출을 negate 한 filter 로 등록."""

    def __init__(self, parent: "_FakeTableQuery") -> None:
        self._parent = parent

    def is_(self, col: str, value: Any) -> "_FakeTableQuery":
        self._parent._filters.append(("not_is", col, value))
        return self._parent


class FakeSupabaseClient:
    """Supabase Client 흉내 — `.table(name)` 으로 `_FakeTableQuery` 반환.

    state
    - `_tables[name] = list[dict]` — 테이블별 행 모음. 테스트가 직접 inspect.
    - `_log_id_seq` / `_chunk_id_seq` — auto-increment 흉내.
    """

    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            "documents": [],
            "chunks": [],
            "ingest_jobs": [],
            "ingest_logs": [],
        }
        self._log_id_seq = 0
        self._chunk_id_seq = 0

    def table(self, name: str) -> _FakeTableQuery:
        return _FakeTableQuery(self, name)

    # ---------------------- helpers ----------------------

    def _next_log_id(self) -> int:
        self._log_id_seq += 1
        return self._log_id_seq

    def _next_chunk_id(self) -> int:
        self._chunk_id_seq += 1
        return self._chunk_id_seq


# ====================================================================
# Fake BGE-M3 provider — 실 EmbeddingResult 반환, embed_batch 호출 ledger
# ====================================================================


class FakeBGEM3Provider:
    """`EmbeddingProvider` 호환 — `embed_batch` 만 재정의.

    - `dense` = 1024 dim 더미 벡터 (chunk_idx 시드로 결정성 부여)
    - `embed_batch_calls` 에 호출 기록 (배치 횟수·텍스트 검증)
    """

    dense_dim: int = 1024

    def __init__(self) -> None:
        self.embed_batch_calls: list[list[str]] = []
        self.embed_calls: list[str] = []

    def embed_batch(self, texts: list[str]) -> list:
        from app.adapters.embedding import EmbeddingResult

        self.embed_batch_calls.append(list(texts))
        out: list[EmbeddingResult] = []
        for i, _ in enumerate(texts):
            # 결정성: 인덱스 기반 seed. (실 hash 비교가 아닌 차원·존재 검증용.)
            seed = 0.001 * (i + 1)
            out.append(EmbeddingResult(dense=[seed] * 1024, sparse={}))
        return out

    def embed(self, text: str):
        """단건 embed (doc_embed stage 가 사용). dense=[len(text)/1000]*1024 결정성."""
        from app.adapters.embedding import EmbeddingResult

        self.embed_calls.append(text)
        seed = max(0.001, min(1.0, len(text) / 1000))
        return EmbeddingResult(dense=[seed] * 1024, sparse={})


# ====================================================================
# 픽스처 헬퍼
# ====================================================================


def _make_extraction(sections: list[tuple[str, str | None]]) -> Any:
    """(text, section_title) tuple 리스트로 ExtractionResult 생성.

    page 는 1 부터 자동 증가 — 테스트 단순화 (전부 page=1 이면 chunk merge 가
    같은 페이지 내 병합 트리거되어 결과 검증이 불안정해지므로 의도적으로 분리).
    """
    from app.adapters.parser import ExtractedSection, ExtractionResult

    extracted = [
        ExtractedSection(text=text, page=i + 1, section_title=title)
        for i, (text, title) in enumerate(sections)
    ]
    return ExtractionResult(
        source_type="pdf",
        sections=extracted,
        raw_text="\n\n".join(t for t, _ in sections),
        warnings=[],
    )


def _seed_job(client: FakeSupabaseClient, job_id: str, doc_id: str) -> None:
    """ingest_jobs 에 더미 row 주입 — update_stage 등이 .eq("id", job_id) 매칭."""
    client._tables["ingest_jobs"].append(
        {
            "id": job_id,
            "doc_id": doc_id,
            "status": "queued",
            "current_stage": None,
            "attempts": 0,
        }
    )


def _seed_document(
    client: FakeSupabaseClient,
    doc_id: str,
    *,
    flags: dict[str, Any] | None = None,
) -> None:
    """documents 에 더미 row 주입 — content_gate `_merge_doc_flags` 가 select-then-update."""
    client._tables["documents"].append(
        {"id": doc_id, "flags": flags or {}}
    )


def _make_chunk(
    *, doc_id: str, chunk_idx: int, text: str, page: int = 1
) -> Any:
    """ChunkRecord 픽스처 — content_gate 입력용 (chunk·load stage 안 거치고 직접 생성)."""
    from app.adapters.vectorstore import ChunkRecord

    return ChunkRecord(
        doc_id=doc_id,
        chunk_idx=chunk_idx,
        text=text,
        page=page,
        section_title=None,
        bbox=None,
        char_range=(0, len(text)),
        metadata={},
    )


# ====================================================================
# E2EBaseTest — 모든 stage 모듈의 get_supabase_client + provider patch
# ====================================================================


class E2EBaseTest(unittest.TestCase):
    """공통 setUp — 4 stage 가 호출하는 외부 의존성 모두 차단.

    patch 대상 (모듈별 import 바인딩 — 각 모듈이 `from app.db import get_supabase_client`
    로 자기 namespace 에 함수 참조를 복사해 보유하므로, 모듈마다 따로 patch 필요)
    - app.db.client.get_supabase_client (lru_cache 의 원본 — `cache_clear()` 후 patch)
    - app.ingest.jobs.get_supabase_client (stage 컨텍스트 매니저가 호출)
    - app.ingest.stages.embed.get_supabase_client (chunks 재조회)
    - app.adapters.impl.supabase_vectorstore.get_supabase_client (load 의 upsert)
    - app.ingest.stages.embed.get_bgem3_provider (실 HF 호출 차단)
    """

    def setUp(self) -> None:
        # lru_cache 비움 — 이전 테스트가 실 client 캐싱했을 가능성 차단.
        from app.db.client import get_supabase_client as _real_db_factory
        from app.adapters.impl.bgem3_hf_embedding import (
            get_bgem3_provider as _real_provider_factory,
        )

        _real_db_factory.cache_clear()
        _real_provider_factory.cache_clear()

        self.fake_client = FakeSupabaseClient()
        self.fake_provider = FakeBGEM3Provider()

        # 모듈별 patch — 각 stage 의 namespace 바인딩 가로채기.
        self._patches = [
            patch(
                "app.db.client.get_supabase_client",
                return_value=self.fake_client,
            ),
            patch(
                "app.ingest.jobs.get_supabase_client",
                return_value=self.fake_client,
            ),
            patch(
                "app.ingest.stages.embed.get_supabase_client",
                return_value=self.fake_client,
            ),
            patch(
                "app.adapters.impl.supabase_vectorstore.get_supabase_client",
                return_value=self.fake_client,
            ),
            patch(
                "app.ingest.stages.embed.get_bgem3_provider",
                return_value=self.fake_provider,
            ),
            patch(
                "app.ingest.stages.content_gate.get_supabase_client",
                return_value=self.fake_client,
            ),
            patch(
                "app.ingest.stages.doc_embed.get_supabase_client",
                return_value=self.fake_client,
            ),
            patch(
                "app.ingest.stages.doc_embed.get_bgem3_provider",
                return_value=self.fake_provider,
            ),
            patch(
                "app.ingest.stages.dedup.get_supabase_client",
                return_value=self.fake_client,
            ),
            patch(
                "app.ingest.stages.tag_summarize.get_supabase_client",
                return_value=self.fake_client,
            ),
            patch(
                "app.ingest.stages.extract.get_supabase_client",
                return_value=self.fake_client,
            ),
            patch(
                "app.routers.stats.get_supabase_client",
                return_value=self.fake_client,
            ),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()


# ====================================================================
# S1 — 정상 흐름 (golden path)
# ====================================================================


class GoldenPathTest(E2EBaseTest):
    """S1 — 정상 한국어 텍스트 3 sections → chunk·filter·load·embed 통과."""

    def test_full_chain_persists_chunks_and_dense_vec(self) -> None:
        from app.ingest.stages.chunk import run_chunk_stage
        from app.ingest.stages.chunk_filter import run_chunk_filter_stage
        from app.ingest.stages.embed import run_embed_stage
        from app.ingest.stages.load import run_load_stage

        job_id, doc_id = "job-s1", "doc-s1"
        _seed_job(self.fake_client, job_id, doc_id)

        # 평균 ~500자 한국어 sections — chunk merge 가 page 분리로 차단되도록 page 다르게.
        sections = [
            (
                "이 계약은 갑과 을 사이에 체결된 공사 도급 계약으로, 공사 대금의 "
                "지급 및 합의 해지에 관한 사항을 명확히 규정한다. 본 계약의 효력은 "
                "양 당사자가 서명한 날로부터 발생하며, 별도의 합의가 없는 한 "
                "공사 완료일까지 유효하다. 분쟁이 발생할 경우 양 당사자는 협의를 "
                "통해 해결하며, 협의가 이루어지지 않을 시 관할 법원의 판결에 따른다.",
                "제1조 계약의 목적",
            ),
            (
                "공사 대금의 지급은 계약 체결 시 30%, 공사 진행 중 40%, 공사 "
                "완료 후 30% 의 비율로 지급한다. 갑은 각 단계의 공사 결과물을 "
                "확인한 후 7일 이내에 해당 회차의 대금을 지급한다. 을이 정당한 "
                "사유 없이 공사 일정을 지연시킬 경우, 갑은 지연 일수에 따라 "
                "지체상금을 청구할 수 있다.",
                "제2조 대금 지급",
            ),
            (
                "본 계약은 공사 완료 검수 후 자동으로 종료된다. 다만 양 당사자의 "
                "합의에 의해 계약을 조기 종료할 수 있으며, 이 경우 정산은 "
                "공정률에 따라 이루어진다. 계약 종료 후에도 하자 보수 책임은 "
                "1년간 유지되며, 이 기간 동안 발견된 하자는 을의 비용으로 "
                "보수한다.",
                "제3조 계약의 종료",
            ),
        ]
        extraction = _make_extraction(sections)

        # 4 stage 순차 실행.
        chunk_records = run_chunk_stage(
            job_id, doc_id=doc_id, extraction=extraction
        )
        chunk_records = run_chunk_filter_stage(
            job_id, doc_id=doc_id, chunks=chunk_records
        )
        loaded = run_load_stage(job_id, chunks=chunk_records)
        embedded = run_embed_stage(job_id, doc_id=doc_id)

        # ---------------- assertion ----------------

        chunks_table = self.fake_client._tables["chunks"]
        self.assertEqual(len(chunks_table), len(chunk_records))
        self.assertEqual(loaded, len(chunk_records))
        self.assertEqual(embedded, len(chunk_records))
        self.assertGreaterEqual(
            len(chunks_table), 3, "최소 3 청크 (sections=3)"
        )

        # 텍스트·page·section_title 보존.
        for row in chunks_table:
            self.assertIsNotNone(row.get("text"))
            self.assertIsInstance(row.get("page"), int)
            # 정상 chunk 는 filtered_reason 미마킹.
            flags = row.get("flags") or {}
            self.assertNotIn(
                "filtered_reason",
                flags,
                f"정상 chunk 가 마킹됨: idx={row.get('chunk_idx')} flags={flags}",
            )

        # section_title 이 최소 1건은 보존되어야 (chunk merge 후에도).
        titles = {row.get("section_title") for row in chunks_table}
        self.assertTrue(
            any(t and "조" in t for t in titles),
            f"section_title 보존 실패: {titles}",
        )

        # 모든 chunk 의 dense_vec 가 1024 dim.
        for row in chunks_table:
            dense = row.get("dense_vec")
            self.assertIsNotNone(
                dense,
                f"dense_vec NULL: chunk_idx={row.get('chunk_idx')}",
            )
            self.assertEqual(len(dense), 1024)

        # embed_batch 호출 횟수 = ceil(N / 16). N <= 16 이면 1회.
        n = len(chunk_records)
        expected_batches = (n + 15) // 16
        self.assertEqual(
            len(self.fake_provider.embed_batch_calls),
            expected_batches,
            f"배치 호출 횟수 불일치: got={len(self.fake_provider.embed_batch_calls)} "
            f"expected=ceil({n}/16)={expected_batches}",
        )


# ====================================================================
# S2 — chunk_filter 마킹 회귀
# ====================================================================


class ChunkFilterMarkingTest(E2EBaseTest):
    """S2 — table_noise + extreme_short + 정상 1건 → 정확히 마킹되어 적재."""

    def test_marked_chunks_persist_with_dense_vec(self) -> None:
        from app.ingest.stages.chunk import run_chunk_stage
        from app.ingest.stages.chunk_filter import run_chunk_filter_stage
        from app.ingest.stages.embed import run_embed_stage
        from app.ingest.stages.load import run_load_stage

        job_id, doc_id = "job-s2", "doc-s2"
        _seed_job(self.fake_client, job_id, doc_id)

        # 표 noise — 짧은 라인 ≥ 0.9 + digit_punct ≥ 0.7 패턴.
        # `_classify_chunk` 임계값 정합성: 라인 ≥ 6 + len ≥ 50.
        table_noise_text = "\n".join(
            [
                "1 | 100 | 200",
                "2 | 150 | 300",
                "3 | 175 | 400",
                "4 | 200 | 500",
                "5 | 225 | 600",
                "6 | 250 | 700",
            ]
        )
        # extreme_short — 한국어/영문 알파벳 0 + len < 20.
        extreme_short_text = "2,800"
        # 정상 산문 — 어떤 마커도 트리거 X. 충분히 길고 한국어 비중 높음.
        normal_text = (
            "본 계약은 양 당사자의 합의에 의해 체결되었으며, 계약의 이행에 "
            "관한 모든 사항은 별도의 부속서에 따른다. 분쟁이 발생할 경우 "
            "양 당사자는 우선 협의를 통한 해결을 시도하며, 협의가 결렬될 "
            "경우 관할 법원의 판단에 따른다. 본 계약의 효력은 서명일로부터 "
            "발생한다."
        )

        # chunk merge 차단 위해 page 분리 + section_title 부여.
        # `_looks_like_table_cell` 가 ` | ` 포함 → 인접 병합 차단.
        # extreme_short 는 `_looks_like_table_cell` 가 짧고 숫자 비중 → 격리됨.
        sections = [
            (table_noise_text, "표 1"),
            (extreme_short_text, "단가"),
            (normal_text, "본문"),
        ]
        extraction = _make_extraction(sections)

        chunk_records = run_chunk_stage(
            job_id, doc_id=doc_id, extraction=extraction
        )
        chunk_records = run_chunk_filter_stage(
            job_id, doc_id=doc_id, chunks=chunk_records
        )
        loaded = run_load_stage(job_id, chunks=chunk_records)
        embedded = run_embed_stage(job_id, doc_id=doc_id)

        # ---------------- assertion ----------------

        chunks_table = self.fake_client._tables["chunks"]
        self.assertEqual(len(chunks_table), 3, f"3개 청크 모두 적재 — got={len(chunks_table)}")
        self.assertEqual(loaded, 3)
        self.assertEqual(embedded, 3)

        # 마킹 카운트.
        reasons = [
            (r.get("flags") or {}).get("filtered_reason") for r in chunks_table
        ]
        table_noise_count = sum(1 for x in reasons if x == "table_noise")
        extreme_short_count = sum(1 for x in reasons if x == "extreme_short")
        none_count = sum(1 for x in reasons if x is None)

        self.assertEqual(
            table_noise_count, 1,
            f"table_noise 마킹 1건 기대 — got={table_noise_count}, reasons={reasons}",
        )
        self.assertEqual(
            extreme_short_count, 1,
            f"extreme_short 마킹 1건 기대 — got={extreme_short_count}, reasons={reasons}",
        )
        self.assertEqual(
            none_count, 1,
            f"정상 1건 기대 — got={none_count}, reasons={reasons}",
        )

        # 정책 핵심 — 마킹된 chunk 도 dense_vec 채워짐.
        # search_hybrid_rrf 가 WHERE flags->>'filtered_reason' IS NULL 로 자동 제외하므로,
        # 임베딩은 일관되게 진행 (재인제스트 시 마킹 변경되어도 vec 살아있음).
        for row in chunks_table:
            dense = row.get("dense_vec")
            self.assertIsNotNone(
                dense,
                f"마킹 chunk 도 dense_vec 채워야 함: "
                f"flags={row.get('flags')} chunk_idx={row.get('chunk_idx')}",
            )
            self.assertEqual(len(dense), 1024)

        # embed_batch 호출 1회 (3 < 16).
        self.assertEqual(len(self.fake_provider.embed_batch_calls), 1)


# ====================================================================
# S3 — empty input edge case
# ====================================================================


class EmptyInputTest(E2EBaseTest):
    """S3 — sections=[] → 4 stage 모두 graceful, 어떤 부수효과도 없음."""

    def test_no_chunks_no_embed_calls(self) -> None:
        from app.ingest.stages.chunk import run_chunk_stage
        from app.ingest.stages.chunk_filter import run_chunk_filter_stage
        from app.ingest.stages.embed import run_embed_stage
        from app.ingest.stages.load import run_load_stage

        job_id, doc_id = "job-s3", "doc-s3"
        _seed_job(self.fake_client, job_id, doc_id)

        extraction = _make_extraction([])

        chunk_records = run_chunk_stage(
            job_id, doc_id=doc_id, extraction=extraction
        )
        chunk_records = run_chunk_filter_stage(
            job_id, doc_id=doc_id, chunks=chunk_records
        )
        loaded = run_load_stage(job_id, chunks=chunk_records)
        embedded = run_embed_stage(job_id, doc_id=doc_id)

        # 어떤 stage 도 예외 발생 X — 호출 자체로 검증 (raise 없으면 통과).
        self.assertEqual(chunk_records, [])
        self.assertEqual(self.fake_client._tables["chunks"], [])
        self.assertEqual(loaded, 0)
        self.assertEqual(embedded, 0)

        # embed_batch 호출 0회 — 빈 input 에서 외부 호출 없음.
        self.assertEqual(len(self.fake_provider.embed_batch_calls), 0)

        # ingest_logs 에는 4개 stage 의 started→succeeded 행 기록 (1행 모델).
        # graceful 검증 — 어떤 stage 도 failed 로 종료되지 않아야 함.
        log_rows = self.fake_client._tables["ingest_logs"]
        failed = [r for r in log_rows if r.get("status") == "failed"]
        self.assertEqual(failed, [], f"빈 입력에 failed 기록됨: {failed}")


# ====================================================================
# S4 — content_gate stage (PII + 워터마크 + 정상 혼합)
# ====================================================================


class ContentGateTest(E2EBaseTest):
    """S4 — chunks 3건 (PII RRN + 워터마크 + 정상) → content_gate 검증.

    검증 포인트
    - PII chunk: metadata.pii_ranges 부착 (RRN 위치)
    - 워터마크 chunk: metadata.watermark_hits = ['대외비']
    - 정상 chunk: metadata 변경 없음 (pii_ranges/watermark_hits 키 없음)
    - documents.flags 머지: has_pii=True · has_watermark=True · third_party=False · watermark_hits=['대외비']
    - 기존 documents.flags 의 다른 키 보존 (select-then-update merge 동작)
    """

    def test_pii_and_watermark_metadata_with_flags_merge(self) -> None:
        from app.ingest.stages.content_gate import run_content_gate_stage

        job_id, doc_id = "job-s4", "doc-s4"
        _seed_job(self.fake_client, job_id, doc_id)
        # 기존 flags 의 보존 검증을 위해 임의 키 사전 주입.
        _seed_document(
            self.fake_client, doc_id, flags={"existing_marker": "keep_me"}
        )

        # PII = 주민번호 (YYMMDD-NXXXXXX, 850123 = 1985-01-23 유효).
        chunks = [
            _make_chunk(
                doc_id=doc_id,
                chunk_idx=0,
                text="신청자 홍길동 주민등록번호 850123-1234567 입니다.",
            ),
            _make_chunk(
                doc_id=doc_id,
                chunk_idx=1,
                text="본 문서는 대외비 자료로 외부 유출이 금지됩니다.",
            ),
            _make_chunk(
                doc_id=doc_id,
                chunk_idx=2,
                text="공사 대금은 분할 지급한다는 일반 조항입니다.",
            ),
        ]
        extraction = _make_extraction(
            [(c.text, None) for c in chunks]
        )

        updated_chunks, flags_update = run_content_gate_stage(
            job_id, doc_id=doc_id, chunks=chunks, extraction=extraction
        )

        # ---------------- chunk metadata ----------------

        self.assertEqual(len(updated_chunks), 3)

        # PII chunk
        pii_meta = updated_chunks[0].metadata
        self.assertIn("pii_ranges", pii_meta)
        self.assertEqual(len(pii_meta["pii_ranges"]), 1)
        start, end = pii_meta["pii_ranges"][0]
        self.assertEqual(
            chunks[0].text[start:end],
            "850123-1234567",
            "pii_ranges 가 RRN 의 정확한 substring 가리킴",
        )
        self.assertNotIn("watermark_hits", pii_meta)

        # 워터마크 chunk
        wm_meta = updated_chunks[1].metadata
        self.assertEqual(wm_meta.get("watermark_hits"), ["대외비"])
        self.assertNotIn("pii_ranges", wm_meta)

        # 정상 chunk — metadata 변경 없음
        normal_meta = updated_chunks[2].metadata
        self.assertNotIn("pii_ranges", normal_meta)
        self.assertNotIn("watermark_hits", normal_meta)

        # ---------------- doc flags ----------------

        self.assertEqual(
            flags_update,
            {
                "has_pii": True,
                "has_watermark": True,
                "third_party": False,
                "watermark_hits": ["대외비"],
            },
        )

        # documents 테이블 실 머지 결과 — 기존 키 보존 + 새 키 추가.
        doc_row = next(
            r for r in self.fake_client._tables["documents"]
            if r["id"] == doc_id
        )
        merged = doc_row["flags"]
        self.assertEqual(merged["existing_marker"], "keep_me")
        self.assertTrue(merged["has_pii"])
        self.assertTrue(merged["has_watermark"])
        self.assertFalse(merged["third_party"])
        self.assertEqual(merged["watermark_hits"], ["대외비"])


# ====================================================================
# S5 — doc_embed stage (summary + raw_text fallback)
# ====================================================================


class DocEmbedTest(E2EBaseTest):
    """S5 — `summary` 가 있으면 그것을 source 로 embed, 없으면 raw_text[:3000].

    검증 포인트
    - summary 우선: documents.summary 가 있으면 embed source 가 summary+implications join
    - raw_text fallback: summary NULL 일 때 raw_text 사용
    - 둘 다 없으면 skip (return False, doc_embedding 미갱신)
    """

    def test_summary_used_as_source(self) -> None:
        from app.ingest.stages.doc_embed import run_doc_embed_stage

        job_id, doc_id = "job-s5a", "doc-s5a"
        _seed_job(self.fake_client, job_id, doc_id)
        self.fake_client._tables["documents"].append({
            "id": doc_id,
            "summary": "요약 본문 1줄",
            "implications": "함의 1줄",
            "doc_embedding": None,
        })
        extraction = _make_extraction([("raw text 본문", None)])

        ok = run_doc_embed_stage(job_id, doc_id=doc_id, extraction=extraction)

        self.assertTrue(ok)
        # embed 호출 인자 — summary + implications join
        self.assertEqual(len(self.fake_provider.embed_calls), 1)
        source = self.fake_provider.embed_calls[0]
        self.assertIn("요약 본문 1줄", source)
        self.assertIn("함의 1줄", source)

        # doc_embedding 갱신 확인
        doc_row = self.fake_client._tables["documents"][0]
        self.assertEqual(len(doc_row["doc_embedding"]), 1024)

    def test_raw_text_fallback_when_no_summary(self) -> None:
        from app.ingest.stages.doc_embed import run_doc_embed_stage

        job_id, doc_id = "job-s5b", "doc-s5b"
        _seed_job(self.fake_client, job_id, doc_id)
        self.fake_client._tables["documents"].append({
            "id": doc_id,
            "summary": None,
            "implications": None,
            "doc_embedding": None,
        })
        extraction = _make_extraction([("raw 본문 텍스트만 존재", None)])

        ok = run_doc_embed_stage(job_id, doc_id=doc_id, extraction=extraction)

        self.assertTrue(ok)
        source = self.fake_provider.embed_calls[0]
        self.assertIn("raw 본문 텍스트만 존재", source)

    def test_skip_when_no_source(self) -> None:
        from app.ingest.stages.doc_embed import run_doc_embed_stage

        job_id, doc_id = "job-s5c", "doc-s5c"
        _seed_job(self.fake_client, job_id, doc_id)
        self.fake_client._tables["documents"].append({
            "id": doc_id,
            "summary": None,
            "implications": None,
            "doc_embedding": None,
        })
        extraction = _make_extraction([])  # raw_text 빈 문자열

        ok = run_doc_embed_stage(job_id, doc_id=doc_id, extraction=extraction)

        self.assertFalse(ok)
        self.assertEqual(len(self.fake_provider.embed_calls), 0)


# ====================================================================
# S6 — dedup stage (Tier 2 cosine ≥0.95)
# ====================================================================


class DedupTier2Test(E2EBaseTest):
    """S6 — 두 doc 의 doc_embedding 이 거의 동일 (cosine 1.0) → Tier 2 마킹.

    - my_doc 과 candidate 의 doc_embedding 모두 [1, 0, 0, ..., 0] (1024 dim)
    - cosine(a, b) = 1.0 → ≥ 0.95 → Tier 2
    - flags.duplicate_tier=2 + duplicate_of=candidate_id + duplicate_similarity=1.0
    """

    def test_tier2_match_marks_flags(self) -> None:
        from app.ingest.stages.dedup import run_dedup_stage
        from app.config import get_settings

        user_id = get_settings().default_user_id
        job_id, my_id, other_id = "job-s6", "doc-s6-me", "doc-s6-other"
        _seed_job(self.fake_client, job_id, my_id)

        unit_vec = [1.0] + [0.0] * 1023  # 1024 dim
        self.fake_client._tables["documents"].extend([
            {
                "id": my_id,
                "user_id": user_id,
                "title": "보고서",
                "storage_path": "default/report.pdf",
                "doc_embedding": unit_vec,
                "deleted_at": None,
                "flags": {},
            },
            {
                "id": other_id,
                "user_id": user_id,
                "title": "보고서",
                "storage_path": "default/report.pdf",
                "doc_embedding": unit_vec,  # 동일 벡터 → cosine 1.0
                "deleted_at": None,
                "flags": {},
            },
        ])

        match = run_dedup_stage(job_id, doc_id=my_id)

        self.assertIsNotNone(match)
        self.assertEqual(match["duplicate_tier"], 2)
        self.assertEqual(match["duplicate_of"], other_id)
        self.assertGreaterEqual(match["duplicate_similarity"], 0.95)

        # documents flags 갱신 확인
        my_row = next(
            r for r in self.fake_client._tables["documents"] if r["id"] == my_id
        )
        self.assertEqual(my_row["flags"]["duplicate_tier"], 2)
        self.assertEqual(my_row["flags"]["duplicate_of"], other_id)


# ====================================================================
# S6b — dedup Tier 3 (sim 0.85~0.95 + filename ≥0.6)
# ====================================================================


class DedupTier3Test(E2EBaseTest):
    """S6b — Tier 2 임계값 (0.95) 미달 + Tier 3 임계값 (0.85) 통과 + filename 유사 → Tier 3.

    cosine 시뮬: my_vec=[1,0,…], other_vec=[0.9, 0.4358..., 0,…] → cos≈0.9 (∈ [0.85, 0.95))
    filename 유사: "default/v1.pdf" vs "default/v2.pdf" → SequenceMatcher ratio ≈ 0.93
    """

    def test_tier3_match_marks_previous_version(self) -> None:
        from app.ingest.stages.dedup import run_dedup_stage
        from app.config import get_settings
        import math

        user_id = get_settings().default_user_id
        job_id, my_id, other_id = "job-s6b", "doc-s6b-me", "doc-s6b-other"
        _seed_job(self.fake_client, job_id, my_id)

        # cosine 0.9 — my=[1,0,...,0], other=[0.9, sqrt(1-0.81), 0,...,0]
        my_vec = [1.0] + [0.0] * 1023
        rest = math.sqrt(1.0 - 0.81)  # ≈ 0.4359
        other_vec = [0.9, rest] + [0.0] * 1022

        self.fake_client._tables["documents"].extend([
            {
                "id": my_id,
                "user_id": user_id,
                "title": "보고서 v2",
                "storage_path": "default/report_v2.pdf",
                "doc_embedding": my_vec,
                "deleted_at": None,
                "flags": {},
            },
            {
                "id": other_id,
                "user_id": user_id,
                "title": "보고서 v1",
                "storage_path": "default/report_v1.pdf",
                "doc_embedding": other_vec,
                "deleted_at": None,
                "flags": {},
            },
        ])

        match = run_dedup_stage(job_id, doc_id=my_id)

        self.assertIsNotNone(match)
        self.assertEqual(match["duplicate_tier"], 3)
        self.assertEqual(match["previous_version_of"], other_id)
        # cosine ≥ 0.85 + < 0.95
        self.assertGreaterEqual(match["duplicate_similarity"], 0.85)
        self.assertLess(match["duplicate_similarity"], 0.95)
        # filename ≥ 0.6
        self.assertGreaterEqual(match["filename_similarity"], 0.6)

        my_row = next(
            r for r in self.fake_client._tables["documents"] if r["id"] == my_id
        )
        self.assertEqual(my_row["flags"]["duplicate_tier"], 3)
        self.assertEqual(my_row["flags"]["previous_version_of"], other_id)

    def test_no_candidates_returns_none(self) -> None:
        """후보 doc 0건 (자기 자신만) → match=None, flags 변경 X (graceful)."""
        from app.ingest.stages.dedup import run_dedup_stage
        from app.config import get_settings

        user_id = get_settings().default_user_id
        job_id, my_id = "job-s6d", "doc-s6d-me"
        _seed_job(self.fake_client, job_id, my_id)

        # 자기 자신만 documents 에 — `.neq("id", exclude_id)` 로 후보 0건.
        self.fake_client._tables["documents"].append({
            "id": my_id,
            "user_id": user_id,
            "title": "유일 문서",
            "storage_path": "default/only.pdf",
            "doc_embedding": [1.0] + [0.0] * 1023,
            "deleted_at": None,
            "flags": {"existing": "preserve"},
        })

        match = run_dedup_stage(job_id, doc_id=my_id)

        self.assertIsNone(match, "후보 0건 → match None")
        my_row = self.fake_client._tables["documents"][0]
        # 기존 flags 보존 + duplicate_* 키 추가 X
        self.assertEqual(my_row["flags"], {"existing": "preserve"})

    def test_tier3_filename_too_different_no_match(self) -> None:
        """sim 0.9 (Tier 3 범위) 이지만 filename 매우 다름 (< 0.6) → 매칭 X."""
        from app.ingest.stages.dedup import run_dedup_stage
        from app.config import get_settings
        import math

        user_id = get_settings().default_user_id
        job_id, my_id, other_id = "job-s6c", "doc-s6c-me", "doc-s6c-other"
        _seed_job(self.fake_client, job_id, my_id)

        my_vec = [1.0] + [0.0] * 1023
        rest = math.sqrt(1.0 - 0.81)
        other_vec = [0.9, rest] + [0.0] * 1022

        self.fake_client._tables["documents"].extend([
            {
                "id": my_id,
                "user_id": user_id,
                "title": "한국 경제 리포트",
                "storage_path": "default/economy_kr.pdf",
                "doc_embedding": my_vec,
                "deleted_at": None,
                "flags": {},
            },
            {
                "id": other_id,
                "user_id": user_id,
                "title": "전혀 다른 자료",
                "storage_path": "default/zzzzzzzz.docx",
                "doc_embedding": other_vec,
                "deleted_at": None,
                "flags": {},
            },
        ])

        match = run_dedup_stage(job_id, doc_id=my_id)
        self.assertIsNone(match, "filename 유사도 미달이면 Tier 3 매칭 X")

        my_row = next(
            r for r in self.fake_client._tables["documents"] if r["id"] == my_id
        )
        self.assertNotIn("duplicate_tier", my_row["flags"])


# ====================================================================
# S7 — tag_summarize stage (LLM mock, graceful fail)
# ====================================================================


class TagSummarizeGracefulTest(E2EBaseTest):
    """S7 — LLM 호출 실패해도 graceful (documents 의 tags/summary NULL 유지)."""

    def test_llm_failure_is_swallowed(self) -> None:
        from app.ingest.stages import tag_summarize

        job_id, doc_id = "job-s7a", "doc-s7a"
        _seed_job(self.fake_client, job_id, doc_id)
        self.fake_client._tables["documents"].append({
            "id": doc_id,
            "tags": [],
            "summary": None,
            "implications": None,
            "flags": {},
        })
        extraction = _make_extraction([("본문 텍스트입니다.", None)])

        # _llm.complete 가 항상 raise — graceful 검증
        with patch.object(
            tag_summarize._llm,
            "complete",
            side_effect=RuntimeError("LLM API down"),
        ):
            tag_summarize.run_tag_summarize_stage(
                job_id, doc_id=doc_id, extraction=extraction
            )

        # raise 없이 통과 (graceful). DB 의 tags/summary 는 그대로 빈 값.
        doc_row = self.fake_client._tables["documents"][0]
        self.assertEqual(doc_row.get("tags"), [])
        self.assertIsNone(doc_row.get("summary"))

    def test_quota_exhausted_skips_summary_call(self) -> None:
        """W9 Day 6 한계 #53 — _call_tags 가 RESOURCE_EXHAUSTED 시 _call_summary skip.

        Day 4 PptxParser fast-fail 패턴을 LLM stage 에 적용. 두 번째 호출 절약 +
        Vision 호출과 quota 공유라 부수적 안정성↑.
        """
        from app.ingest.stages import tag_summarize

        job_id, doc_id = "job-s7c", "doc-s7c"
        _seed_job(self.fake_client, job_id, doc_id)
        self.fake_client._tables["documents"].append({
            "id": doc_id, "tags": [], "summary": None,
            "implications": None, "flags": {},
        })
        extraction = _make_extraction([("본문", None)])

        # _llm.complete 호출 카운터 — 첫 호출 raise (RESOURCE_EXHAUSTED) 후 두 번째 호출 안 됨
        call_count = [0]

        def _quota_raise(*args, **kwargs):
            call_count[0] += 1
            raise RuntimeError(
                "429 RESOURCE_EXHAUSTED. You exceeded your current quota"
            )

        with patch.object(tag_summarize._llm, "complete", side_effect=_quota_raise):
            tag_summarize.run_tag_summarize_stage(
                job_id, doc_id=doc_id, extraction=extraction
            )

        # 첫 호출 (_call_tags) 만 실행 — _call_summary 는 skip
        self.assertEqual(
            call_count[0], 1,
            f"quota 감지 후 summary 호출 skip — got {call_count[0]} (이전 정책: 2)",
        )
        # graceful — DB 의 tags/summary 는 그대로
        doc_row = self.fake_client._tables["documents"][0]
        self.assertEqual(doc_row.get("tags"), [])
        self.assertIsNone(doc_row.get("summary"))

    def test_non_quota_failure_still_attempts_summary(self) -> None:
        """quota 가 아닌 일반 fail (예: 5xx) 은 두 번째 호출 그대로 시도."""
        from app.ingest.stages import tag_summarize

        job_id, doc_id = "job-s7d", "doc-s7d"
        _seed_job(self.fake_client, job_id, doc_id)
        self.fake_client._tables["documents"].append({
            "id": doc_id, "tags": [], "summary": None,
            "implications": None, "flags": {},
        })
        extraction = _make_extraction([("본문", None)])

        call_count = [0]

        def _generic_raise(*args, **kwargs):
            call_count[0] += 1
            raise RuntimeError("Service temporarily unavailable")

        with patch.object(tag_summarize._llm, "complete", side_effect=_generic_raise):
            tag_summarize.run_tag_summarize_stage(
                job_id, doc_id=doc_id, extraction=extraction
            )

        # 두 호출 모두 시도 (graceful 정책 유지)
        self.assertEqual(call_count[0], 2)

    def test_llm_success_persists_tags_and_summary(self) -> None:
        from app.ingest.stages import tag_summarize
        import json

        job_id, doc_id = "job-s7b", "doc-s7b"
        _seed_job(self.fake_client, job_id, doc_id)
        self.fake_client._tables["documents"].append({
            "id": doc_id,
            "tags": [],
            "summary": None,
            "implications": None,
            "flags": {},
        })
        extraction = _make_extraction([("계약·합의 본문", None)])

        # _call_tags 와 _call_summary 가 _llm.complete 두 번 호출 — 응답 시퀀스 부여.
        responses = [
            json.dumps({
                "topic_tags": ["계약", "합의"],
                "entity_tags": ["갑", "을"],
                "document_type": "보고서",
                "time_reference": "2025-01",
            }),
            json.dumps({
                "summary_3line": "1줄 요약\n2줄 요약\n3줄 요약",
                "implications": "본 문서의 함의",
            }),
        ]
        with patch.object(
            tag_summarize._llm,
            "complete",
            side_effect=responses,
        ):
            tag_summarize.run_tag_summarize_stage(
                job_id, doc_id=doc_id, extraction=extraction
            )

        doc_row = self.fake_client._tables["documents"][0]
        # tags = topic_tags + entity_tags 합집합 (순서 유지)
        self.assertEqual(doc_row["tags"], ["계약", "합의", "갑", "을"])
        self.assertEqual(doc_row["summary"], "1줄 요약\n2줄 요약\n3줄 요약")
        self.assertEqual(doc_row["implications"], "본 문서의 함의")
        # flags 에 document_type / time_reference 머지
        self.assertEqual(doc_row["flags"]["document_type"], "보고서")
        self.assertEqual(doc_row["flags"]["time_reference"], "2025-01")


# ====================================================================
# S8 — extract stage (지원/비지원 포맷 dispatch + storage.get mock)
# ====================================================================


class ExtractDocxTest(E2EBaseTest):
    """S8a — 지원 포맷 (DOCX) → DocxParser dispatch + ExtractionResult 반환.

    검증 포인트
    - documents.doc_type='docx' seed → _PARSERS_BY_DOC_TYPE 에서 DocxParser 선택
    - SupabaseBlobStorage.get mock → 합성 DOCX bytes 반환
    - parser.parse() 정상 실행 → sections 1+
    - flags.extract_skipped 미마킹
    """

    def test_docx_dispatch_returns_extraction(self) -> None:
        import io as _io
        import docx as _docx
        from app.ingest.stages import extract as extract_mod

        job_id, doc_id = "job-s8a", "doc-s8a"
        _seed_job(self.fake_client, job_id, doc_id)
        self.fake_client._tables["documents"].append({
            "id": doc_id,
            "doc_type": "docx",
            "storage_path": "default/test.docx",
            "flags": {},
        })

        # 합성 DOCX bytes
        docx_doc = _docx.Document()
        docx_doc.add_paragraph("e2e 검증 본문 텍스트입니다.")
        buf = _io.BytesIO()
        docx_doc.save(buf)
        docx_bytes = buf.getvalue()

        # SupabaseBlobStorage.get mock — 합성 bytes 반환
        class _FakeStorage:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, path: str) -> bytes:
                return docx_bytes

        with patch.object(extract_mod, "SupabaseBlobStorage", _FakeStorage):
            result = extract_mod.run_extract_stage(job_id, doc_id)

        self.assertIsNotNone(result, "DOCX dispatch 시 ExtractionResult 반환")
        self.assertEqual(result.source_type, "docx")
        self.assertGreaterEqual(len(result.sections), 1)
        self.assertIn("e2e 검증 본문", result.raw_text)

        # documents flags 변경 없음 — 지원 포맷이라 extract_skipped 미마킹
        doc_row = self.fake_client._tables["documents"][0]
        self.assertNotIn("extract_skipped", doc_row.get("flags", {}))


class ExtractUnsupportedTest(E2EBaseTest):
    """S8b — 비지원 포맷 (xlsx 등) → graceful skip + flags.extract_skipped=True."""

    def test_unsupported_format_marks_skipped(self) -> None:
        from app.ingest.stages.extract import run_extract_stage

        job_id, doc_id = "job-s8b", "doc-s8b"
        _seed_job(self.fake_client, job_id, doc_id)
        self.fake_client._tables["documents"].append({
            "id": doc_id,
            "doc_type": "xlsx",  # _PARSERS_BY_DOC_TYPE 미등록
            "storage_path": "default/sheet.xlsx",
            "flags": {},
        })

        result = run_extract_stage(job_id, doc_id)

        # graceful — 예외 없이 None 반환
        self.assertIsNone(result, "비지원 포맷은 None 반환")

        # flags.extract_skipped 마킹 + reason 명시
        doc_row = self.fake_client._tables["documents"][0]
        self.assertTrue(doc_row["flags"].get("extract_skipped"))
        self.assertIn("xlsx", doc_row["flags"].get("extract_skipped_reason", ""))


# ====================================================================
# S9 — stats router e2e (W10 Day 2 — F1 count 자산 활용)
# ====================================================================


class StatsRouterE2ETest(E2EBaseTest):
    """S9 — `GET /stats` 라우터 함수 직접 호출 → 집계 정확도 검증.

    검증 포인트
    - documents 분포 (by_doc_type / by_source_channel / total_size_bytes)
    - chunks 분포 (.count 자산 활용 — 한계 #20 회수 검증)
    - failed 문서 분리 (flags.failed=True 는 별도 집계)
    - search_slo / vision_usage in-memory counter 정상 직렬화
    """

    def setUp(self) -> None:
        super().setUp()
        # in-memory metrics reset — 이전 테스트 영향 차단
        from app.services import search_metrics, vision_metrics
        # search_metrics 는 직접 리셋 API 가 없어 deque clear (구현 디테일).
        search_metrics._ring.clear()  # noqa: SLF001 — 테스트 시점 격리
        vision_metrics.reset()

    def test_stats_aggregates_documents_chunks_jobs(self) -> None:
        from app.config import get_settings
        from app.routers.stats import stats as stats_endpoint

        user_id = get_settings().default_user_id

        # documents seed — 다양한 doc_type + 1 failed
        self.fake_client._tables["documents"].extend([
            {
                "id": "d1", "user_id": user_id, "doc_type": "pdf",
                "source_channel": "drag-drop", "size_bytes": 1024,
                "flags": {}, "tags": ["계약"], "created_at": None,
                "received_ms": 800, "deleted_at": None,
            },
            {
                "id": "d2", "user_id": user_id, "doc_type": "docx",
                "source_channel": "drag-drop", "size_bytes": 2048,
                "flags": {}, "tags": ["보고서"], "created_at": None,
                "received_ms": 600, "deleted_at": None,
            },
            {
                "id": "d-fail", "user_id": user_id, "doc_type": "pdf",
                "source_channel": "url", "size_bytes": 4096,
                "flags": {"failed": True}, "tags": [], "created_at": None,
                "received_ms": None, "deleted_at": None,
            },
        ])

        # chunks seed — 5건 (3 effective + 2 filtered)
        for i in range(3):
            self.fake_client._tables["chunks"].append({
                "id": f"c{i}", "doc_id": "d1", "chunk_idx": i,
                "text": f"chunk {i}", "flags": {},
            })
        self.fake_client._tables["chunks"].append({
            "id": "c-tn", "doc_id": "d1", "chunk_idx": 3,
            "text": "| col |", "flags": {"filtered_reason": "table_noise"},
        })
        self.fake_client._tables["chunks"].append({
            "id": "c-es", "doc_id": "d1", "chunk_idx": 4,
            "text": "x", "flags": {"filtered_reason": "extreme_short"},
        })

        # ingest_jobs seed
        self.fake_client._tables["ingest_jobs"].extend([
            {"id": "j1", "doc_id": "d1", "status": "completed"},
            {"id": "j2", "doc_id": "d2", "status": "completed"},
            {"id": "j3", "doc_id": "d-fail", "status": "failed",
             "current_stage": "extract", "error_msg": "x", "queued_at": None},
        ])

        resp = stats_endpoint()

        # ---------------- documents ----------------
        self.assertEqual(resp.documents.total, 2, "failed 1건 분리")
        self.assertEqual(resp.documents.failed_count, 1)
        self.assertEqual(resp.documents.by_doc_type, {"pdf": 1, "docx": 1})
        self.assertEqual(resp.documents.total_size_bytes, 1024 + 2048)

        # ---------------- chunks (count 자산 활용) ----------------
        self.assertEqual(resp.chunks.total, 5)
        self.assertEqual(resp.chunks.effective, 3)
        self.assertEqual(
            resp.chunks.filtered_breakdown,
            {"table_noise": 1, "extreme_short": 1},
        )
        self.assertAlmostEqual(resp.chunks.filtered_ratio, 0.4, places=3)
        self.assertEqual(resp.chunks_total, 5, "backward compatible 필드")

        # ---------------- jobs ----------------
        self.assertEqual(resp.jobs.total, 3)
        self.assertEqual(
            resp.jobs.by_status,
            {"completed": 2, "failed": 1},
        )

        # ---------------- in-memory metrics ----------------
        self.assertEqual(resp.search_slo.sample_count, 0)
        self.assertEqual(resp.vision_usage.total_calls, 0)


if __name__ == "__main__":
    unittest.main()
