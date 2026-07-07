-- ============================================================
-- 025_billing_subscription.sql — 수익화 W5-6 (카카오페이 정기결제)
-- ============================================================
-- 배경
--   마이그 022 subscriptions(user_id PK, plan_code, status, current_period_end,
--   billing_key, ...) 를 정기결제가 재사용한다. 본 마이그는 결제 flow 에 필요한
--   컬럼 2개를 추가하고, 결제 이력 테이블을 신설한다.
--
-- 추가 컬럼 (subscriptions)
--   - pending_tid  : ready→approve 사이 KakaoPay tid 보관 (approve 시 조회 후 clear).
--   - past_due_since: 결제 최초 실패 시각. 7일 grace sweep 판정 기준
--                     (updated_at 은 타 사유로도 갱신되어 모호 — 별도 컬럼).
--   - billing_key  : (기존) KakaoPay SID 의 Fernet 암호문. 평문 저장 금지.
--
-- payment_history
--   - 배치/승인/해지 이벤트 감사 로그. RLS 본인 SELECT + service_role full.
--
-- 적용 절차
--   Supabase Studio → SQL Editor → New query 빈 탭 → 본 파일 paste → Run.
--
-- 검증 SQL (적용 후)
--   SELECT column_name FROM information_schema.columns
--     WHERE table_name='subscriptions'
--       AND column_name IN ('pending_tid','past_due_since');   -- 2행
--   SELECT * FROM payment_history LIMIT 1;                     -- 빈 결과(에러 없음)
-- ============================================================

ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS pending_tid    TEXT,
    ADD COLUMN IF NOT EXISTS past_due_since TIMESTAMPTZ;

COMMENT ON COLUMN subscriptions.billing_key IS
    'KakaoPay SID(빌링키)의 Fernet 암호문. 평문 저장 금지 (app/services/billing_crypto.py).';

CREATE TABLE IF NOT EXISTS payment_history (
    id          BIGSERIAL PRIMARY KEY,
    user_id     UUID NOT NULL,
    event       TEXT NOT NULL
                CHECK (event IN ('subscribe', 'charge_success', 'charge_failed', 'cancel')),
    amount_krw  INTEGER,
    detail      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS payment_history_user_created_idx
    ON payment_history (user_id, created_at DESC);

ALTER TABLE payment_history ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS payment_history_select_own ON payment_history;
CREATE POLICY payment_history_select_own
    ON payment_history FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS payment_history_service_role_all ON payment_history;
CREATE POLICY payment_history_service_role_all
    ON payment_history FOR ALL
    TO service_role
    USING (TRUE) WITH CHECK (TRUE);

-- ============================================================
-- 끝. Python 연동은 app/services/billing.py (Task 7).
-- ============================================================
