-- ============================================================
-- 035_dense_vec_skip_filtered.sql — 필터된 청크의 dense_vec 을 NULL 로 되돌린다
-- ============================================================
-- 배경
--   검색 RPC 는 전부 미필터 청크만 본다:
--     search_dense_only / search_hybrid_rrf ... WHERE (c.flags->>'filtered_reason') IS NULL
--   즉 `filtered_reason` 이 붙은 청크의 dense_vec 은 **어떤 쿼리도 읽지 않는다.**
--   그런데 지금까지 embed 단계가 전 청크를 임베딩해 왔다. 전체 청크의 40.9% 가 필터
--   대상(header_footer / extreme_short / table_noise / empty)이므로, 그만큼이
--   ① DeepInfra 호출 비용 ② HNSW 인덱스 크기(실측 237MB)로만 쌓였다.
--   Micro 인스턴스의 shared_buffers 가 224MB 라 인덱스가 캐시에 안 들어가고,
--   2026-09-15 dense 검색 I/O 장애의 직접 원인이 됐다.
--
-- 코드 쪽 수정 (이 파일과 같은 커밋)
--   - supabase/functions/_shared/ingest/handlers/embed.ts
--   - api/app/ingest/stages/embed.py
--   둘 다 조회에 `flags->>filtered_reason IS NULL` 을 추가해 **앞으로는 안 만든다.**
--   이 파일은 **이미 만들어진 것을 지우는 데이터 정리**다.
--
-- 예상 영향 행수 (2026-09-15 기준)
--   약 15,145 행. 적용 전 아래 검증 쿼리로 실제 값을 먼저 확인할 것.
--
-- 적용 방법 — **한 번에 UPDATE 하지 말 것**
--   단일 UPDATE 는 15k 행의 TOAST(벡터 4KB/행)를 한 트랜잭션에 잡아 statement_timeout 과
--   WAL 급증을 부른다(2026-09-15 reingest 의 25,806행 단일 DELETE 에서 겪은 것과 같은 형태).
--   DO 블록 안에서는 COMMIT 을 할 수 없어 배치 루프를 서버에 맡길 수 없다. 그래서
--   **한 배치만 처리하고 건수를 돌려주는 함수**를 두고, 호출하는 쪽이 0 이 나올 때까지
--   sleep 을 끼워 반복한다(각 호출이 독립 트랜잭션 = 자동 커밋).
--
--     -- 1) 이 파일을 apply (함수 생성)
--     -- 2) 아래를 0 이 나올 때까지 반복 (SQL Editor 또는 psql). 2~3초 간격 권장.
--     SELECT public.null_filtered_dense_vec();          -- 기본 2000 행
--     SELECT public.null_filtered_dense_vec(500);       -- 부하가 크면 줄인다
--
--   psql 이면 컨트롤러를 셸에 둔다(각 호출이 독립 트랜잭션이다):
--     while [ "$(psql "$DB_URL" -tAc 'SELECT public.null_filtered_dense_vec()')" != 0 ]; do sleep 2; done
--
-- 검증 쿼리
--   -- 적용 전/후 모두 같은 쿼리로 본다. 완료 상태는 remaining = 0.
--   SELECT count(*) FILTER (WHERE (flags->>'filtered_reason') IS NOT NULL
--                             AND dense_vec IS NOT NULL) AS remaining,
--          count(*) FILTER (WHERE (flags->>'filtered_reason') IS NOT NULL) AS filtered_total,
--          count(*) FILTER (WHERE (flags->>'filtered_reason') IS NULL
--                             AND dense_vec IS NULL)     AS unfiltered_missing_vec,
--          count(*)                                       AS chunks_total
--     FROM chunks;
--   -- 기대: remaining 0, unfiltered_missing_vec 는 적용 전후로 **변하지 않음**
--   --       (이 마이그레이션은 미필터 청크를 건드리지 않는다).
--
-- 마무리 (별도 판단 — 이 파일에 넣지 않았다)
--   UPDATE ... SET dense_vec = NULL 은 HNSW 인덱스에서 죽은 엔트리를 남길 뿐 크기를 안 줄인다.
--   237MB 를 실제로 회수하려면 REINDEX INDEX CONCURRENTLY idx_chunks_dense 가 필요하고,
--   그 동안 디스크에 사본이 하나 더 생긴다. 남은 행수·인덱스 크기를 재고 나서 따로 결정할 것.
--
-- 롤백
--   되돌릴 수 없다(벡터 값을 버린다). 필요하면 해당 문서를 reingest 하거나
--   마킹을 지운 뒤 embed 를 다시 돌리면 미필터 조건이 참이 되어 다시 채워진다.
-- ============================================================

CREATE OR REPLACE FUNCTION public.null_filtered_dense_vec(p_batch int DEFAULT 2000)
RETURNS int
LANGUAGE plpgsql
AS $$
DECLARE
    v_count int;
BEGIN
    IF p_batch IS NULL OR p_batch < 1 THEN
        RAISE EXCEPTION 'p_batch 는 1 이상이어야 한다 (got=%)', p_batch;
    END IF;

    -- ctid 로 지목한다. 대상 행에 인덱스로 도달할 수 있는 조건이 아니므로
    -- (dense_vec IS NOT NULL 은 인덱스가 없다) 스캔은 어차피 순차다 —
    -- LIMIT 으로 배치 크기만 잡고, 잠긴 행은 건너뛰어 동시 인제스트와 안 부딪히게 한다.
    WITH victim AS (
        SELECT ctid
          FROM chunks
         WHERE (flags->>'filtered_reason') IS NOT NULL
           AND dense_vec IS NOT NULL
         LIMIT p_batch
           FOR UPDATE SKIP LOCKED
    )
    UPDATE chunks c
       SET dense_vec = NULL
      FROM victim v
     WHERE c.ctid = v.ctid;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

COMMENT ON FUNCTION public.null_filtered_dense_vec(int) IS
    '필터 마킹된 청크의 dense_vec 을 한 배치(기본 2000행)만 NULL 로 만들고 건수를 반환한다. '
    '0 이 나올 때까지 호출하는 쪽에서 sleep 을 끼워 반복한다. 035 마이그레이션 참조.';

-- 운영 API 표면에 노출하지 않는다 — service_role(및 DB 소유자)만 부른다.
REVOKE ALL ON FUNCTION public.null_filtered_dense_vec(int) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.null_filtered_dense_vec(int) FROM anon;
REVOKE ALL ON FUNCTION public.null_filtered_dense_vec(int) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.null_filtered_dense_vec(int) TO service_role;

-- 정리가 끝난 뒤 한 번 — dense CTE 의 행수 추정이 033 이후 다시 바뀌었다.
-- (이 파일 apply 시점이 아니라 **루프가 0 을 반환한 뒤** 실행할 것.)
--   ANALYZE chunks;

-- 롤백 (함수 제거만. 데이터는 되돌릴 수 없다)
--   DROP FUNCTION IF EXISTS public.null_filtered_dense_vec(int);
