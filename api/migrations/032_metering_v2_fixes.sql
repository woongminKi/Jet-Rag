-- ============================================================
-- 032_metering_v2_fixes.sql — 031 후속 교정 (자동 수집 ① 스펙 §4 S4)
-- ============================================================
-- 배경
--   031 을 적용한 뒤 통합 리뷰에서 두 가지가 나왔다. 둘 다 031 을 고쳐 다시 돌릴 수 없어서
--   (이미 운영에 적용됨) 별도 파일로 낸다.
--
-- 바뀌는 것
--   1. vision_quota_release 에 p_force 인자 + service_role EXECUTE 권한.
--      031 은 PUBLIC/anon/authenticated 에서 REVOKE 만 하고 **service_role 에 GRANT 를 안 했다.**
--      cron 은 postgres 소유라 돌지만, 사람이 PostgREST 로 부르면 permission denied 다.
--      플랜 업그레이드 직후 수동 해제 같은 게 그 경로다.
--   2. 재투입의 경합을 닫는다. 031 은 `ingest_queue_send` 를 **먼저** 하고 UPDATE 를 나중에
--      해서, 같은 함수가 두 번 겹쳐 돌면(수동 호출 + cron) 같은 잡의 페이로드가 큐에 두 번
--      들어간다 → 같은 문서를 두 번 처리한다. SELECT ... FOR UPDATE 로 행을 잠그고,
--      **UPDATE 가 실제로 1 행을 바꿨을 때만** 큐에 넣는다.
--
--   upload_burst_sweep() 은 **일부러 cron 전용으로 남긴다** — 사람이 부를 이유가 없고,
--   부를 수 있게 하면 남용 방지 카운터를 밖에서 지울 수 있는 문이 하나 생긴다.
--
-- 적용 절차: supabase db query --linked -f api/migrations/032_metering_v2_fixes.sql
--            (또는 Supabase Studio → SQL Editor → paste → Run)
--
-- 검증 SQL:
--   SELECT has_function_privilege('service_role', 'public.vision_quota_release(boolean)', 'EXECUTE');
--     → t
--   SELECT has_function_privilege('anon', 'public.vision_quota_release(boolean)', 'EXECUTE');
--     → f   (authenticated 도 f 여야 한다)
--   SELECT has_function_privilege('service_role', 'public.upload_burst_sweep()', 'EXECUTE');
--     → f   (cron 전용 — 의도한 값이다)
--   SELECT p.oid::regprocedure FROM pg_proc p
--     JOIN pg_namespace n ON n.oid = p.pronamespace
--    WHERE n.nspname = 'public' AND p.proname = 'vision_quota_release';
--     → public.vision_quota_release(boolean) **한 줄만** (0-인자 버전이 남아 있으면 안 된다)
--   SELECT public.vision_quota_release();        -- 월초가 아니면 0 (달 가드)
--   SELECT public.vision_quota_release(TRUE);    -- 강제 — 보류 잡이 없으면 0
--   SELECT jobname, schedule, active FROM cron.job WHERE jobname = 'vision-quota-release';
--     → '0 15 * * *', active = t (031 이 등록한 그대로, 이 파일은 cron 을 건드리지 않는다)
-- ============================================================

-- 1. 0-인자 버전을 먼저 지운다.
--    `CREATE OR REPLACE FUNCTION f(p BOOLEAN DEFAULT FALSE)` 는 시그니처가 달라서 기존
--    `f()` 를 **대체하지 않고 하나 더 만든다.** 그러면 `SELECT f()` 가 두 후보에 걸려
--    "is not unique" 로 깨진다 — cron 본문이 바로 그 호출이다. 그래서 DROP 이 먼저다.
--    cron.job 은 명령을 텍스트로 들고 있어 DROP 에 걸리는 의존성이 없다.
DROP FUNCTION IF EXISTS public.vision_quota_release();

-- 2. 재투입 — 달 가드 + p_force + 경합 차단
CREATE OR REPLACE FUNCTION public.vision_quota_release(p_force BOOLEAN DEFAULT FALSE)
RETURNS INTEGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r RECORD; n INTEGER := 0; n_upd INTEGER;
BEGIN
  -- 한도는 월 단위다. 같은 달에 풀면 워커 게이트가 그날 안에 다시 보류하므로 헛돈다.
  -- p_force 는 그 가드를 넘는다 — 플랜 업그레이드 직후 수동 해제용.
  IF NOT p_force
     AND date_trunc('month', now() AT TIME ZONE 'Asia/Seoul')
       = date_trunc('month', (now() - interval '1 day') AT TIME ZONE 'Asia/Seoul') THEN
    RETURN 0;
  END IF;

  -- FOR UPDATE 로 행을 잠근다. 두 세션이 겹쳐 돌면 뒤엣놈은 여기서 기다렸다가
  -- 이미 queued 로 바뀐 행을 보게 된다.
  FOR r IN SELECT id, deferred_task FROM ingest_jobs
            WHERE status = 'deferred_quota' AND deferred_task IS NOT NULL
            FOR UPDATE LOOP
    -- **UPDATE 가 먼저다.** WHERE 에 status 를 한 번 더 걸어, 그 사이 남이 가져간 행이면
    -- 0 행이 바뀐다. 큐 투입은 이 UPDATE 가 성공했을 때만 — 순서를 뒤집으면 같은
    -- 페이로드가 큐에 두 번 들어가 같은 문서를 두 번 처리한다.
    UPDATE ingest_jobs
       SET status = 'queued', deferred_task = NULL, error_msg = NULL,
           current_stage = NULL, started_at = NULL
     WHERE id = r.id AND status = 'deferred_quota';
    GET DIAGNOSTICS n_upd = ROW_COUNT;
    IF n_upd = 1 THEN
      PERFORM public.ingest_queue_send(r.deferred_task, 0);
      n := n + 1;
    END IF;
  END LOOP;
  RETURN n;
END; $$;

-- 3. 권한 — 031 이 빠뜨린 GRANT.
REVOKE ALL ON FUNCTION public.vision_quota_release(BOOLEAN) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.vision_quota_release(BOOLEAN) TO service_role;

-- ============================================================
-- 롤백 (031 상태로 되돌린다 — 0-인자, 경합 열린 채)
--   DROP FUNCTION IF EXISTS public.vision_quota_release(BOOLEAN);
--   CREATE OR REPLACE FUNCTION public.vision_quota_release()
--   RETURNS INTEGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
--   DECLARE r RECORD; n INTEGER := 0;
--   BEGIN
--     IF date_trunc('month', now() AT TIME ZONE 'Asia/Seoul')
--        = date_trunc('month', (now() - interval '1 day') AT TIME ZONE 'Asia/Seoul') THEN
--       RETURN 0;
--     END IF;
--     FOR r IN SELECT id, deferred_task FROM ingest_jobs
--               WHERE status = 'deferred_quota' AND deferred_task IS NOT NULL LOOP
--       PERFORM public.ingest_queue_send(r.deferred_task, 0);
--       UPDATE ingest_jobs
--          SET status = 'queued', deferred_task = NULL, error_msg = NULL,
--              current_stage = NULL, started_at = NULL
--        WHERE id = r.id;
--       n := n + 1;
--     END LOOP;
--     RETURN n;
--   END; $$;
--   REVOKE ALL ON FUNCTION public.vision_quota_release() FROM PUBLIC, anon, authenticated;
--   (cron 'vision-quota-release' 은 그대로 둔다 — 본문이 인자 없는 호출이라 양쪽 다 돈다.)
-- ============================================================
