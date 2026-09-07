-- ============================================================================
-- 028_ingest_cron_drain.sql — pg_cron 이 인제스트 워커를 주기적으로 깨운다
--
-- 마이그 026 이 큐(pgmq)와 확장(pg_cron 1.6.4 · pg_net 0.20.0)을 깔았고, 027 이
-- 중간 산출물 자리를 만들었다. 지금까지는 `api-ingest-worker/drain` 을 **사람이**
-- 불러야 큐가 돌았다. 이 마이그가 그걸 자동화한다.
--
-- ## 왜 DB 가 Edge 를 부르는가
-- 파서(mupdf WASM · @rhwp/core)가 Deno 에 있어 DB 안에서 처리할 수 없다. Edge 는
-- 스스로 깨어나지 않으므로 누군가 주기적으로 두드려야 하고, 그 역할을 pg_cron 이 한다.
--
-- ## service_role 키를 Vault 에 둔다
-- `net.http_post` 로 Edge 를 부르려면 Authorization 헤더가 필요하다. 마이그레이션
-- 파일이나 함수 본문에 키를 적으면 그대로 저장소·`pg_proc` 에 남는다. Supabase 가
-- 제공하는 `supabase_vault`(0.3.1, 설치됨)에 넣고 이름으로 참조한다.
--
-- **이 파일은 키를 담지 않는다.** 아래 STEP 1 을 사람이 한 번 실행해야 한다.
--
-- ## 큐가 비면 호출하지 않는다
-- 10 초마다 무조건 부르면 하루 8,640 번 Edge 인보케이션을 태운다. 대부분은 큐가 비어
-- 있다. `ingest_drain_tick()` 이 먼저 큐 길이를 보고 0 이면 아무것도 하지 않는다.
--
-- ## 겹쳐 도는 것은 안전하다
-- `net.http_post` 는 비동기라 응답을 기다리지 않는다. 앞 요청이 아직 도는 중에 다음
-- tick 이 올 수 있다. 그래도 pgmq 의 visibility timeout(워커가 600s 로 읽는다)이
-- 같은 메시지를 두 번 주지 않는다.
--
-- 적용 순서:
--   STEP 1 (사람) — Vault 에 키 저장
--   STEP 2 — 이 파일
--   STEP 3 — 검증 쿼리
-- ============================================================================

-- ---------------------------------------------------------------------------
-- STEP 1 (사람이 SQL Editor 에서 1 회). 이 파일에 키를 적지 않는다.
--
--   select vault.create_secret(
--     '<SUPABASE_SERVICE_ROLE_KEY 값>',
--     'ingest_worker_service_key',
--     'api-ingest-worker/drain 호출용. 마이그 028.'
--   );
--
-- 이미 있으면 갱신:
--   select vault.update_secret(
--     (select id from vault.secrets where name = 'ingest_worker_service_key'),
--     '<새 값>'
--   );
-- ---------------------------------------------------------------------------

-- 함수 URL 도 Vault 에 둔다. 프로젝트 ref 가 바뀌어도 SQL 수정 없이 따라간다.
-- (URL 은 비밀이 아니지만 한곳에서 관리하는 편이 낫다.)
--   select vault.create_secret(
--     'https://<project-ref>.supabase.co/functions/v1/api-ingest-worker/drain',
--     'ingest_worker_drain_url',
--     'pg_cron 이 부를 워커 엔드포인트. 마이그 028.'
--   );

-- ---------------------------------------------------------------------------
-- STEP 2 — tick 함수
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.ingest_drain_tick()
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pgmq, vault, net
AS $$
DECLARE
  v_pending bigint;
  v_key     text;
  v_url     text;
  v_req     bigint;
BEGIN
  -- 큐가 비면 아무것도 하지 않는다. 대부분의 tick 이 여기서 끝난다.
  SELECT count(*) INTO v_pending FROM pgmq.q_ingest_tasks;
  IF v_pending = 0 THEN
    RETURN NULL;
  END IF;

  SELECT decrypted_secret INTO v_key
    FROM vault.decrypted_secrets WHERE name = 'ingest_worker_service_key';
  SELECT decrypted_secret INTO v_url
    FROM vault.decrypted_secrets WHERE name = 'ingest_worker_drain_url';

  -- 키가 없으면 **조용히 넘어가지 않는다.** 조용하면 큐가 쌓이는 이유를 못 찾는다.
  IF v_key IS NULL OR v_url IS NULL THEN
    RAISE EXCEPTION
      'Vault 에 ingest_worker_service_key / ingest_worker_drain_url 이 없다 (마이그 028 STEP 1)';
  END IF;

  SELECT net.http_post(
           url     := v_url,
           headers := jsonb_build_object(
                        'Authorization', 'Bearer ' || v_key,
                        'Content-Type',  'application/json'),
           body    := '{}'::jsonb,
           -- Edge 가 예산(1.5s)만큼 돌고 응답한다. 넉넉히 준다.
           timeout_milliseconds := 30000
         ) INTO v_req;

  RETURN v_req;
END;
$$;

COMMENT ON FUNCTION public.ingest_drain_tick() IS
  'pg_cron 이 부른다. 큐에 작업이 있을 때만 api-ingest-worker/drain 을 호출한다. 마이그 028.';

-- 권한: cron 은 postgres 로 돈다. 그 외에는 아무도 부를 수 없어야 한다 —
-- 이 함수는 Vault 의 service_role 키를 읽는다.
REVOKE ALL ON FUNCTION public.ingest_drain_tick() FROM PUBLIC, anon, authenticated;

-- ---------------------------------------------------------------------------
-- STEP 2b — 스케줄
--
-- pg_cron 1.5+ 는 초 단위 표현을 받는다. 10 초 주기면 업로드 후 반응이 빠르면서도
-- 빈 tick 비용이 거의 없다(큐가 비면 http 호출을 안 한다).
--
-- 같은 이름으로 다시 걸면 중복되므로 먼저 지운다.
-- ---------------------------------------------------------------------------

SELECT cron.unschedule('ingest-drain')
 WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'ingest-drain');

SELECT cron.schedule('ingest-drain', '10 seconds', $cron$
  SELECT public.ingest_drain_tick();
$cron$);

-- ---------------------------------------------------------------------------
-- STEP 3 — 검증
--
-- 1) 잡이 걸렸는가
--    SELECT jobid, jobname, schedule, active FROM cron.job WHERE jobname = 'ingest-drain';
--    기대: 1행, active = true
--
-- 2) 빈 큐에서 tick 이 http 를 안 부르는가
--    SELECT public.ingest_drain_tick();
--    기대: NULL (큐가 비어 있을 때)
--
-- 3) 최근 실행 결과
--    SELECT status, return_message, start_time
--      FROM cron.job_run_details
--     WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname = 'ingest-drain')
--     ORDER BY start_time DESC LIMIT 5;
--    기대: status = 'succeeded'
--
-- 4) pg_net 응답 (작업을 넣은 뒤)
--    SELECT id, status_code, left(content, 200) FROM net._http_response
--     ORDER BY created DESC LIMIT 5;
--    기대: status_code = 200, content 에 {"read":…,"ok":…,"rounds":…}
--
-- 롤백:
--    SELECT cron.unschedule('ingest-drain');
--    DROP FUNCTION IF EXISTS public.ingest_drain_tick();
--    -- Vault 시크릿까지 지우려면:
--    -- DELETE FROM vault.secrets WHERE name IN
--    --   ('ingest_worker_service_key','ingest_worker_drain_url');
-- ---------------------------------------------------------------------------
