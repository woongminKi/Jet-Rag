-- ============================================================
-- 026_pgmq_ingest_queue.sql — Phase 3 인제스트 큐 (pgmq + pg_cron + pg_net)
-- ============================================================
-- 배경
--   현행은 `upload_document` 가 202 를 주고 FastAPI `BackgroundTasks` 로 9단계를 한 번에
--   돌린다. Edge Functions 는 **요청당 CPU 2초**라 그 모델이 성립하지 않는다.
--   "작업 1건씩" 처리하는 큐 드리븐 상태 머신으로 바꾼다.
--
-- 실측 근거 (2026-09-07)
--   PDF 텍스트+span      페이지당 최대 100.8ms   (Phase 0 S2)
--   PDF vision 래스터화   페이지당 최악  443ms    (렌더 254 + PNG 173, 이번 세션 실측)
--   → vision 페이지는 요청당 1~2p, 텍스트만이면 10p 까지.
--
-- 확장 상태 (2026-09-07 실측)
--   설치됨   : pgroonga 3.2.5, vector 0.8.0, pgcrypto, uuid-ossp, supabase_vault 0.3.1 …
--   미설치   : pgmq, pg_cron, pg_net   ← 본 마이그가 설치한다
--   설치 가능: pgmq 1.5.1, pg_cron 1.6.4, pg_net 0.20.0
--
-- 왜 `public` 래퍼인가
--   PostgREST 가 노출하는 스키마는 `public`, `graphql_public` 뿐이다(실측: `pgmq_public.send`
--   호출 시 PGRST106). Edge Function 은 supabase-js → PostgREST 로 DB 를 쓰므로 `pgmq.*` 를
--   직접 못 부른다. 그래서 `public` 에 SECURITY DEFINER 래퍼를 두고 **service_role 에게만**
--   실행 권한을 준다. anon/authenticated 가 큐를 건드리면 안 된다.
--
-- 적용 절차
--   Supabase Studio → SQL Editor → New query 빈 탭 → 본 파일 paste → Run.
--   **STEP 0 을 먼저 단독 실행**해서 pgmq 함수 시그니처를 눈으로 확인할 것.
--   시그니처가 아래 가정과 다르면 STEP 3 래퍼를 그에 맞게 고쳐야 한다.
--
-- 되돌리기
--   파일 하단 롤백 SQL. 큐에 메시지가 남아 있으면 먼저 비운다.
-- ============================================================


-- ------------------------------------------------------------
-- STEP 0. 확장 설치 + **시그니처 확인** (여기까지 먼저 실행)
-- ------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgmq;
CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_net;

-- 아래 SELECT 의 출력을 **눈으로 확인**한다. 이 마이그는 다음을 가정한다:
--   pgmq.create(queue_name text)
--   pgmq.send(queue_name text, msg jsonb, delay integer)          → bigint
--   pgmq.read(queue_name text, vt integer, qty integer)           → setof record
--        (msg_id bigint, read_ct integer, enqueued_at timestamptz, vt timestamptz, message jsonb)
--   pgmq.delete(queue_name text, msg_id bigint)                   → boolean
--   pgmq.archive(queue_name text, msg_id bigint)                  → boolean
-- 다르면 STEP 3 을 고친 뒤 진행할 것. 짐작으로 넘어가지 말 것.
SELECT p.proname,
       pg_get_function_identity_arguments(p.oid) AS args,
       pg_get_function_result(p.oid)             AS returns
  FROM pg_proc p
  JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'pgmq'
   AND p.proname IN ('create','send','send_batch','read','pop','delete','archive','drop_queue')
 ORDER BY p.proname, args;


-- ------------------------------------------------------------
-- STEP 1. 큐 생성
-- ------------------------------------------------------------
-- 이름이 `ingest_tasks` 인 이유: 단위가 **문서가 아니라 작업**이다. 한 문서가 여러 메시지를
-- 만든다(예: PDF 40p → extract 작업 여러 건).
SELECT pgmq.create('ingest_tasks');


-- ------------------------------------------------------------
-- STEP 2. ingest_jobs 를 상태 머신으로 쓰기 위한 최소 보강
-- ------------------------------------------------------------
-- 기존 컬럼을 최대한 재사용한다(001_init + 010_stage_progress):
--   status(queued/running/completed/failed/cancelled) · current_stage · attempts
--   · stage_progress JSONB · queued_at/started_at/finished_at
--
-- 큐 모델에서 새로 필요한 것은 둘뿐이다.
ALTER TABLE ingest_jobs
  -- 이 잡이 아직 큐에 남긴 작업 수. 0 이 되면 잡 완료 판정.
  ADD COLUMN IF NOT EXISTS pending_tasks INTEGER NOT NULL DEFAULT 0,
  -- 마지막으로 워커가 건드린 시각. 고아 잡 sweep 의 기준(기존 started_at 은 최초 1회뿐).
  ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ;

-- 드레인이 "처리할 게 남은 잡" 을 싸게 찾도록.
CREATE INDEX IF NOT EXISTS idx_ingest_jobs_pending
    ON ingest_jobs (status, last_heartbeat_at)
    WHERE status = 'running';


-- ------------------------------------------------------------
-- STEP 3. public 래퍼 (Edge 가 supabase-js 로 부른다)
-- ------------------------------------------------------------
-- SECURITY DEFINER 인 이유: 호출자(service_role)에게 pgmq 스키마 권한을 직접 주지 않고,
-- 이 함수들만 통로로 남긴다. search_path 를 고정해 함수 하이재킹을 막는다.

CREATE OR REPLACE FUNCTION public.ingest_queue_send(payload jsonb, delay_seconds integer DEFAULT 0)
RETURNS bigint
LANGUAGE sql
SECURITY DEFINER
SET search_path = pgmq, public
AS $$
  SELECT pgmq.send('ingest_tasks', payload, delay_seconds);
$$;

CREATE OR REPLACE FUNCTION public.ingest_queue_read(vt_seconds integer, qty integer)
RETURNS TABLE (
  msg_id      bigint,
  read_ct     integer,
  enqueued_at timestamptz,
  vt          timestamptz,
  message     jsonb
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pgmq, public
AS $$
  SELECT msg_id, read_ct, enqueued_at, vt, message
    FROM pgmq.read('ingest_tasks', vt_seconds, qty);
$$;

CREATE OR REPLACE FUNCTION public.ingest_queue_delete(message_id bigint)
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = pgmq, public
AS $$
  SELECT pgmq.delete('ingest_tasks', message_id);
$$;

-- 재시도 한도를 넘긴 작업은 지우지 않고 **보관**한다. 사후 분석이 가능해야 한다.
CREATE OR REPLACE FUNCTION public.ingest_queue_archive(message_id bigint)
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = pgmq, public
AS $$
  SELECT pgmq.archive('ingest_tasks', message_id);
$$;

-- 큐 적체 관측용. 드레인 주기·동시성을 정할 근거가 된다.
CREATE OR REPLACE FUNCTION public.ingest_queue_depth()
RETURNS TABLE (queue_length bigint, oldest_age_seconds double precision)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pgmq, public
AS $$
  SELECT count(*)::bigint,
         COALESCE(EXTRACT(EPOCH FROM (now() - min(enqueued_at))), 0)::double precision
    FROM pgmq.q_ingest_tasks;
$$;

-- **권한**: 기본 PUBLIC 실행 권한을 걷어내고 service_role 에게만 준다.
REVOKE ALL ON FUNCTION public.ingest_queue_send(jsonb, integer)      FROM PUBLIC;
REVOKE ALL ON FUNCTION public.ingest_queue_read(integer, integer)    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.ingest_queue_delete(bigint)            FROM PUBLIC;
REVOKE ALL ON FUNCTION public.ingest_queue_archive(bigint)           FROM PUBLIC;
REVOKE ALL ON FUNCTION public.ingest_queue_depth()                   FROM PUBLIC;

GRANT EXECUTE ON FUNCTION public.ingest_queue_send(jsonb, integer)   TO service_role;
GRANT EXECUTE ON FUNCTION public.ingest_queue_read(integer, integer) TO service_role;
GRANT EXECUTE ON FUNCTION public.ingest_queue_delete(bigint)         TO service_role;
GRANT EXECUTE ON FUNCTION public.ingest_queue_archive(bigint)        TO service_role;
GRANT EXECUTE ON FUNCTION public.ingest_queue_depth()                TO service_role;


-- ------------------------------------------------------------
-- STEP 4. 검증 (적용 후 실행 — 전부 통과해야 한다)
-- ------------------------------------------------------------
-- 1) 확장 3종
--    SELECT extname, extversion FROM pg_extension
--     WHERE extname IN ('pgmq','pg_cron','pg_net') ORDER BY 1;
--    기대: pg_cron, pg_net, pgmq 3행
--
-- 2) 큐 테이블
--    SELECT to_regclass('pgmq.q_ingest_tasks'), to_regclass('pgmq.a_ingest_tasks');
--    기대: 둘 다 NOT NULL
--
-- 3) 래퍼 왕복 — 넣고, 읽고, 지운다
--    SELECT public.ingest_queue_send('{"probe":true}'::jsonb);        -- msg_id 반환
--    SELECT * FROM public.ingest_queue_read(30, 1);                   -- 그 메시지가 보임
--    SELECT public.ingest_queue_delete(<위 msg_id>);                  -- true
--    SELECT * FROM public.ingest_queue_depth();                       -- 0
--
-- 4) 권한 — anon 은 못 불러야 한다
--    SET ROLE anon;
--    SELECT public.ingest_queue_send('{}'::jsonb);   -- permission denied 여야 정상
--    RESET ROLE;
--
-- 5) ingest_jobs 컬럼
--    SELECT column_name FROM information_schema.columns
--     WHERE table_name='ingest_jobs' AND column_name IN ('pending_tasks','last_heartbeat_at');
--    기대: 2행


-- ------------------------------------------------------------
-- 롤백 (필요 시)
-- ------------------------------------------------------------
-- DROP FUNCTION IF EXISTS public.ingest_queue_send(jsonb, integer);
-- DROP FUNCTION IF EXISTS public.ingest_queue_read(integer, integer);
-- DROP FUNCTION IF EXISTS public.ingest_queue_delete(bigint);
-- DROP FUNCTION IF EXISTS public.ingest_queue_archive(bigint);
-- DROP FUNCTION IF EXISTS public.ingest_queue_depth();
-- DROP INDEX IF EXISTS idx_ingest_jobs_pending;
-- ALTER TABLE ingest_jobs DROP COLUMN IF EXISTS pending_tasks;
-- ALTER TABLE ingest_jobs DROP COLUMN IF EXISTS last_heartbeat_at;
-- SELECT pgmq.drop_queue('ingest_tasks');     -- 메시지가 남아 있으면 먼저 비울 것
-- -- 확장은 다른 곳에서 쓸 수 있으므로 기본적으로 남긴다.
-- -- DROP EXTENSION IF EXISTS pg_net;  DROP EXTENSION IF EXISTS pg_cron;  DROP EXTENSION IF EXISTS pgmq;


-- ------------------------------------------------------------
-- 다음 마이그(027)로 미룬 것 — 의도적이다
-- ------------------------------------------------------------
--   * pg_cron 드레인 스케줄 + pg_net 으로 Edge `ingest-worker` 호출
--     → 워커가 아직 없다. 없는 엔드포인트를 부르는 cron 을 먼저 만들면 실패 로그만 쌓인다.
--   * service_role 키를 Vault 에 넣고 cron 이 읽게 하기
--     → SQL 에 키를 평문으로 박지 않기 위해서다. 드레인과 함께 들어가야 의미가 있다.
--   * 고아 잡 sweep cron (`last_heartbeat_at` 기준)
--     → 워커의 heartbeat 주기를 실측한 뒤 임계를 정한다.
