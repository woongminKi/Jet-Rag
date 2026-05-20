-- ============================================================
-- 017_invite_codes.sql — D1 멀티유저 Auth 초대 코드 게이트 (2026-05-20)
-- ============================================================
-- 배경
--   D1 (plan §5, D1-Q5/Q9) — 공개 범위 = 초대 코드 lockdown → 베타 30명
--   안정화 → 일반 공개. 가입 직후 신규 user 의 JWT 로 백엔드가 코드를
--   검증 + 소진 (POST /auth/redeem-invite). 소진 이력 없는 user = 미승인.
--
-- 설계
--   - code TEXT PRIMARY KEY — 사람이 읽는 짧은 코드 (UUID 아님). 중복 자동 차단.
--   - used_by NULL = 미사용. 소진 시 user UUID + used_at 기록.
--   - expires_at NULL = 무기한. 값 있으면 now() 초과 시 거부.
--   - 소진은 조건부 UPDATE (used_by IS NULL) 로 race 방어 (plan §5).
--   - 부분 인덱스 (used_by IS NULL) — 미사용 코드 조회/redeem 핫패스 최적화.
--   - RLS ENABLE / 정책 없음 — service_role(백엔드)만 접근. 프론트 직접 접근 0
--     (001_init 의 4 테이블 + 패턴 동일). D2 에서 per-user 정책 별도 검토 불요
--     (초대 코드는 운영 전용 — 사용자 노출 0).
--
-- ROLLBACK:
--   DROP TABLE IF EXISTS invite_codes;
-- ============================================================

BEGIN;

CREATE TABLE IF NOT EXISTS invite_codes (
    code        TEXT PRIMARY KEY,
    issued_by   UUID,
    used_by     UUID,
    used_at     TIMESTAMPTZ,
    expires_at  TIMESTAMPTZ,
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 미사용 코드 조회/redeem 핫패스 — 부분 인덱스로 used 코드 제외.
CREATE INDEX IF NOT EXISTS idx_invite_codes_unused
    ON invite_codes (code) WHERE used_by IS NULL;

ALTER TABLE invite_codes ENABLE ROW LEVEL SECURITY;

COMMIT;

-- ============================================================
-- 베타 30개 seed (사용자가 별도 실행 — 코드는 임의로 교체할 것)
-- ------------------------------------------------------------
-- 아래 INSERT 는 의도적으로 주석 처리. 실 운영 시 code 값을 추측 불가능한
-- 랜덤 문자열로 교체한 뒤 SQL Editor 에서 한 번 실행한다. ON CONFLICT DO
-- NOTHING 으로 재실행 idempotent (이미 있는 code 는 skip).
--
-- 예시 — 30개 (BETA-XX 는 데모용 placeholder, 운영 전 반드시 교체):
-- ============================================================
-- INSERT INTO invite_codes (code, note) VALUES
--     ('BETA-01', '베타 1차'), ('BETA-02', '베타 1차'), ('BETA-03', '베타 1차'),
--     ('BETA-04', '베타 1차'), ('BETA-05', '베타 1차'), ('BETA-06', '베타 1차'),
--     ('BETA-07', '베타 1차'), ('BETA-08', '베타 1차'), ('BETA-09', '베타 1차'),
--     ('BETA-10', '베타 1차'), ('BETA-11', '베타 1차'), ('BETA-12', '베타 1차'),
--     ('BETA-13', '베타 1차'), ('BETA-14', '베타 1차'), ('BETA-15', '베타 1차'),
--     ('BETA-16', '베타 1차'), ('BETA-17', '베타 1차'), ('BETA-18', '베타 1차'),
--     ('BETA-19', '베타 1차'), ('BETA-20', '베타 1차'), ('BETA-21', '베타 1차'),
--     ('BETA-22', '베타 1차'), ('BETA-23', '베타 1차'), ('BETA-24', '베타 1차'),
--     ('BETA-25', '베타 1차'), ('BETA-26', '베타 1차'), ('BETA-27', '베타 1차'),
--     ('BETA-28', '베타 1차'), ('BETA-29', '베타 1차'), ('BETA-30', '베타 1차')
-- ON CONFLICT (code) DO NOTHING;
-- ============================================================
