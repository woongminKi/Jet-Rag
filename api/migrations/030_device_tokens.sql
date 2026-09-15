-- ============================================================
-- 030_device_tokens.sql — 기기 토큰 (자동 수집 ① 스펙 §4 S3)
-- ============================================================
-- 배경
--   PC 에이전트·아이폰 단축어·안드로이드 앱은 무인으로 돈다. 브라우저 세션(1시간 JWT +
--   회전 refresh)은 갱신을 놓치면 조용히 로그아웃된다. 긴 수명·기기 단위 폐기·좁은 스코프의
--   토큰이 필요하다.
--
-- 설계
--   - 토큰 원문은 저장하지 않는다. sha256 hex 만. 원문은 발급 응답에 한 번 실린다.
--   - token_prefix: 표시용 앞 8자(`jrd_xxxx`). 사용자가 어느 기기인지 알아보는 용도.
--   - scopes: 기본 {ingest} = POST /documents, POST /documents/precheck,
--     GET /documents/{id}/status, GET /documents/batch-status 만.
--   - revoked_at 이 채워지면 401. 행은 지우지 않는다(감사 흔적).
--
-- RLS
--   본인 행 SELECT 만. 쓰기는 service_role(Edge) 만.
--
-- 적용 절차
--   Supabase Studio → SQL Editor → 본 파일 paste → Run.
--
-- 검증 SQL
--   SELECT column_name FROM information_schema.columns WHERE table_name='device_tokens';
--   SELECT polname FROM pg_policy WHERE polrelid = 'device_tokens'::regclass;
-- ============================================================

CREATE TABLE IF NOT EXISTS device_tokens (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    token_hash    TEXT NOT NULL UNIQUE,
    token_prefix  TEXT NOT NULL,
    scopes        TEXT[] NOT NULL DEFAULT '{ingest}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_device_tokens_user ON device_tokens (user_id, created_at DESC);

ALTER TABLE device_tokens ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS device_tokens_select_own ON device_tokens;
CREATE POLICY device_tokens_select_own
    ON device_tokens FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS device_tokens_service_role_all ON device_tokens;
CREATE POLICY device_tokens_service_role_all
    ON device_tokens FOR ALL
    TO service_role
    USING (TRUE) WITH CHECK (TRUE);

-- ============================================================
-- 롤백
--   DROP TABLE IF EXISTS device_tokens;
-- ============================================================
