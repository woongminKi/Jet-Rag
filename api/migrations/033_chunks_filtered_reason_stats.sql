-- ============================================================
-- 033_chunks_filtered_reason_stats.sql — dense 검색 계획 오추정 수정 (2026-09-15 장애)
-- ============================================================
-- 배경
--   search_dense_only / search_hybrid_rrf 의 dense CTE 는
--     WHERE (c.flags->>'filtered_reason') IS NULL ... ORDER BY dense_vec <=> q LIMIT top_k
--   인데, JSON 식에 통계가 없어 planner 가 이 조건의 결과를 **185행**으로 추정했다(실제 21,920).
--   그래서 LIMIT 50 을 채우려면 HNSW 인덱스의 27% 를 훑어야 한다고 보고 **Seq Scan + 정렬**을 골랐다.
--   37k 벡터(TOAST 194MB)를 매 질의마다 detoast 하는 계획이다. 이전엔 TOAST 가 캐시(224MB)에 있어
--   P95 1.7s 로 버텼고, 2026-09-15 SK 문서 25,806 청크 재적재로 캐시가 밀리자 9~20s → Edge 500.
--
-- 수정
--   식 인덱스를 만들면 ANALYZE 가 그 식의 통계를 모아 추정이 21,888 로 맞아지고 HNSW 를 탄다.
--   실측: 함수 직접 호출 13.7s → 1.21s, API hybrid 500 → 1.4s. 인덱스 자체는 조회에 안 쓰여도 된다 —
--   통계가 목적이다.
--
-- 남은 과제(미적용)
--   hnsw.ef_search 기본 40 이라 top_k 50 요청에 dense 후보가 ~25개만 온다(이전 Seq Scan 은 정확히 50).
--   ef_search=100 은 50개를 주지만 15s 로 되돌아간다(계획이 다시 바뀜). pgvector 0.8 의
--   hnsw.iterative_scan = relaxed_order 로 해결할 수 있는지 별도 실측 필요.
--
-- 적용: supabase db query --linked -f api/migrations/033_chunks_filtered_reason_stats.sql
--       (CREATE INDEX CONCURRENTLY 는 트랜잭션 밖이어야 한다 — Management API 경로는 통과했다. 2026-09-15)
-- 검증:
--   EXPLAIN 의 idx_chunks_dense 행에 rows=2만대 추정, Execution Time 2s 이내.
-- ============================================================

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunks_filtered_reason
    ON chunks ((flags->>'filtered_reason'));

ANALYZE chunks;

-- 롤백
--   DROP INDEX CONCURRENTLY IF EXISTS idx_chunks_filtered_reason;
