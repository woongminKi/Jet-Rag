-- ============================================================
-- 008_search_mode_split_rpc.sql — W20 Day 1 (한계 #74)
-- ============================================================
-- 배경
--   W13 Day 2 ablation mode 도입 시 응용 layer 필터링 (search.py 가 RPC 결과 후
--   dense_rank / sparse_rank 검사) 사용. 진정 ablation 위해서는 RPC 호출 자체가
--   분리되어야 측정 정확 (현재는 RPC 호출 비용 동일 — 응용 필터만 다름).
--
-- 설계
--   - search_dense_only(query_dense, k_rrf, top_k, user_id_arg)
--     → search_hybrid_rrf 의 dense_hits CTE 와 동일 + sparse_rank=NULL
--   - search_sparse_only(query_text, k_rrf, top_k, user_id_arg)
--     → 004 search_sparse_only_pgroonga 와 본질 동일 + dense_rank/rrf_score 컬럼 추가
--       (기존 함수와 schema 일치, search.py 가 동일 path 처리)
--   - 반환 schema: (chunk_id, doc_id, rrf_score, dense_rank, sparse_rank)
--     · search_hybrid_rrf 와 100% 동일 → search.py 가 mode 별 RPC 만 분기, 후속 처리 동일
--     · rrf_score = 1.0 / (k_rrf + rank) (sparse 는 dense_rank=NULL, dense 는 sparse_rank=NULL)
--
-- backward compat
--   search_hybrid_rrf 그대로 유지 — mode=hybrid 호출자 영향 0.
--   004 의 search_sparse_only_pgroonga 도 그대로 (legacy 호출자 보호).
--   본 008 의 search_sparse_only 는 schema 일관성 위해 신규 — 004 와 별개 함수.
--
-- 적용 절차
--   Supabase Studio → SQL Editor → 본 파일 paste → Run.
--
-- 검증 SQL (적용 후)
--   -- dense only
--   SELECT * FROM search_dense_only(
--     '[0.1, 0.2, ...]'::vector(1024), 60, 50, NULL
--   ) LIMIT 5;
--   -- sparse only
--   SELECT * FROM search_sparse_only('공사대금 합의해지', 60, 50, NULL) LIMIT 5;
--   → dense_only: sparse_rank IS NULL / sparse_only: dense_rank IS NULL
-- ============================================================

BEGIN;

-- ---------------- search_dense_only RPC ----------------
CREATE OR REPLACE FUNCTION search_dense_only(
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
    )
    SELECT chunk_id,
           doc_id,
           (1.0 / (k_rrf + rank)::FLOAT)::FLOAT AS rrf_score,
           rank::INT       AS dense_rank,
           NULL::INT       AS sparse_rank
      FROM dense_hits
     ORDER BY rrf_score DESC
     LIMIT top_k;
$$;


-- ---------------- search_sparse_only RPC (008 신규, schema 일관) ----------------
CREATE OR REPLACE FUNCTION search_sparse_only(
    query_text   TEXT,
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
    WITH sparse_hits AS (
        SELECT c.id AS chunk_id, c.doc_id,
               ROW_NUMBER() OVER (
                   ORDER BY pgroonga_score(c.tableoid, c.ctid) DESC
               ) AS rank
          FROM chunks c
          JOIN documents d ON d.id = c.doc_id
         WHERE c.text &@~ query_text
           AND d.deleted_at IS NULL
           AND (user_id_arg IS NULL OR d.user_id = user_id_arg)
           AND (c.flags->>'filtered_reason') IS NULL
         ORDER BY pgroonga_score(c.tableoid, c.ctid) DESC
         LIMIT top_k
    )
    SELECT chunk_id,
           doc_id,
           (1.0 / (k_rrf + rank)::FLOAT)::FLOAT AS rrf_score,
           NULL::INT      AS dense_rank,
           rank::INT      AS sparse_rank
      FROM sparse_hits
     ORDER BY rrf_score DESC
     LIMIT top_k;
$$;

COMMIT;

-- ============================================================
-- 끝. Python search.py 가 mode 별 RPC 분기 — W20 Day 2.
-- ============================================================
