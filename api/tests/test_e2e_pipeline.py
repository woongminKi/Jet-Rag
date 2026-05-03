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
    """supabase-py 의 `APIResponse` 흉내 — `.data` 속성만 노출."""

    __slots__ = ("data",)

    def __init__(self, data: list[dict[str, Any]]) -> None:
        self.data = data


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

    # ---------------------- chain 빌더 ----------------------

    def select(self, cols: str = "*") -> "_FakeTableQuery":
        self._op = "select"
        self._select_cols = cols
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
        if self._limit is not None:
            out = out[: self._limit]
        return _FakeQueryResponse(out)

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
            if op == "eq":
                if row.get(col) != value:
                    return False
            elif op == "is":
                # supabase-py 는 `.is_("col", "null")` 또는 `.is_("col", None)` 둘 다 사용.
                if value in ("null", None):
                    if row.get(col) is not None:
                        return False
                else:
                    if row.get(col) != value:
                        return False
            elif op == "in":
                if row.get(col) not in value:
                    return False
        return True


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

    def embed_batch(self, texts: list[str]) -> list:
        from app.adapters.embedding import EmbeddingResult

        self.embed_batch_calls.append(list(texts))
        out: list[EmbeddingResult] = []
        for i, _ in enumerate(texts):
            # 결정성: 인덱스 기반 seed. (실 hash 비교가 아닌 차원·존재 검증용.)
            seed = 0.001 * (i + 1)
            out.append(EmbeddingResult(dense=[seed] * 1024, sparse={}))
        return out


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


if __name__ == "__main__":
    unittest.main()
