-- ============================================================
-- 005_vision_usage_log.sql — W15 Day 2 (한계 #34·#62)
-- ============================================================
-- 배경
--   W8 Day 4 vision_metrics 모듈이 in-memory counter 만 유지 — 프로세스 재시작 시 휘발.
--   W11 Day 1 last_quota_exhausted_at 추가 후에도 휘발성 trade-off 동일.
--   본 마이그레이션은 호출 1건당 row 1건 영구 저장 → 장기 추세 추적 + 재시작 회복.
--
-- 설계
--   - call_id BIGSERIAL — 단순 단조 증가 PK
--   - called_at TIMESTAMPTZ — UTC, default now()
--   - success BOOLEAN — Vision API 호출 결과 (caption 성공/raise)
--   - error_msg TEXT NULL — raise 시 Exception str (디버깅용, 200자 제한 권장)
--   - quota_exhausted BOOLEAN — is_quota_exhausted (W9 Day 7 3 단계 매트릭스) 결과
--   - source_type TEXT NULL — 'image' / 'pdf_scan' / 'pptx_rerouting' / 'pptx_augment'
--                              (호출 컨텍스트 — 디버깅용, NULL 허용)
--
-- 인덱스
--   - PK call_id 자동 인덱스
--   - idx_vision_usage_log_called_at — 최근 N건 / 시간 범위 조회 (DESC)
--   - idx_vision_usage_log_quota — quota 초과 시점 빠른 조회 (partial)
--
-- RLS
--   - service_role 만 쓰기 (백엔드 전용) — anon / authenticated 차단
--   - 단일 사용자 MVP — user_id 컬럼 미도입 (W2 §3.M user_id 정책과 일관, 필요 시 W16+ 추가)
--
-- 적용 절차
--   Supabase Studio → SQL Editor → 본 파일 paste → Run (단일 트랜잭션).
--
-- 검증 SQL (적용 후)
--   INSERT INTO vision_usage_log (success, quota_exhausted, source_type)
--     VALUES (TRUE, FALSE, 'image');
--   SELECT * FROM vision_usage_log ORDER BY called_at DESC LIMIT 5;
--   → 1 row (방금 insert)
--
--   DELETE FROM vision_usage_log WHERE source_type = 'image';
--   → cleanup
-- ============================================================

CREATE TABLE IF NOT EXISTS vision_usage_log (
    call_id          BIGSERIAL PRIMARY KEY,
    called_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    success          BOOLEAN NOT NULL,
    error_msg        TEXT,
    quota_exhausted  BOOLEAN NOT NULL DEFAULT FALSE,
    source_type      TEXT
);

-- 최근 N건 / 시간 범위 — 일반 stats 조회 path
CREATE INDEX IF NOT EXISTS idx_vision_usage_log_called_at
    ON vision_usage_log (called_at DESC);

-- quota 초과 시점만 빠르게 — partial index (대부분 row 는 quota_exhausted=FALSE)
CREATE INDEX IF NOT EXISTS idx_vision_usage_log_quota
    ON vision_usage_log (called_at DESC)
    WHERE quota_exhausted = TRUE;

-- ---------------- RLS ----------------
ALTER TABLE vision_usage_log ENABLE ROW LEVEL SECURITY;

-- service_role 은 SECURITY DEFINER 의 묵시적 bypass 로 동작하나, 명시적 정책으로 의도 분명.
-- anon / authenticated 는 정책 부재로 자동 차단 (RLS enabled + no policy = deny all).
DROP POLICY IF EXISTS vision_usage_log_service_role_all ON vision_usage_log;
CREATE POLICY vision_usage_log_service_role_all
    ON vision_usage_log
    FOR ALL
    TO service_role
    USING (TRUE)
    WITH CHECK (TRUE);

-- ============================================================
-- 끝. Python write-through 는 W15 Day 3+ (vision_metrics 모듈 갱신).
-- ============================================================
