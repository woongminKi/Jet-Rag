-- ============================================================
-- 022_plans_subscriptions.sql — 수익화 W3 (플랜·구독 + quota 기반)
-- ============================================================
-- 배경
--   W3 = Free/Pro 플랜 한도 정의 + 사용량 enforcement(402).
--   W5-6 카카오페이 정기결제가 subscriptions(status/billing_key/current_period_end)
--   를 그대로 재사용한다. 결제 전까지는 admin 수동 upsert 로 Pro 부여 (베타 체험).
--
-- 설계
--   - plans: code PK. 한도 숫자는 스펙 가안(오픈 이슈 #1) — UPDATE 만으로 조정 가능.
--     max_documents = 보유 문서 수 상한 (deleted_at IS NULL COUNT).
--     answers_per_day = 일일 답변 상한 (usage_counters 'answers' 재사용).
--     0 이하 = 해당 한도 무제한.
--   - subscriptions: user_id PK (유저당 1행). status active/past_due 만 유효 플랜.
--     past_due = 결제 실패 7일 grace (W5-6 상태 머신 예약). canceled/행 없음 = free.
--     billing_key 는 W5-6 카카오페이 SID 저장용 (지금은 NULL).
--
-- RLS
--   - plans: 가격표 = 공개 정보. authenticated/anon SELECT 허용.
--   - subscriptions: 본인 SELECT only (019 documents 패턴). 쓰기는 service_role 만.
--
-- 적용 절차
--   Supabase Studio → SQL Editor → New query 빈 탭 → 본 파일 paste → Run.
--
-- 검증 SQL (적용 후)
--   SELECT * FROM plans ORDER BY price_krw;                       -- free/pro 2행
--   INSERT INTO subscriptions (user_id, plan_code) VALUES
--     ('00000000-0000-0000-0000-00000000dead', 'pro');
--   SELECT * FROM subscriptions;                                  -- 1행, status=active
--   DELETE FROM subscriptions
--     WHERE user_id = '00000000-0000-0000-0000-00000000dead';     -- cleanup
-- ============================================================

CREATE TABLE IF NOT EXISTS plans (
    code            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    max_documents   INTEGER NOT NULL,
    answers_per_day INTEGER NOT NULL,
    price_krw       INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- seed — 스펙 가안 (2026-07-05 sprint 디자인). 확정 시 UPDATE 로 조정.
INSERT INTO plans (code, name, max_documents, answers_per_day, price_krw)
VALUES
    ('free', 'Free', 10, 5, 0),
    ('pro',  'Pro',  200, 50, 6900)
ON CONFLICT (code) DO NOTHING;

CREATE TABLE IF NOT EXISTS subscriptions (
    user_id            UUID PRIMARY KEY,
    plan_code          TEXT NOT NULL REFERENCES plans(code),
    status             TEXT NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'past_due', 'canceled')),
    current_period_end TIMESTAMPTZ,
    billing_key        TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------- RLS ----------------
ALTER TABLE plans ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS plans_select_all ON plans;
CREATE POLICY plans_select_all
    ON plans FOR SELECT
    TO authenticated, anon
    USING (TRUE);

DROP POLICY IF EXISTS plans_service_role_all ON plans;
CREATE POLICY plans_service_role_all
    ON plans FOR ALL
    TO service_role
    USING (TRUE) WITH CHECK (TRUE);

ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS subscriptions_select_own ON subscriptions;
CREATE POLICY subscriptions_select_own
    ON subscriptions FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS subscriptions_service_role_all ON subscriptions;
CREATE POLICY subscriptions_service_role_all
    ON subscriptions FOR ALL
    TO service_role
    USING (TRUE) WITH CHECK (TRUE);

-- ============================================================
-- 끝. Python 연동은 app/services/quota.py (Task 3).
-- ============================================================
