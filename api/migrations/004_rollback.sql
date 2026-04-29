-- ============================================================
-- 004_rollback.sql — 마이그레이션 004 (PGroonga 한국어 FTS) 안전망 rollback
-- ============================================================
-- 명세: work-log/2026-04-29 W3 스프린트 명세 v0.5.md (CONFIRMED) §3.A-5
-- 트리거 조건 (사용자 결정 필요):
--   1) PGroonga 인덱스 빌드 실패 또는 운영 중 PGroonga 확장 호환성 이슈 발생
--   2) sparse RPC (`search_hybrid_rrf`, `search_sparse_only_pgroonga`) 가 비정상 결과
--   3) Supabase 호스팅 환경에서 pgroonga 미지원/제거 통보
--
-- 동작:
--   - PGroonga 인덱스/RPC 제거 → 003 의 simple FTS (chunks.fts STORED + GIN) 복구
--   - search_hybrid_rrf RPC 본문은 003 SQL 그대로 (self-contained — 적용 시 003 파일
--     참조 불필요. 트레이드오프: 본 파일 ~70 라인 증가 vs 적용 안전성 ↑)
--   - chunks.flags 컬럼은 **유지** — chunk_filter (G(3)) 와 documents.flags 정합. 003 시점에
--     없던 컬럼이지만 검색 RPC 의 `flags->>'filtered_reason' IS NULL` 필터는 003 에 추가
--     포팅함 (아래 본문). 즉 rollback 후에도 자동 필터링 룰은 작동.
--
-- 적용 절차 (필요 시): Supabase Studio SQL Editor 에 본 파일 내용 붙여넣고 Run.
--
-- 적용 후 검증 (메인 스레드 책임):
--   1. SELECT count(*) FROM chunks WHERE fts IS NOT NULL;  -- STORED 컬럼 백필 확인
--   2. EXPLAIN SELECT * FROM search_hybrid_rrf('테스트', '[0,..,0]'::vector(1024), 60, 50, NULL);
--      -- plan 에 idx_chunks_fts (GIN) 사용 확인
--   3. SELECT * FROM pg_extension WHERE extname='pgroonga';  -- 0 row (제거됨)
--
-- ⚠️ pg_trgm 확장과 idx_documents_title_trgm 은 dedup Tier 3 에서 사용 → 유지.
-- ⚠️ idx_chunks_dense / idx_documents_embed (HNSW) 는 003 그대로라 변경 없음.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1) 004 의 PGroonga 인덱스/RPC 제거
-- ------------------------------------------------------------
DROP INDEX IF EXISTS idx_chunks_text_pgroonga;
DROP FUNCTION IF EXISTS search_sparse_only_pgroonga(TEXT, UUID, INT);
-- search_hybrid_rrf 는 아래에서 003 본문으로 CREATE OR REPLACE — 별도 DROP 불요.

-- ------------------------------------------------------------
-- 2) PGroonga 확장 제거 (다른 곳에서 미사용 확인 후)
--    CASCADE 미사용 — 다른 의존 객체가 있으면 에러로 알려서 사용자가 판단.
-- ------------------------------------------------------------
DROP EXTENSION IF EXISTS pgroonga;

-- ------------------------------------------------------------
-- 3) chunks.fts 컬럼 + GIN 인덱스 복구 (003 동등)
--    STORED generated 라 ALTER 시점에 자동 백필됨.
-- ------------------------------------------------------------
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS fts tsvector
        GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED;

CREATE INDEX IF NOT EXISTS idx_chunks_fts
    ON chunks USING GIN (fts);

-- ------------------------------------------------------------
-- 4) search_hybrid_rrf RPC — 003 본문 그대로 (self-contained)
--    + chunks.flags->>'filtered_reason' IS NULL 필터 추가 (G(3) 정합)
--    시그니처는 003/004 동일 — 라우터 호출부 변경 0.
-- ------------------------------------------------------------
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
           AND (c.flags->>'filtered_reason') IS NULL
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
           AND (c.flags->>'filtered_reason') IS NULL
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
-- 5) search_sparse_only_pgroonga 대체 — _sparse_only_fallback 라우터가 호출.
--    rollback 후에는 라우터 코드도 003 시절 PostgREST 직접 filter 경로로 되돌려야 하지만,
--    그 변경은 별도 git commit (W3 v0.5 §3.A-5 의 trigger 시점에 함께 진행) 으로
--    명시 — 본 SQL 은 DB 측만 책임.
--
--    임시 호환 RPC: 같은 시그니처로 simple FTS 사용. 라우터 변경 없이 즉시 작동.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION search_sparse_only_pgroonga(
    query_text   TEXT,
    user_id_arg  UUID DEFAULT NULL,
    top_k        INT  DEFAULT 50
) RETURNS TABLE (
    chunk_id     UUID,
    doc_id       UUID,
    sparse_rank  INT
)
LANGUAGE SQL STABLE
AS $$
    SELECT c.id AS chunk_id,
           c.doc_id,
           ROW_NUMBER() OVER (
               ORDER BY ts_rank(c.fts, plainto_tsquery('simple', query_text)) DESC
           )::INT AS sparse_rank
      FROM chunks c
      JOIN documents d ON d.id = c.doc_id
     WHERE c.fts @@ plainto_tsquery('simple', query_text)
       AND d.deleted_at IS NULL
       AND (user_id_arg IS NULL OR d.user_id = user_id_arg)
       AND (c.flags->>'filtered_reason') IS NULL
     ORDER BY ts_rank(c.fts, plainto_tsquery('simple', query_text)) DESC
     LIMIT top_k;
$$;

COMMIT;

-- ------------------------------------------------------------
-- (참고) rollback 후 sparse 검색 품질 — 003 의 한계 그대로.
--   "공사대금 합의해지" 같은 합성어/조사 결합 한국어 쿼리는 sparse_hits=0 가능.
--   이 경우 dense 가 hybrid RRF 에서 보완. 회복적 운용 (PGroonga 재도입) 까지 임시 운영용.
-- ------------------------------------------------------------
