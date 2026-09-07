-- ============================================================================
-- 029_billing_cron.sql — pg_cron 이 정기결제 배치를 깨운다
--
-- Railway 를 끄면 `scripts/billing_charge.py` 를 돌리던 cron 이 함께 사라진다.
-- 그 자리를 pg_cron 이 대신한다. 마이그 028(인제스트 drain)이 만든 패턴을 따른다.
--
-- ## 지금 Railway cron 은 **돌고 있지 않다**
-- W5-6 때 `0 18 * * *` 로 설정하기로 해 놓고 `JETRAG_KAKAOPAY_SECRET_KEY`(카카오페이
-- 심사 대기) 때문에 켜지 않았다. 켜면 `billing_charge.py` 가 provider 생성 단계에서
-- RuntimeError 를 낸다. 그래서 **이 마이그는 돌던 것을 옮기는 게 아니라, 처음부터
-- 대기 중이던 것을 Edge 쪽에 세우는 것**이다.
--
-- Edge 쪽은 그 문제가 없다. `/billing/run` 은 결제 키가 없으면 provider 를 만들기
-- **전에** 503 으로 끊는다(`routes.ts` 의 `ensureEnabled`). 키를 넣기 전에 스케줄을
-- 걸어 둬도 매일 503 한 번일 뿐 예외가 아니다.
--
-- ## 028 과 다른 점 둘
-- 1. **큐 가드가 없다.** 028 은 10 초마다 도니까 "큐가 비면 호출 안 함" 이 필요했다
--    (하루 8,640 회). 여기는 **하루 1 회**라 아낄 게 없다. 반대로 가드를 잘못 쓰면
--    청구가 조용히 안 도는 실패가 생긴다 — 그 위험이 절약보다 크다.
-- 2. **Authorization 헤더를 안 붙인다.** 실측(2026-09-07): 헤더 없이 POST 해도 함수가
--    실행된다(503 은 게이트웨이가 아니라 우리 게이트의 응답이다). 이 엔드포인트의
--    보안 경계는 `X-Billing-Cron-Secret` 이므로, 필요 없는 service_role 키를 한 곳 더
--    복사해 두지 않는다.
--
-- ## 멱등하다 — 여러 번 불러도 안전하다
-- `charge_due_subscriptions` 가 `payment_history` 의 `charge_success` 마커
-- (`detail = period_key`)를 보고 이번 주기에 이미 결제된 구독을 건너뛴다. 그래서
-- 재시도·중복 실행이 **이중 청구로 이어지지 않는다.**
--
-- 적용 순서:
--   STEP 1 (사람) — Vault 에 secret · URL 저장
--   STEP 2 — 이 파일
--   STEP 3 — 검증
-- ============================================================================

-- ---------------------------------------------------------------------------
-- STEP 1 (사람이 SQL Editor 에서 1 회). **이 파일에 값을 적지 않는다.**
--
--   select vault.create_secret(
--     '<JETRAG_BILLING_CRON_SECRET 값 — Edge secret 과 같아야 한다>',
--     'billing_cron_secret',
--     'POST /billing/run 게이트. 마이그 029.'
--   );
--
--   select vault.create_secret(
--     'https://<project-ref>.supabase.co/functions/v1/api-payments/billing/run',
--     'billing_run_url',
--     'pg_cron 이 부를 결제 배치 엔드포인트. 마이그 029.'
--   );
--
-- 값이 바뀌면:
--   select vault.update_secret(
--     (select id from vault.secrets where name = 'billing_cron_secret'), '<새 값>');
--
-- **Edge 쪽 secret 과 값이 같아야 한다.** 다르면 매일 401 이 쌓인다(STEP 3-3 으로 확인).
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- STEP 2 — tick 함수
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.billing_run_tick()
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, vault, net
AS $$
DECLARE
  v_secret text;
  v_url    text;
  v_req    bigint;
BEGIN
  SELECT decrypted_secret INTO v_secret
    FROM vault.decrypted_secrets WHERE name = 'billing_cron_secret';
  SELECT decrypted_secret INTO v_url
    FROM vault.decrypted_secrets WHERE name = 'billing_run_url';

  -- 없으면 **조용히 넘어가지 않는다.** 조용하면 결제가 안 도는 이유를 못 찾는다.
  IF v_secret IS NULL OR v_url IS NULL THEN
    RAISE EXCEPTION
      'Vault 에 billing_cron_secret / billing_run_url 이 없다 (마이그 029 STEP 1)';
  END IF;

  SELECT net.http_post(
           url     := v_url,
           headers := jsonb_build_object(
                        'X-Billing-Cron-Secret', v_secret,
                        'Content-Type',          'application/json'),
           body    := '{}'::jsonb,
           -- 구독자 수만큼 KakaoPay 를 순차 호출한다. 건당 최대 15s 라 넉넉히 준다.
           timeout_milliseconds := 120000
         ) INTO v_req;

  RETURN v_req;
END;
$$;

COMMENT ON FUNCTION public.billing_run_tick() IS
  'pg_cron 이 하루 1회 부른다. api-payments/billing/run 을 호출해 만료 자동결제 + 7일 grace sweep 을 돌린다. 마이그 029.';

-- 권한: cron 은 postgres 로 돈다. 그 외에는 아무도 부를 수 없어야 한다 —
-- 이 함수는 Vault 의 결제 cron secret 을 읽고 **실제로 카드를 긁는 경로**를 깨운다.
REVOKE ALL ON FUNCTION public.billing_run_tick() FROM PUBLIC, anon, authenticated;

-- ---------------------------------------------------------------------------
-- STEP 2b — 스케줄
--
-- `0 18 * * *` UTC = **KST 새벽 3시**. W5-6 에서 Railway cron 에 쓰려던 값 그대로다
-- (work-log 2026-07-08 §3). 결제 주기는 월 1회지만 배치는 **매일** 돌아야 한다 —
-- `current_period_end` 가 아무 날짜나 될 수 있고, past_due 는 매일 재시도해야
-- 7일 grace 안에 회복할 기회가 생긴다.
--
-- 같은 이름으로 다시 걸면 중복되므로 먼저 지운다.
-- ---------------------------------------------------------------------------

SELECT cron.unschedule('billing-run')
 WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'billing-run');

SELECT cron.schedule('billing-run', '0 18 * * *', $cron$
  SELECT public.billing_run_tick();
$cron$);

-- ---------------------------------------------------------------------------
-- STEP 3 — 검증
--
-- 1) 잡이 걸렸는가
--    SELECT jobid, jobname, schedule, active FROM cron.job WHERE jobname = 'billing-run';
--    기대: 1행, schedule = '0 18 * * *', active = true
--
-- 2) 수동으로 한 번 (스케줄을 기다리지 않고)
--    SELECT public.billing_run_tick();
--    기대: bigint 요청 id. Vault 가 비었으면 여기서 EXCEPTION 이 난다
--
-- 3) 응답 확인 — **여기서 secret 이 맞는지 드러난다**
--    SELECT id, status_code, left(content, 300), created
--      FROM net._http_response ORDER BY created DESC LIMIT 3;
--
--    | status_code | 뜻 |
--    |---|---|
--    | 200 `{"charged":..,"failed":..,"canceled":..}` | 정상 |
--    | 503 `billing cron 이 비활성` | Edge 에 JETRAG_BILLING_CRON_SECRET 미설정 |
--    | 503 `결제 기능이 비활성` | Edge 에 KAKAOPAY_SECRET_KEY / BILLING_KEY_ENCRYPTION_KEY 미설정 |
--    | 401 `cron secret 불일치` | **Vault 값과 Edge secret 이 다르다** |
--
-- 4) 며칠 뒤 실행 이력
--    SELECT status, return_message, start_time
--      FROM cron.job_run_details
--     WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname = 'billing-run')
--     ORDER BY start_time DESC LIMIT 5;
--    기대: status = 'succeeded' (HTTP 상태와는 별개다 — 3) 을 같이 본다)
--
-- 5) 실제로 청구가 됐는지는 원장으로
--    SELECT event, count(*) FROM payment_history GROUP BY event;
--
-- 롤백:
--    SELECT cron.unschedule('billing-run');
--    DROP FUNCTION IF EXISTS public.billing_run_tick();
--    -- Vault 시크릿까지 지우려면:
--    -- DELETE FROM vault.secrets WHERE name IN ('billing_cron_secret','billing_run_url');
-- ---------------------------------------------------------------------------
