-- ============================================================
-- 031_metering_v2.sql — 계량 교체 (자동 수집 ① 스펙 §4 S4)
-- ============================================================
-- 배경
--   문서 수 상한(Free 10·Pro 200)과 일일 30건은 손으로 올리는 전제다. 자동 수집은 첫 동기화에서
--   바로 막힌다. 비용을 만드는 건 문서 수가 아니라 저장 용량과 Vision 페이지(페이지당 $0.005~0.03
--   실측)다. 그 둘만 센다.
--
-- 바뀌는 것
--   1. plans: storage_bytes_limit · vision_pages_per_month 추가, max_documents 제거.
--      값은 **잠정**(스펙 §4 S4). 출시 전 Vision 단가 실측으로 재산정.
--   2. documents.source_channel CHECK 에 pc-agent · ios-shortcut · android-agent.
--   3. ingest_jobs.status 에 'deferred_quota' + deferred_task jsonb (재투입용 페이로드).
--   4. RPC storage_bytes_used(uuid) · vision_pages_used_since(uuid, timestamptz).
--   5. upload_burst 표 + increment_upload_burst — 분당 업로드 남용 방지.
--   6. vision_quota_release() + cron 'vision-quota-release' 매일 00:00 KST(=15:00 UTC):
--      deferred_quota 잡을 큐에 되돌린다. 아직 한도 초과면 워커 게이트가 다시 보류한다(싸다).
--
-- 적용 절차: Supabase Studio → SQL Editor → paste → Run.
-- 검증 SQL:
--   SELECT code, storage_bytes_limit, vision_pages_per_month FROM plans;
--   SELECT public.storage_bytes_used('<owner uuid>');
--   SELECT public.vision_pages_used_since('<owner uuid>', date_trunc('month', now()));
--   SELECT jobname, schedule FROM cron.job WHERE jobname = 'vision-quota-release';
-- ============================================================

-- 1. plans
ALTER TABLE plans
    ADD COLUMN IF NOT EXISTS storage_bytes_limit    BIGINT  NOT NULL DEFAULT 1073741824,  -- 1GB
    ADD COLUMN IF NOT EXISTS vision_pages_per_month INTEGER NOT NULL DEFAULT 100;
UPDATE plans SET storage_bytes_limit = 1073741824,  vision_pages_per_month = 100  WHERE code = 'free';
UPDATE plans SET storage_bytes_limit = 10737418240, vision_pages_per_month = 1000 WHERE code = 'pro';
ALTER TABLE plans DROP COLUMN IF EXISTS max_documents;

-- 2. source_channel
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_source_channel_check;
ALTER TABLE documents ADD CONSTRAINT documents_source_channel_check CHECK (source_channel IN
    ('drag-drop','os-share','clipboard','url','camera','api','email','pc-agent','ios-shortcut','android-agent'));

-- 3. ingest_jobs
ALTER TABLE ingest_jobs DROP CONSTRAINT IF EXISTS ingest_jobs_status_check;
ALTER TABLE ingest_jobs ADD CONSTRAINT ingest_jobs_status_check CHECK (status IN
    ('queued','running','completed','failed','cancelled','deferred_quota'));
ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS deferred_task JSONB;

-- 4. 사용량 RPC (service_role 전용)
CREATE OR REPLACE FUNCTION public.storage_bytes_used(p_user_id UUID)
RETURNS BIGINT LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE(SUM(size_bytes), 0)::BIGINT FROM documents
   WHERE user_id = p_user_id AND deleted_at IS NULL;
$$;

CREATE OR REPLACE FUNCTION public.vision_pages_used_since(p_user_id UUID, p_since TIMESTAMPTZ)
RETURNS INTEGER LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COUNT(*)::INTEGER FROM vision_usage_log v
    JOIN documents d ON d.id = v.doc_id
   WHERE d.user_id = p_user_id AND v.success = TRUE AND v.called_at >= p_since;
$$;

REVOKE ALL ON FUNCTION public.storage_bytes_used(UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.vision_pages_used_since(UUID, TIMESTAMPTZ) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.storage_bytes_used(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.vision_pages_used_since(UUID, TIMESTAMPTZ) TO service_role;

-- 5. 분당 업로드 남용 방지
CREATE TABLE IF NOT EXISTS upload_burst (
    user_key  TEXT NOT NULL,
    minute    TIMESTAMPTZ NOT NULL,
    count     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_key, minute)
);
ALTER TABLE upload_burst ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS upload_burst_service_role_all ON upload_burst;
CREATE POLICY upload_burst_service_role_all ON upload_burst FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

CREATE OR REPLACE FUNCTION public.increment_upload_burst(p_user_key TEXT, p_minute TIMESTAMPTZ)
RETURNS INTEGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE new_count INTEGER;
BEGIN
  INSERT INTO upload_burst (user_key, minute, count) VALUES (p_user_key, p_minute, 1)
  ON CONFLICT (user_key, minute) DO UPDATE SET count = upload_burst.count + 1
  RETURNING count INTO new_count;
  RETURN new_count;
END; $$;
REVOKE ALL ON FUNCTION public.increment_upload_burst(TEXT, TIMESTAMPTZ) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.increment_upload_burst(TEXT, TIMESTAMPTZ) TO service_role;

-- 6. 보류 잡 재투입 + 하루치 burst 정리
CREATE OR REPLACE FUNCTION public.vision_quota_release()
RETURNS INTEGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r RECORD; n INTEGER := 0;
BEGIN
  FOR r IN SELECT id, deferred_task FROM ingest_jobs
            WHERE status = 'deferred_quota' AND deferred_task IS NOT NULL LOOP
    PERFORM public.ingest_queue_send(r.deferred_task, 0);
    UPDATE ingest_jobs SET status = 'queued', deferred_task = NULL, error_msg = NULL WHERE id = r.id;
    n := n + 1;
  END LOOP;
  DELETE FROM upload_burst WHERE minute < now() - interval '1 day';
  RETURN n;
END; $$;
REVOKE ALL ON FUNCTION public.vision_quota_release() FROM PUBLIC, anon, authenticated;

SELECT cron.unschedule('vision-quota-release')
 WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'vision-quota-release');
SELECT cron.schedule('vision-quota-release', '0 15 * * *', $cron$
  SELECT public.vision_quota_release();
$cron$);

-- ============================================================
-- 롤백
--   SELECT cron.unschedule('vision-quota-release');
--   DROP FUNCTION IF EXISTS public.vision_quota_release(), public.increment_upload_burst(TEXT, TIMESTAMPTZ),
--     public.vision_pages_used_since(UUID, TIMESTAMPTZ), public.storage_bytes_used(UUID);
--   DROP TABLE IF EXISTS upload_burst;
--   ALTER TABLE ingest_jobs DROP COLUMN IF EXISTS deferred_task;  (status CHECK 는 001 값으로 되돌림)
--   ALTER TABLE plans ADD COLUMN max_documents INTEGER NOT NULL DEFAULT 10; UPDATE plans SET max_documents = 200 WHERE code='pro';
--   ALTER TABLE plans DROP COLUMN storage_bytes_limit, DROP COLUMN vision_pages_per_month;
-- ============================================================
