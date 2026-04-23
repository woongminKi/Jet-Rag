"""Embed 스테이지 — 기획서 §10.2 [9].

load 스테이지가 chunks 를 `dense_vec=NULL` 상태로 저장한 이후,
BGE-M3 HF 어댑터로 각 청크 텍스트를 임베딩해 같은 row 를 UPDATE 로 채운다.

배치 단위 전송 → HF API 호출 수 최소화. Supabase upsert(on_conflict=id) 로 UPDATE.

실패 정책 (§10.10)
- 3회 retry 는 어댑터 내부에서 처리. 최종 실패 시 예외 전파 → pipeline.fail_job.
  chunks 는 dense_vec NULL 상태로 남고, keyword 검색은 동작. 재처리는 W2 에 추가할
  `POST /documents/{id}/reingest` 엔드포인트 예정.
"""

from __future__ import annotations

import logging

from app.adapters.impl.bgem3_hf_embedding import BGEM3HFEmbeddingProvider
from app.db import get_supabase_client
from app.ingest.jobs import stage

logger = logging.getLogger(__name__)

_STAGE = "embed"
_BATCH_SIZE = 16


def run_embed_stage(job_id: str, *, doc_id: str) -> int:
    """doc_id 의 dense_vec NULL 청크를 batch 로 임베딩. 반환: 처리 건수."""
    with stage(job_id, _STAGE):
        client = get_supabase_client()
        resp = (
            client.table("chunks")
            .select("id, text")
            .eq("doc_id", doc_id)
            .is_("dense_vec", "null")
            .order("chunk_idx")
            .execute()
        )
        rows = resp.data or []
        if not rows:
            logger.info("embed: doc=%s 임베딩 필요한 청크 없음", doc_id)
            return 0

        provider = BGEM3HFEmbeddingProvider()
        total = 0
        for i in range(0, len(rows), _BATCH_SIZE):
            batch = rows[i : i + _BATCH_SIZE]
            texts = [row["text"] for row in batch]
            embeddings = provider.embed_batch(texts)

            payload = [
                {"id": row["id"], "dense_vec": emb.dense}
                for row, emb in zip(batch, embeddings)
            ]
            client.table("chunks").upsert(payload, on_conflict="id").execute()
            total += len(batch)
            logger.info(
                "embed: doc=%s 진행 %d/%d", doc_id, total, len(rows)
            )

        return total
