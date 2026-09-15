"""Embed 스테이지 — 기획서 §10.2 [9].

load 스테이지가 chunks 를 `dense_vec=NULL` 상태로 저장한 이후,
BGE-M3 HF 어댑터로 각 청크 텍스트를 임베딩해 **id 기준 단건 UPDATE** 로 채운다.

`flags->>filtered_reason` 이 붙은 청크는 **건너뛴다** (2026-09-15).
검색 RPC (`search_dense_only` / `search_hybrid_rrf`) 가
`WHERE (c.flags->>'filtered_reason') IS NULL` 로 이미 제외하므로 그 dense_vec 은
아무도 읽지 않는다. 채우면 DeepInfra 호출 비용과 HNSW 인덱스 크기(실측 237MB)만 는다.
마킹은 pipeline 상 `chunk_filter` → `load` → `embed` 순서라 여기서는 이미 확정 상태다.

HF 호출은 배치 (BATCH_SIZE=16) 로 묶어 API 호출 수를 최소화한다.
DB 적재는 **`chunks_set_dense_vec` RPC 로 32행씩**(마이그 037, 2026-09-16) — 예전엔 row 별 update 였는데
커밋마다 WAL fsync 가 나서 Micro 의 I/O 예산을 태웠다(사고 4). upsert 가 아니라 UPDATE 라
"보내지 않은 컬럼이 NULL 이 되는" 회귀도 없다. 반환값(갱신 행수)이 보낸 개수와 다르면 raise.

실패 정책 (§10.10)
- 3회 retry 는 어댑터 내부에서 처리. 최종 실패 시 예외 전파 → pipeline.fail_job.
  chunks 는 dense_vec NULL 상태로 남고 keyword 검색은 동작.
  `POST /documents/{id}/reingest` 로 재처리 가능.
"""

from __future__ import annotations

import logging
from typing import Any

from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider
from app.db import get_supabase_client
from app.ingest.jobs import stage

logger = logging.getLogger(__name__)

_STAGE = "embed"
_BATCH_SIZE = 16
# `chunks_set_dense_vec` 한 번에 보내는 행수. Edge(`EMBED_WRITE_SLICE`)와 같은 값. 이 경로는 문서의
# NULL 청크를 전부 가져오므로(25k 도 가능) 무제한으로 보내면 RPC 본문이 수백 MB 가 된다.
_WRITE_SLICE = 32


def _round_for_halfvec(vec: list[float]) -> list[float]:
    """halfvec(1024) 는 fp16 이라 유효숫자 ~3.3자리 — 소수 6자리 이상은 DB 캐스팅에서 버려진다.
    float64 를 그대로 JSON 으로 보내면 값당 ~19자. 저장값 변화 없이 본문을 ~60% 줄인다."""
    return [round(x, 6) for x in vec]


def run_embed_stage(job_id: str, *, doc_id: str) -> int:
    """doc_id 의 **미필터** dense_vec NULL 청크를 batch 로 임베딩. 반환: 처리 건수."""
    with stage(job_id, _STAGE):
        client = get_supabase_client()
        resp = (
            client.table("chunks")
            .select("id, text")
            .eq("doc_id", doc_id)
            .is_("dense_vec", "null")
            # 필터 마킹된 청크는 검색이 절대 보지 않는다 — 임베딩하면
            # DeepInfra 비용과 HNSW 인덱스 크기만 늘어난다. 조회에서 아예 뺀다.
            .is_("flags->>filtered_reason", "null")
            .order("chunk_idx")
            .execute()
        )
        rows = resp.data or []
        if not rows:
            logger.info("embed: doc=%s 임베딩 필요한 청크 없음", doc_id)
            return 0

        provider = get_bgem3_provider()
        payload: list[dict[str, Any]] = []
        for i in range(0, len(rows), _BATCH_SIZE):
            batch = rows[i : i + _BATCH_SIZE]
            texts = [row["text"] for row in batch]
            embeddings = provider.embed_batch(texts)
            payload.extend(
                {"id": row["id"], "vec": _round_for_halfvec(emb.dense)}
                for row, emb in zip(batch, embeddings)
            )
            logger.info("embed: doc=%s 임베딩 %d/%d", doc_id, len(payload), len(rows))

        for i in range(0, len(payload), _WRITE_SLICE):
            chunk = payload[i : i + _WRITE_SLICE]
            written = client.rpc("chunks_set_dense_vec", {"p_rows": chunk}).execute().data
            if not isinstance(written, int) or written != len(chunk):
                raise RuntimeError(
                    f"dense_vec 저장 개수 불일치: wrote={written!r}, expect={len(chunk)} (doc={doc_id})"
                )
        return len(payload)
