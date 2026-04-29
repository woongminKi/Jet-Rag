-- ============================================================
-- 004_pgroonga_korean_fts.sql — W3 v0.5 §3.A (DE-60) + DE-62
-- ============================================================
-- 명세: work-log/2026-04-29 W3 스프린트 명세 v0.5.md (CONFIRMED)
-- 채택 결정 (Q1·Q2 답):
--   DE-60: simple FTS (003) → PGroonga 한국어 형태소 분석기 (Mecab) 로 교체.
--          한국어 조사/어미를 분해하여 sparse hits 0건 회귀 차단
--          (직전 senior-qa 회귀: q="공사대금 합의해지" → sparse_hits=0).
--   DE-62: chunks.flags JSONB 컬럼 신설 — 청킹 품질 진단 도구
--          (scripts/diagnose_chunk_quality.py) 가 표 노이즈/헤더-푸터 의심 청크에
--          flags.filtered_reason 을 기록 → 검색에서 자동 제외.
--   chunk.py 본격 변경은 W4-Q-14 로 deferred (work-log/2026-04-29 청킹 정책 검토.md).
--
-- 003 의 simple FTS 인프라(`chunks.fts` 컬럼 + GIN 인덱스)는 본 마이그레이션에서 제거.
-- 003 의 HNSW 인덱스 (idx_chunks_dense, idx_documents_embed) 와 pg_trgm 은 그대로 유지.
--
-- 적용 절차: Claude Code MCP `apply_migration` 으로 단일 트랜잭션 실행.
--   (별도 단계, 본 파일은 SQL 작성만)
--
-- 검증 SQL (적용 후 메인 스레드 책임 — v0.5 §3.A AC):
--   SELECT * FROM search_hybrid_rrf('공사대금 합의해지', '[0,...,0]'::vector(1024), 60, 50, NULL)
--      LIMIT 10;
--   → sparse_rank IS NOT NULL 인 row 가 1건 이상이어야 함.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1) PGroonga extension 활성화 (Mecab 토크나이저 기본 활성)
-- ------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgroonga;

-- ------------------------------------------------------------
-- 2) chunks.flags 컬럼 신설 (DE-62 — Q2 답: 부재 확인됨)
--    documents.flags 와 동일 패턴. diagnose_chunk_quality.py 가
--    표 노이즈/헤더-푸터 의심 청크에 {"filtered_reason": "..."} 기록.
-- ------------------------------------------------------------
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS flags JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_chunks_flags
    ON chunks USING GIN (flags);

-- ------------------------------------------------------------
-- 3) 003 의 simple FTS 컬럼/인덱스 제거
--    DROP COLUMN 시 GIN 인덱스도 cascade 로 사라지지만 명시 DROP 으로 의도 표시.
-- ------------------------------------------------------------
DROP INDEX IF EXISTS idx_chunks_fts;
ALTER TABLE chunks DROP COLUMN IF EXISTS fts;

-- ------------------------------------------------------------
-- 4) PGroonga 인덱스 — text 컬럼 직접 인덱싱 (Mecab 형태소 분석)
--    조사/어미 분해 + 정확한 한국어 매칭. ts_vector 별도 컬럼 불필요.
-- ------------------------------------------------------------
CREATE INDEX idx_chunks_text_pgroonga
    ON chunks USING pgroonga (text);

-- ------------------------------------------------------------
-- 5) search_hybrid_rrf RPC 재작성
--    - sparse path: tsvector @@ tsquery → text &@~ query_text (PGroonga query 모드)
--    - sparse 정렬: ts_rank → pgroonga_score(tableoid, ctid)
--    - dense + sparse 양쪽에 (c.flags->>'filtered_reason') IS NULL 추가 (DE-62)
--    - 시그니처 (인자/반환 컬럼) 003 과 100% 동일 — 라우터 호출부 변경 X
--
-- &@~ 는 PGroonga query 모드 (자연어 + and/or). 단순 매칭 &@ 보다 자연어에 적합.
-- pgroonga_score 는 PostgreSQL 시스템 컬럼 (tableoid, ctid) 을 인자로 받음.
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
-- 6) search_sparse_only_pgroonga RPC 신설 (옵션 B 채택)
--    채택 사유:
--      PostgREST 는 PGroonga 의 &@~ 연산자를 직접 노출하지 않음 (안전한 화이트리스트
--      연산자만 노출). raw filter (`filter("text", "%26@%7E", q)`) 시도는 PostgREST
--      파싱 단계에서 거부될 가능성 높음 + 정렬도 보장 안 됨 (직전 qa E-6 회귀).
--      → 별도 RPC 로 캡슐화하면 (1) 정렬 보장 (2) flags 필터 일관 (3) deleted_at 필터
--      RPC 안에서 명시. dense path 와 sparse path 의 구현 일관성 유지.
--
--    search.py 의 `_sparse_only_fallback` 가 본 RPC 를 호출.
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
               ORDER BY pgroonga_score(c.tableoid, c.ctid) DESC
           )::INT AS sparse_rank
      FROM chunks c
      JOIN documents d ON d.id = c.doc_id
     WHERE c.text &@~ query_text
       AND d.deleted_at IS NULL
       AND (user_id_arg IS NULL OR d.user_id = user_id_arg)
       AND (c.flags->>'filtered_reason') IS NULL
     ORDER BY pgroonga_score(c.tableoid, c.ctid) DESC
     LIMIT top_k;
$$;

COMMIT;

-- ------------------------------------------------------------
-- (참고) PGroonga 인덱스 빌드 시간:
--   chunks 1k 기준 ~20초 추정 (Mecab 토큰화 포함). 003 의 HNSW (~50초) 보다 빠름.
--   기존 chunks 데이터에 자동 백필됨 (CREATE INDEX 가 처리).
-- ------------------------------------------------------------
