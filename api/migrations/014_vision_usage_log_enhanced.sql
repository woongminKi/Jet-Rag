-- ============================================================
-- 014_vision_usage_log_enhanced.sql — Phase 1 S0 D1 (2026-05-06)
-- ============================================================
-- 배경
--   master plan §6/§7 (어댑터 인터페이스 정합성 + 비용 추적 보강).
--   기존 005 schema 는 success / error_msg / quota_exhausted / source_type 4개 시그널만.
--   Phase 1 부터 (1) doc_id/page 로 row 단위 추적 (2) prompt/output/thinking 토큰 분리
--   (3) estimated_cost 보존 (4) retry_attempt 별 분포 — 4축 신규 도입.
--
-- 호환성
--   ADD COLUMN IF NOT EXISTS — 기존 row 는 모두 NULL. record_call 의 새 인자는
--   default None 이므로 호출 코드 회귀 0.
--
-- 적용 절차
--   Supabase Studio → SQL Editor → 본 파일 paste → Run (단일 트랜잭션).
--
-- 검증 SQL (적용 후)
--   INSERT INTO vision_usage_log (
--       success, quota_exhausted, source_type,
--       doc_id, page, prompt_tokens, output_tokens, estimated_cost, model_used
--   ) VALUES (
--       TRUE, FALSE, 'pdf_vision_enrich',
--       NULL, 3, 850, 210, 0.000169, 'gemini-2.5-flash'
--   );
--   SELECT call_id, doc_id, page, prompt_tokens, output_tokens, estimated_cost, model_used
--     FROM vision_usage_log ORDER BY called_at DESC LIMIT 5;
--   → 추가 컬럼 모두 채워짐 확인 후 cleanup.
-- ============================================================

BEGIN;

ALTER TABLE vision_usage_log
    ADD COLUMN IF NOT EXISTS doc_id          UUID REFERENCES documents(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS page            INT,
    ADD COLUMN IF NOT EXISTS prompt_tokens   INT,
    ADD COLUMN IF NOT EXISTS image_tokens    INT,
    ADD COLUMN IF NOT EXISTS output_tokens   INT,
    ADD COLUMN IF NOT EXISTS thinking_tokens INT,
    ADD COLUMN IF NOT EXISTS retry_attempt   INT,
    ADD COLUMN IF NOT EXISTS estimated_cost  NUMERIC(10, 6),
    ADD COLUMN IF NOT EXISTS model_used      TEXT;

-- doc_id + page 조회 — 특정 문서의 페이지별 vision 비용 분석
CREATE INDEX IF NOT EXISTS idx_vision_usage_doc_page
    ON vision_usage_log (doc_id, page);

-- 시간 범위 + 비용 합산 — 일/주/월별 누적 추세 분석
-- (idx_vision_usage_log_called_at 005 가 이미 존재 — 중복 회피)
CREATE INDEX IF NOT EXISTS idx_vision_usage_created
    ON vision_usage_log (called_at);

COMMENT ON COLUMN vision_usage_log.doc_id IS 'documents 참조 — image_parser 단독 호출 시 NULL';
COMMENT ON COLUMN vision_usage_log.page IS 'PDF 페이지 번호 (1-based) — pdf_vision_enrich 만 채움';
COMMENT ON COLUMN vision_usage_log.prompt_tokens IS '입력 프롬프트 텍스트 토큰';
COMMENT ON COLUMN vision_usage_log.image_tokens IS '이미지 토큰 — Gemini SDK 가 별도 분리 안 하면 NULL';
COMMENT ON COLUMN vision_usage_log.output_tokens IS '응답 텍스트 토큰 (candidates_token_count)';
COMMENT ON COLUMN vision_usage_log.thinking_tokens IS 'Flash Thinking 모드 reasoning 토큰 (S3 보강 대비)';
COMMENT ON COLUMN vision_usage_log.retry_attempt IS '_gemini_common.with_retry 의 attempt 번호 (1=첫 시도)';
COMMENT ON COLUMN vision_usage_log.estimated_cost IS '단가 × 토큰 수 기반 추정 비용 (USD)';
COMMENT ON COLUMN vision_usage_log.model_used IS '실제 호출된 모델 ID (예: gemini-2.5-flash) — 추후 모델 변경 추적';

COMMIT;

-- ============================================================
-- 끝. record_call usage 인자 + GeminiVisionCaptioner usage_metadata 파싱 은
-- D1 Task #2 / Task #5 에서 다룸 (본 마이그는 schema 만).
-- ============================================================
