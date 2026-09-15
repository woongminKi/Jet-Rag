-- ============================================================
-- 034_hnsw_iterative_scan.sql — dense 후보 부족 수정 (2026-09-15 실측)
-- ============================================================
-- 배경
--   search_dense_only / search_hybrid_rrf 는 HNSW 후보(hnsw.ef_search 기본 40)에서
--   (flags->>'filtered_reason') IS NULL 로 40.9% 를 걸러낸 뒤 LIMIT 50 을 채운다.
--   그래서 dense 후보가 25~41개만 온다(실측, 실제 벡터 2종·합성 1종). 브루트포스 top-50 대비
--   재현율 19~41/50.
--
-- 수정
--   pgvector 0.8 의 iterative scan 을 켠다. LIMIT 을 못 채우면 그래프를 더 훑는다.
--   실측(실제 벡터): ef 40 + relaxed_order → 50/50, 순위도 strict 와 동일, 3.6ms/1,192 buffers.
--   ef_search=100 은 여유분 — 첫 반복에서 50 을 채워 iterative 경로에 아예 안 들어간다.
--   (ef 100 에서 relaxed_order 와 off 의 계획·버퍼가 완전히 같음: 1788/1788, 1345/1345.)
--
-- 왜 함수별 SET 이 아니라 DB 수준인가
--   ALTER FUNCTION ... SET 은 pg_proc.proconfig 를 채우고, PostgreSQL 은 proconfig 가 있는
--   SQL 함수를 **인라인하지 않는다**. 인라인이 막히면 LIMIT top_k 가 Param 으로 남아 planner 가
--   Seq Scan + 전체 정렬(194MB detoast, 15s)을 고른다 — 2026-09-15 벤치가 벡터를 서브쿼리로
--   넘겨 인라인을 막았을 때 그 경로를 실제로 밟았다. DB 수준 GUC 는 인라인에 영향이 없다.
--
-- 주의
--   - 새 연결부터 적용된다(PostgREST 커넥션 풀은 재접속 시).
--   - `show hnsw.ef_search` 는 벡터 연산 전엔 42704 를 낸다(vector.so 미로드). 검증은 함수 호출로.
--   - hnsw.max_scan_tuples 기본 20,000 < 미필터 청크 21,920 — 코퍼스가 커지면 상한을 올릴 것.
--
-- 적용: supabase db query --linked -f api/migrations/034_hnsw_iterative_scan.sql
-- 검증: 실제 청크 벡터로 SELECT count(*) FROM search_dense_only(v, 60, 50, '<owner>') → 50
-- ============================================================

ALTER DATABASE postgres SET hnsw.iterative_scan = 'relaxed_order';
ALTER DATABASE postgres SET hnsw.ef_search = 100;

-- 롤백
--   ALTER DATABASE postgres RESET hnsw.iterative_scan;
--   ALTER DATABASE postgres RESET hnsw.ef_search;
