-- ============================================================
-- 037_chunks_set_dense_vec_batch.sql — dense_vec 를 한 문장·한 트랜잭션으로 쓰는 RPC
-- ============================================================
-- 배경 (2026-09-16 사고 4, work-log 2026-09-16 §4)
--   embed 태스크는 청크 64개의 dense_vec 을 **단건 UPDATE 64번**으로 각각 커밋했다. `synchronous_commit=on`
--   이라 커밋마다 WAL fsync 가 나고, Micro 의 디스크 I/O 예산이 얕아지면 fsync 가 초 단위로 늘어져
--   UPDATE 하나가 authenticator 의 statement_timeout(8s)에 걸린다. 실패한 태스크는 11분 뒤 재시도라
--   처리량이 무너지고, 그 배치가 검색을 15~61s 로 밀어냈다(05:25~06:03 무응답 38분).
--   halfvec(036)로 인덱스는 265MB→25MB 가 됐지만 **쓰기 횟수**는 그대로였다. 이 파일이 그걸 고친다:
--   태스크당 fsync 64회 → 2회(32행씩).
--
-- 함수
--   public.chunks_set_dense_vec(p_rows jsonb) returns integer
--     p_rows = [{"id": "<uuid>", "vec": [f, f, ...]}, ...]   (vec 는 1024 차원 JSON 배열)
--     한 UPDATE 로 전부 쓰고 갱신된 행수를 돌려준다(호출 쪽은 32행씩 보낸다 = 태스크당 fsync 2회).
--     호출 쪽은 반환값 = 보낸 개수를 확인한다
--     (없는 id 는 조용히 빠지므로, 불일치는 "청크가 그 사이 지워졌다"는 뜻이다).
--   - 텍스트 '[...]' 를 halfvec(1024) 로 캐스팅해 fp16 양자화는 DB 안에서 한 번에 한다.
--   - SECURITY INVOKER. service_role 만 부른다(Edge 워커·Python 복구 스크립트). 다른 롤은 회수.
--   - 멱등: 같은 행을 다시 보내면 같은 값으로 덮어쓴다. 호출 쪽이 `dense_vec IS NULL` 인 행만 고르므로
--     재시도에서 이미 쓴 행은 애초에 안 온다.
--
-- 정리
--   035 의 `null_filtered_dense_vec` 는 일회성 데이터 정리 함수였고 2026-09-16 01:37 에 끝났다(잔여 0).
--   운영 표면에 남길 이유가 없어 여기서 지운다. 필요하면 035 파일을 다시 apply 하면 된다.
--
-- 적용: supabase db query --linked -f api/migrations/037_chunks_set_dense_vec_batch.sql
-- 검증
--   select public.chunks_set_dense_vec('[]'::jsonb);                       -- 0
--   select public.chunks_set_dense_vec(jsonb_build_array(jsonb_build_object(
--     'id', (select id from chunks where dense_vec is not null limit 1),
--     'vec', (select dense_vec::text::jsonb from chunks where dense_vec is not null limit 1))));  -- 1 (같은 값 덮어씀)
--   set local role anon; select public.chunks_set_dense_vec('[]'::jsonb);  -- 42501 permission denied
-- 롤백
--   drop function if exists public.chunks_set_dense_vec(jsonb);
--   (Edge 워커·Python 은 이 함수가 없으면 embed 가 실패한다 — 코드도 같이 되돌릴 것)
--   이 파일이 지운 035 의 null_filtered_dense_vec 가 다시 필요하면 035 파일을 다시 apply 한다.
-- ============================================================

CREATE OR REPLACE FUNCTION public.chunks_set_dense_vec(p_rows jsonb)
RETURNS integer
LANGUAGE sql
AS $$
    WITH v AS (
        SELECT (r->>'id')::uuid              AS id,
               (r->>'vec')::halfvec(1024)    AS vec
          FROM jsonb_array_elements(p_rows) AS r
    ),
    upd AS (
        UPDATE public.chunks c
           SET dense_vec = v.vec
          FROM v
         WHERE c.id = v.id
     RETURNING 1
    )
    SELECT count(*)::integer FROM upd;
$$;

COMMENT ON FUNCTION public.chunks_set_dense_vec(jsonb) IS
    'embed 단계 배치 쓰기 — [{id, vec}] 를 한 UPDATE 로 저장하고 갱신 행수를 반환한다(fsync 1회). 037 참조.';

REVOKE ALL ON FUNCTION public.chunks_set_dense_vec(jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.chunks_set_dense_vec(jsonb) TO service_role;

-- 035 의 일회성 정리 함수 제거 (2026-09-16 01:37 완료, 잔여 0)
DROP FUNCTION IF EXISTS public.null_filtered_dense_vec(integer);
