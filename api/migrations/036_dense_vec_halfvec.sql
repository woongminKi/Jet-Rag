-- ============================================================
-- 036_dense_vec_halfvec.sql — chunks.dense_vec 를 halfvec(1024) 로 전환 (인덱스 절반)
-- ============================================================
-- 배경 (2026-09-15 장애 3건 → 2026-09-16 결정: 컴퓨트 상향 없이 해결)
--   HNSW 인덱스 237MB > Micro shared_buffers 224MB 라 인덱스가 캐시에 안 들어가고, 임베딩 쓰기와
--   vacuum 이 디스크 I/O 예산을 소진해 서비스가 2시간 503 이었다.
--   전략 3단계: ① 필터 청크는 임베딩 안 함(마이그 035 + embed 단계 코드) ② **이 파일**: 16비트 halfvec
--   ③ 대량 쓰기 운용 규칙(야간·매분 cron·autovacuum 수동).
--   실측(2026-09-16 01시): 벡터 14,281 중 필터 4,612 → ①로 비움. 최종 대상 22,077(미필터 전체).
--   22,077 × 2KB ≈ 45MB + 그래프 ≈ 65MB 예상 — 캐시 안.
--
-- 전제 (**적용 전 반드시**)
--   1. `cron.unschedule('ingest-drain')` — 큐 정지 (테이블 재작성 중 embed UPDATE 충돌 방지)
--   2. `ALTER TABLE chunks SET (autovacuum_enabled = false)` — 이미 off (2026-09-15)
--   3. 마이그 035 의 필터 벡터 비우기가 끝났는지: SELECT count(*) FROM chunks WHERE dense_vec IS NOT NULL
--      AND (flags->>'filtered_reason') IS NOT NULL → 0
--   4. 야간·저부하. 테이블 재작성(TOAST 194MB 읽기 + 절반 쓰기)과 인덱스 빌드가 일회성 I/O 를 쓴다.
--
-- 무엇이 바뀌나
--   - `chunks.dense_vec vector(1024)` → `halfvec(1024)`. 쓰기 쪽(supabase-js 배열 / Python 리스트)은
--     텍스트 입력이라 그대로 동작한다(halfvec 도 '[...]' 문자열을 받는다).
--   - HNSW 인덱스를 `halfvec_cosine_ops` 로 재생성. m·ef_construction 동일.
--   - `search_dense_only` / `search_hybrid_rrf`: 파라미터는 `vector` 그대로 두고 비교 시
--     `query_dense::halfvec(1024)` 로 캐스팅 — Edge 호출 코드 변경 0.
--   - `documents.doc_embedding vector(1024)` 는 14행이라 그대로 둔다.
--
-- 적용 (2026-09-16 01:46 KST, 실행 완료)
--   `supabase db query --linked -f` 는 **statement_timeout 120s** 가 걸린다(실측: pg_sleep(150) 이 57014 로 취소).
--   그래서 테이블 재작성+인덱스 빌드는 pg_cron 일회성 잡으로 돌렸다 — DB 안에서 실행되므로 HTTP·타임아웃과 무관:
--     select cron.schedule('halfvec-migrate-036', '* * * * *', $job$
--       SET statement_timeout = 0; SET maintenance_work_mem = '128MB';
--       DO $do$ BEGIN
--         EXECUTE 'LOCK TABLE public.chunks IN ACCESS EXCLUSIVE MODE';   -- 1분 뒤 2번째 실행이 뜨면 여기서 막혔다가 건너뛴다
--         IF (컬럼 타입) = 'vector(1024)' THEN <아래 1·2·3절의 EXECUTE> END IF;
--       END $do$;
--       <아래 4절 함수 2개>; ANALYZE chunks;
--       SELECT cron.unschedule('halfvec-migrate-036') WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname='halfvec-migrate-036');
--     $job$);
--   결과: 01:46:00 시작 → 01:46:37 성공 (**37초**). 이 파일을 그대로 apply 하는 것과 같은 상태.
--   실측: 인덱스 265MB → **25MB**, chunks 총 506MB → **75MB**(재작성이 삭제 잔해도 회수), 힙 20MB, 벡터 9,669.
--   정확 top-50(enable_indexscan=off) 이 fp16 전환 전후 3개 질의 모두 50/50 일치 → 양자화 손실 없음.
--   HNSW 기본(ef_search 40) 재현율: 전환 전 5·21·39/50(죽은 엔트리가 ef 예산을 먹음) → 후 15·40·41/50.
--   골든셋 120행 hybrid: top-1 81.7%→82.5%, top-3 89.2%→90.0%, p95 1,438ms→421ms.
-- 검증
--   SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid='chunks'::regclass AND attname='dense_vec';  -- halfvec(1024)
--   SELECT pg_size_pretty(pg_relation_size('idx_chunks_dense'));   -- ≈ 60~70MB
--   SELECT count(*) FROM search_dense_only((SELECT dense_vec::vector FROM chunks WHERE dense_vec IS NOT NULL LIMIT 1), 60, 50, '<owner>');  -- 50
--   EXPLAIN: Index Scan using idx_chunks_dense, 콜드 1~2s, 웜 <100ms
--   골든셋: api/scripts/golden_batch_smoke.py (2026-09-15 기준 top-1 77.5% / top-3 84.2%)
-- 사후
--   VACUUM (ANALYZE) chunks;  -- 옛 TOAST 회수(별도 트랜잭션)
--   ALTER TABLE chunks SET (autovacuum_enabled = true);
--   cron 재개: api/migrations/028 의 ingest-drain (이관 후 규칙은 '* * * * *' 매분)
-- ============================================================

-- 1. 인덱스 제거 (타입 변경은 인덱스가 있으면 실패한다)
DROP INDEX IF EXISTS idx_chunks_dense;

-- 2. 타입 전환 — 테이블 재작성
ALTER TABLE chunks
    ALTER COLUMN dense_vec TYPE halfvec(1024) USING dense_vec::halfvec(1024);

-- 3. 인덱스 재생성 (halfvec 연산자 클래스)
CREATE INDEX idx_chunks_dense ON chunks USING hnsw (dense_vec halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- 4. 검색 함수 — 비교식만 캐스팅 (시그니처·반환 동일)
CREATE OR REPLACE FUNCTION public.search_dense_only(query_dense vector, k_rrf integer DEFAULT 60, top_k integer DEFAULT 50, user_id_arg uuid DEFAULT NULL::uuid)
 RETURNS TABLE(chunk_id uuid, doc_id uuid, rrf_score double precision, dense_rank integer, sparse_rank integer)
 LANGUAGE sql
 STABLE
AS $function$
    WITH dense_hits AS (
        SELECT c.id AS chunk_id, c.doc_id,
               ROW_NUMBER() OVER (ORDER BY c.dense_vec <=> query_dense::halfvec(1024)) AS rank
          FROM chunks c
          JOIN documents d ON d.id = c.doc_id
         WHERE c.dense_vec IS NOT NULL
           AND d.deleted_at IS NULL
           AND (user_id_arg IS NULL OR d.user_id = user_id_arg)
           AND (c.flags->>'filtered_reason') IS NULL
         ORDER BY c.dense_vec <=> query_dense::halfvec(1024)
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
$function$;

CREATE OR REPLACE FUNCTION public.search_hybrid_rrf(query_text text, query_dense vector, k_rrf integer DEFAULT 60, top_k integer DEFAULT 50, user_id_arg uuid DEFAULT NULL::uuid)
 RETURNS TABLE(chunk_id uuid, doc_id uuid, rrf_score double precision, dense_rank integer, sparse_rank integer)
 LANGUAGE sql
 STABLE
AS $function$
      WITH dense_hits AS (
          SELECT c.id AS chunk_id, c.doc_id,
                 ROW_NUMBER() OVER (ORDER BY c.dense_vec <=> query_dense::halfvec(1024)) AS rank
            FROM chunks c
            JOIN documents d ON d.id = c.doc_id
           WHERE c.dense_vec IS NOT NULL
             AND d.deleted_at IS NULL
             AND (user_id_arg IS NULL OR d.user_id = user_id_arg)
             AND (c.flags->>'filtered_reason') IS NULL
           ORDER BY c.dense_vec <=> query_dense::halfvec(1024)
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
  $function$;

ANALYZE chunks;

-- 롤백 (인덱스 재생성 포함, 같은 순서로)
--   DROP INDEX IF EXISTS idx_chunks_dense;
--   ALTER TABLE chunks ALTER COLUMN dense_vec TYPE vector(1024) USING dense_vec::vector(1024);
--   CREATE INDEX idx_chunks_dense ON chunks USING hnsw (dense_vec vector_cosine_ops) WITH (m = 16, ef_construction = 64);
--   두 함수의 `::halfvec(1024)` 캐스팅 제거 (2026-09-15 이전 정의).
