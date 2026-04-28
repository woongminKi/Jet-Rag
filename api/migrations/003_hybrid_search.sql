-- ============================================================
-- 003_hybrid_search.sql — W3 자연어 검색 인프라
-- ============================================================
-- 명세: work-log/2026-04-28 W3 스프린트 명세.md (v0.4 CONFIRMED, Option Y)
-- 채택 결정:
--   DE-56: pgvector 인덱스 IVFFlat → HNSW (incremental update + 점진 업로드)
--   DE-57: chunks.fts STORED tsvector('simple') (의존성 0, dense 가 한국어 보완)
--   DE-58: search_hybrid_rrf RPC (k=60, dense=sparse=1.0)
--   추가: pg_trgm extension (Tier 3 dedup 의 파일명 유사도)
--
-- 적용 절차: Supabase Studio SQL Editor 에 본 파일 내용 붙여넣고 Run.
--   (Claude Code MCP 는 --read-only 라 DDL 차단 — Studio 직접)
--
-- HNSW 빌드 시간: chunks 1k 기준 ~50초 추정. 운영 중 1회만 발생.
-- ============================================================

-- ------------------------------------------------------------
-- 1) 기존 IVFFlat 인덱스 → HNSW 교체 (DE-56)
-- ------------------------------------------------------------
DROP INDEX IF EXISTS idx_chunks_dense;
CREATE INDEX idx_chunks_dense ON chunks USING hnsw
    (dense_vec vector_cosine_ops) WITH (m = 16, ef_construction = 64);

DROP INDEX IF EXISTS idx_documents_embed;
CREATE INDEX idx_documents_embed ON documents USING hnsw
    (doc_embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- ------------------------------------------------------------
-- 2) chunks.fts (DE-57) — STORED generated tsvector
-- ------------------------------------------------------------
-- 'simple' config: 공백 분리 + lowercase. 한국어 형태소 분석 X.
-- 조사·어미 매칭 한계는 dense (BGE-M3 한국어 학습) 가 RRF 단계에서 보완.
-- W5 Recall@10 < 0.85 측정 시 (c) PGroonga 사용자 사전 승인 후 전환 (W4-Q-2).
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS fts tsvector
        GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED;

CREATE INDEX IF NOT EXISTS idx_chunks_fts
    ON chunks USING GIN (fts);

-- ------------------------------------------------------------
-- 3) pg_trgm extension — Tier 3 dedup 의 파일명 trigram 유사도
-- ------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_documents_title_trgm
    ON documents USING GIN (title gin_trgm_ops);

-- ------------------------------------------------------------
-- 4) search_hybrid_rrf RPC (DE-58) — Reciprocal Rank Fusion
-- ------------------------------------------------------------
-- k=60: 학계 robust default (Cormack 2009, Elastic·Pinecone·Weaviate 일관)
-- dense=sparse=1.0: Day 5 ablation 후 +5pp 미달 시 (b) dense 우세 가중치 검토
--
-- 입력
--   query_text  : 사용자 자연어 쿼리 (FTS 입력)
--   query_dense : BgeM3HfEmbedding.embed_query() 결과 (1024 dim)
--   k_rrf       : RRF 상수 (default 60)
--   top_k       : 각 검색 path 의 상위 K (default 50)
--   user_id_arg : 멀티유저 시 사용 (W5+)
--
-- 출력
--   chunk_id    : chunks.id
--   doc_id      : chunks.doc_id
--   rrf_score   : 1/(k+rank_dense) + 1/(k+rank_sparse) 합산
--   dense_rank  : dense 검색 시 순위 (없으면 NULL)
--   sparse_rank : FTS 검색 시 순위 (없으면 NULL)
CREATE OR REPLACE FUNCTION search_hybrid_rrf(
    query_text   TEXT,
    query_dense  vector(1024),
    k_rrf        INT  DEFAULT 60,
    top_k        INT  DEFAULT 50,
    user_id_arg  UUID DEFAULT NULL
) RETURNS TABLE (
    chunk_id    UUID,
    doc_id      UUID,
    rrf_score   FLOAT,
    dense_rank  INT,
    sparse_rank INT
)
LANGUAGE SQL STABLE
AS $$
    WITH dense_hits AS (
        SELECT c.id AS chunk_id, c.doc_id,
               ROW_NUMBER() OVER (ORDER BY c.dense_vec <=> query_dense) AS rank
          FROM chunks c
          JOIN documents d ON d.id = c.doc_id
         WHERE c.dense_vec IS NOT NULL
           AND d.deleted_at IS NULL
           AND (user_id_arg IS NULL OR d.user_id = user_id_arg)
         ORDER BY c.dense_vec <=> query_dense
         LIMIT top_k
    ),
    sparse_hits AS (
        SELECT c.id AS chunk_id, c.doc_id,
               ROW_NUMBER() OVER (
                   ORDER BY ts_rank(c.fts, plainto_tsquery('simple', query_text)) DESC
               ) AS rank
          FROM chunks c
          JOIN documents d ON d.id = c.doc_id
         WHERE c.fts @@ plainto_tsquery('simple', query_text)
           AND d.deleted_at IS NULL
           AND (user_id_arg IS NULL OR d.user_id = user_id_arg)
         ORDER BY ts_rank(c.fts, plainto_tsquery('simple', query_text)) DESC
         LIMIT top_k
    ),
    fused AS (
        SELECT chunk_id, doc_id,
               1.0 / (k_rrf + rank)::FLOAT AS score,
               rank::INT AS dense_rank,
               NULL::INT AS sparse_rank
          FROM dense_hits
        UNION ALL
        SELECT chunk_id, doc_id,
               1.0 / (k_rrf + rank)::FLOAT AS score,
               NULL::INT AS dense_rank,
               rank::INT AS sparse_rank
          FROM sparse_hits
    )
    SELECT chunk_id,
           doc_id,
           SUM(score)::FLOAT AS rrf_score,
           MIN(dense_rank)   AS dense_rank,
           MIN(sparse_rank)  AS sparse_rank
      FROM fused
     GROUP BY chunk_id, doc_id
     ORDER BY rrf_score DESC
     LIMIT top_k;
$$;

-- ------------------------------------------------------------
-- 5) (참고) ALTER TABLE 시점에 fts 컬럼은 STORED 라 자동 백필됨.
--    별도 백필 스크립트 불요.
--    HNSW 인덱스 빌드는 본 마이그레이션 실행 시 1회 ~50초.
-- ------------------------------------------------------------
