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
--   **정책이 "없다"는 것이 방어다.** `authenticated` 롤에 INSERT/UPDATE/DELETE 정책을
--   하나도 안 만든 것이 의도다 — RLS 가 켜진 테이블에서 해당 명령의 정책이 없으면 전부
--   거절되므로, anon key 를 쥔 브라우저는 토큰을 스스로 만들거나(무한 발급) 남의 행을
--   폐기 해제할 수 없다. `service_role` 정책은 형식상 둔 것이고 실제로는 RLS 를 우회한다.
--   따라서 발급·폐기의 소유자 검사는 Edge 코드(`_shared/me/devices.ts` 의 user_id 필터)가
--   유일한 방어선이다 — 거기서 `.eq("user_id", ...)` 를 빼면 즉시 뚫린다.
--   SELECT 정책조차 `token_hash` 를 가려주지 않는다(컬럼 단위 권한이 아니다). 다만 해시라
--   원문 복원이 불가능하다는 것이 근거다.
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
