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
-- 적용 상태
--   **2026-09-07 운영에 적용 완료.** STEP 0~3 실행 + STEP 4 검증 전부 통과(실패 0).
--   재적용해도 안전하다(`IF NOT EXISTS` / `CREATE OR REPLACE`).
--
-- 재적용 절차
--   Supabase Studio → SQL Editor → New query 빈 탭 → 본 파일 paste → Run.
--   **STEP 0 을 먼저 단독 실행**해 pgmq 시그니처를 눈으로 확인할 것 — 버전이 올라가면
--   `send` 의 반환이나 `message_record` 컬럼이 바뀔 수 있다.
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

-- **2026-09-07 실측 결과** (아래 SELECT 로 확인함 — 가정 2 개가 틀려서 STEP 3 을 고쳤다):
--   pgmq.create(queue_name text)                                      → void
--   pgmq.send(queue_name text, msg jsonb, delay integer)              → **SETOF bigint** (스칼라 아님)
--   pgmq.read(queue_name text, vt int, qty int,
--             conditional jsonb DEFAULT '{}')                         → SETOF pgmq.message_record
--   pgmq.delete(queue_name text, msg_id bigint)                       → boolean
--   pgmq.archive(queue_name text, msg_id bigint)                      → boolean
--   pgmq.message_record = (msg_id bigint, read_ct integer, enqueued_at timestamptz,
--                          vt timestamptz, message jsonb, **headers jsonb**)  ← 6 컬럼
-- 처음엔 send 를 스칼라로, message_record 를 5 컬럼으로 가정했다. 그대로 갔으면 깨졌다.
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
  -- `pgmq.send` 는 **SETOF bigint** 다(실측). FROM 절에 두고 한 행만 꺼낸다.
  SELECT s FROM pgmq.send('ingest_tasks', payload, delay_seconds) AS s LIMIT 1;
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
  -- `message_record` 에는 `headers` 컬럼도 있지만(실측 6 컬럼) 인제스트는 안 쓴다.
  -- 4 번째 인자 `conditional` 은 기본값 `'{}'` 이 있어 생략 가능하다.
  SELECT r.msg_id, r.read_ct, r.enqueued_at, r.vt, r.message
    FROM pgmq.read('ingest_tasks', vt_seconds, qty) AS r;
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

-- **권한**: PUBLIC 만 회수하면 안 된다.
--
-- 처음엔 `REVOKE ... FROM PUBLIC` 만 했는데 **anon 이 그대로 호출됐다**(실측: `SET LOCAL
-- ROLE anon` 상태에서 send 가 msg_id 를 반환). Supabase 는 `public` 스키마의 새 함수에
-- `anon`/`authenticated` 로 **직접** EXECUTE 를 부여하는 default privileges 를 두고 있어,
-- PUBLIC 회수로는 그 부여분이 남는다. 롤을 명시해 회수한다.
REVOKE ALL ON FUNCTION public.ingest_queue_send(jsonb, integer)      FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.ingest_queue_read(integer, integer)    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.ingest_queue_delete(bigint)            FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.ingest_queue_archive(bigint)           FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.ingest_queue_depth()                   FROM PUBLIC, anon, authenticated;

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
-- 3) 래퍼 왕복 — 넣고, 읽고, 지운다  (**큐를 비운 상태에서** 할 것. FIFO 라
--    남은 메시지가 있으면 방금 넣은 게 아니라 가장 오래된 게 읽힌다)
--    SELECT public.ingest_queue_send('{"probe":true}'::jsonb);        -- msg_id 반환
--    SELECT * FROM public.ingest_queue_read(30, 1);                   -- 그 메시지가 보임
--    SELECT public.ingest_queue_delete(<위 msg_id>);                  -- true
--    SELECT * FROM public.ingest_queue_depth();                       -- 0
--
-- 4) 권한 — anon·authenticated 는 못 불러야 하고 service_role 은 돼야 한다
--    BEGIN; SET LOCAL ROLE anon;          SELECT public.ingest_queue_send('{}'::jsonb); ROLLBACK;
--    BEGIN; SET LOCAL ROLE authenticated; SELECT public.ingest_queue_send('{}'::jsonb); ROLLBACK;
--      → 둘 다 `permission denied for function ingest_queue_send` 여야 정상
--    BEGIN; SET LOCAL ROLE service_role;  SELECT public.ingest_queue_send('{}'::jsonb); ROLLBACK;
--      → 이건 **성공해야** 한다(대조군). 막히면 워커가 못 돈다.
--    ※ `SET LOCAL` 은 트랜잭션 안에서만 유효하다 — BEGIN 없이 쓰면 조용히 무시된다.
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
