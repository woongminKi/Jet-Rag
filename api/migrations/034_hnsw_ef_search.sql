-- ============================================================
-- 034_hnsw_ef_search.sql — hnsw.ef_search = 100 (DB 수준 GUC, 2026-09-16 적용·유지)
-- ============================================================
-- 목적
--   dense 후보 부족. HNSW 는 ef_search(기본 40)개까지만 후보를 내고, 거기서 (flags->>'filtered_reason')
--   IS NULL · user_id · deleted_at 필터가 걸러내면 top_k 50 요청에 25~41개만 온다.
--
-- 이력 — 같은 설정이 한 번 실패했다 (2026-09-15, 원래 이 파일은 "적용하지 않음" 기록이었다)
--   인덱스 237~265MB(shared_buffers 224MB 초과, 죽은 엔트리 다수) 상태에서
--   ALTER DATABASE SET hnsw.iterative_scan='relaxed_order' + ef_search=100 을 넣자 API 의 **dense 전용 모드가
--   1.0s → 10~14s**. Edge 가 PostgREST 로 top_k 를 바인드 파라미터로 넘겨 generic plan 이 LIMIT 을 모른 채
--   HNSW 경로 비용(ef·iterative 로 상승)을 Seq Scan 보다 높게 봐 계획이 뒤집혔다. iterative_scan 만 되돌려도
--   10s, plan_cache_mode=force_custom_plan 도 무효(풀 연결마다 설정이 섞여 후보 수가 25↔49 로 흔들림).
--   전부 RESET 해 기준선(dense 0.9s, hybrid 1.0s)으로 돌아갔다.
--
-- 재적용 (2026-09-16 01:58 KST) — 036(halfvec) 뒤, 인덱스 25MB 가 캐시에 들어가고 죽은 엔트리 0 인 상태
--   ef_search 만 100. iterative_scan 은 건드리지 않는다(위 악화의 원인, docs/ops/db-write-control.md §5 금지).
--   실측(골든 120행 × 3모드, 적용 전→후):
--     hybrid top-1 99/120 → 99/120, avg 246 → 217ms
--     dense  top-1 100/120 → 100/120, avg 158 → 231ms (max 323 → 1,917ms 1건, 콜드)
--     sparse 54/120 → 54/120
--     PostgREST rpc search_dense_only(top_k 50): 행수 40~41 → **50**, 웜 57~78ms. dense 전용 10s 재현 없음.
--   조건: 인덱스가 다시 110MB 를 넘으면(§1 예산) 이 설정부터 의심한다.
--
-- 참고
--   - `ALTER DATABASE SET hnsw.*` 는 같은 세션에서 벡터 연산으로 vector.so 를 먼저 로드해야 42501 이 안 난다.
--   - 벤치에서 벡터를 서브쿼리로 넘기면 함수 인라인이 막혀(contain_subplans) Seq Scan 을 밟는다 — 리터럴로 넣을 것.
--   - `ALTER FUNCTION ... SET` 도 proconfig 때문에 인라인을 막으므로 금지.
--   - 새 연결부터 적용된다(PostgREST 풀은 자연 교체). 적용 확인은 PostgREST rpc 의 행수(50)로.
--
-- 적용: supabase db query --linked -f api/migrations/034_hnsw_ef_search.sql
-- 롤백: select '[1,2]'::vector <-> '[1,2]'::vector; alter database postgres reset hnsw.ef_search;
-- ============================================================

-- 순서 가드 — 번호는 034 지만 **036(halfvec) 뒤에만** 안전하다(위 이력: vector(1024)+큰 인덱스에서는 dense 10s).
-- 새 DB 에 001..037 을 번호순으로 재생하면 여기서 멈춘다. 036 을 먼저 적용하고 다시 돌릴 것.
DO $$
BEGIN
    IF (SELECT format_type(atttypid, atttypmod) FROM pg_attribute
         WHERE attrelid = 'public.chunks'::regclass AND attname = 'dense_vec') <> 'halfvec(1024)' THEN
        RAISE EXCEPTION '034 는 036(halfvec) 뒤에 적용한다 — chunks.dense_vec 가 아직 halfvec(1024) 가 아니다';
    END IF;
END $$;

-- vector.so 선로드 (없으면 ALTER DATABASE SET hnsw.* 가 42501)
SELECT '[1,2]'::vector <-> '[1,2]'::vector AS preload;

ALTER DATABASE postgres SET hnsw.ef_search = 100;

-- 확인
SELECT unnest(setconfig) AS db_setting
  FROM pg_db_role_setting
 WHERE setdatabase = (SELECT oid FROM pg_database WHERE datname = 'postgres')
   AND setrole = 0;   -- DB 전체 설정만(롤별 설정 제외)
