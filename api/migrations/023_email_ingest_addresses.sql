-- ============================================================
-- 023_email_ingest_addresses.sql — 수익화 W4 (이메일 인제스트)
-- ============================================================
-- 배경
--   Pro 유저 전용 이메일 인제스트 — u-{token}@in.woong-s.com 수신 주소를
--   user_id 에 매핑한다. 베타 피드백 1순위(업로드 마찰) 해소.
--
-- 설계
--   - user_id PK — 유저당 주소 1개.
--   - token UNIQUE — 8자리 소문자 영숫자 (URL·이메일 안전). 유출/스팸 시
--     rotate 로 재발급 (rotated_at 갱신, 구 토큰 즉시 무효).
--   - owner_email — 주소 발급 시점의 가입 이메일 (JWT claim). webhook 이
--     발신자(From) 화이트리스트 비교에 사용 — admin API 조회 불필요.
--
-- RLS
--   - service_role only (백엔드 전용 — 프론트는 GET /me/email-ingest 경유).
--     (021 usage_counters 와 동일 패턴.)
--
-- 적용 절차
--   Supabase Studio → SQL Editor → New query 빈 탭 → paste → Run.
--
-- 검증 SQL (적용 후)
--   INSERT INTO email_ingest_addresses (user_id, token, owner_email)
--   VALUES ('00000000-0000-0000-0000-00000000dead', 'abc12345', 'x@y.z');
--   SELECT * FROM email_ingest_addresses WHERE token = 'abc12345';   -- 1행
--   DELETE FROM email_ingest_addresses
--     WHERE user_id = '00000000-0000-0000-0000-00000000dead';        -- cleanup
-- ============================================================

CREATE TABLE IF NOT EXISTS email_ingest_addresses (
    user_id     UUID PRIMARY KEY,
    token       TEXT NOT NULL UNIQUE,
    owner_email TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at  TIMESTAMPTZ
);

ALTER TABLE email_ingest_addresses ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS email_ingest_addresses_service_role_all ON email_ingest_addresses;
CREATE POLICY email_ingest_addresses_service_role_all
    ON email_ingest_addresses
    FOR ALL
    TO service_role
    USING (TRUE) WITH CHECK (TRUE);

-- ============================================================
-- 끝. Python 연동은 app/services/email_ingest.py (Task 3).
-- ============================================================
