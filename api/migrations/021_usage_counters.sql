-- ============================================================
-- 021_usage_counters.sql — 수익화 W2 (per-user rate limit)
-- ============================================================
-- 배경
--   W2 = Gemini 유료 키 전환 + per-user rate limit. 익명 데모/로그인 사용자
--   모두에 일일 사용량 상한을 걸어 비용 폭주·남용을 방어한다.
--   W3-4 플랜/구독 미터링(usage enforcement, 402)이 본 테이블을 그대로 재사용.
--
-- 설계
--   - user_key TEXT — 로그인=user_id(uuid 문자열) / 익명=ip:<주소>. 두 형식 모두
--                     수용하려 TEXT (uuid 타입 아님).
--   - metric TEXT — 'answers' / 'docs'.
--   - period_date DATE — UTC 일 단위 버킷.
--   - count INTEGER — 해당 (키,metric,일) 누적 요청 수.
--   - PK (user_key, metric, period_date) — UPSERT 충돌 키.
--   - increment_usage_counter RPC — 원자적 +1 후 새 count 반환 (동시성 안전).
--
-- RLS
--   - service_role 만 (백엔드 전용). anon/authenticated 는 정책 부재로 차단.
--     (005 vision_usage_log 정책과 동일 패턴.)
--
-- 적용 절차
--   Supabase Studio → SQL Editor → 본 파일 paste → Run (단일 트랜잭션).
--
-- 검증 SQL (적용 후)
--   SELECT increment_usage_counter('ip:1.2.3.4', 'answers', CURRENT_DATE);  -- → 1
--   SELECT increment_usage_counter('ip:1.2.3.4', 'answers', CURRENT_DATE);  -- → 2
--   SELECT * FROM usage_counters WHERE user_key = 'ip:1.2.3.4';            -- → count=2
--   DELETE FROM usage_counters WHERE user_key = 'ip:1.2.3.4';             -- cleanup
-- ============================================================

CREATE TABLE IF NOT EXISTS usage_counters (
    user_key     TEXT NOT NULL,
    metric       TEXT NOT NULL,
    period_date  DATE NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_key, metric, period_date)
);

-- 날짜별 정리/조회용 (W3-4 미터링 리포트)
CREATE INDEX IF NOT EXISTS idx_usage_counters_period
    ON usage_counters (period_date);

-- ---------------- RLS ----------------
ALTER TABLE usage_counters ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS usage_counters_service_role_all ON usage_counters;
CREATE POLICY usage_counters_service_role_all
    ON usage_counters
    FOR ALL
    TO service_role
    USING (TRUE)
    WITH CHECK (TRUE);

-- ---------------- 원자적 증가 RPC ----------------
CREATE OR REPLACE FUNCTION increment_usage_counter(
    p_user_key   TEXT,
    p_metric     TEXT,
    p_period_date DATE
) RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    new_count INTEGER;
BEGIN
    INSERT INTO usage_counters (user_key, metric, period_date, count, updated_at)
    VALUES (p_user_key, p_metric, p_period_date, 1, now())
    ON CONFLICT (user_key, metric, period_date)
    DO UPDATE SET count = usage_counters.count + 1, updated_at = now()
    RETURNING count INTO new_count;
    RETURN new_count;
END;
$$;

-- 007 RPC 와 동일 — service_role 만 실행. anon/authenticated 차단.
REVOKE ALL ON FUNCTION increment_usage_counter(TEXT, TEXT, DATE) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION increment_usage_counter(TEXT, TEXT, DATE) TO service_role;

-- ============================================================
-- 끝. Python 연동은 Task 3 (app/services/rate_limit.py).
-- ============================================================
