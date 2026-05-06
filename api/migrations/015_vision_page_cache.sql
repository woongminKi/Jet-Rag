-- ============================================================
-- 015_vision_page_cache.sql — Phase 1 S0 D1 (2026-05-06)
-- ============================================================
-- 배경
--   master plan §6/§7 — vision_enrich 가 reingest 마다 같은 페이지를 다시 호출 →
--   비용·latency 누적. PDF sha256 + page + prompt_version 단위 캐시 도입.
--
-- 설계
--   - sha256 + page + prompt_version 3-tuple UNIQUE — 정확히 같은 입력에만 hit
--   - prompt_version 변경 시 자동 invalidate (새 row 로 재진입)
--   - result JSONB — VisionCaption 4필드 (type/ocr_text/caption/structured) 직렬화
--   - estimated_cost 보존 — 캐시 hit 로 절감한 비용 사후 분석 가능
--
-- D1 범위
--   schema 만 도입. lookup 통합은 D2 (extract.py 변경 시).
--
-- 적용 절차
--   Supabase Studio → SQL Editor → 본 파일 paste → Run.
--
-- 검증 SQL (적용 후)
--   INSERT INTO vision_page_cache (sha256, page, prompt_version, result, estimated_cost)
--     VALUES (
--       'abc123' || repeat('0', 58), 1, 'v1',
--       '{"type":"문서","ocr_text":"","caption":"테스트","structured":null}',
--       0.00075
--     );
--   SELECT id, sha256, page, prompt_version, estimated_cost FROM vision_page_cache LIMIT 5;
--   → 1 row.
--   DELETE FROM vision_page_cache WHERE sha256 = 'abc123' || repeat('0', 58);
-- ============================================================

BEGIN;

CREATE TABLE IF NOT EXISTS vision_page_cache (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sha256          TEXT NOT NULL,
    page            INT  NOT NULL,
    prompt_version  TEXT NOT NULL,
    result          JSONB NOT NULL,
    estimated_cost  NUMERIC(10, 6),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (sha256, page, prompt_version)
);

-- lookup 가속 — UNIQUE 가 자동 인덱스 만들지만, prompt_version 무관 조회용 보조 인덱스
CREATE INDEX IF NOT EXISTS idx_vision_cache_lookup
    ON vision_page_cache (sha256, page);

COMMENT ON TABLE  vision_page_cache             IS 'PDF vision_enrich 페이지 단위 캐시 — reingest 시 호출 0회';
COMMENT ON COLUMN vision_page_cache.sha256          IS 'documents.sha256 와 매칭 (Storage 의 원본 PDF 해시)';
COMMENT ON COLUMN vision_page_cache.page            IS 'PDF 페이지 번호 (1-based)';
COMMENT ON COLUMN vision_page_cache.prompt_version  IS '프롬프트 버전 (변경 시 자동 invalidate)';
COMMENT ON COLUMN vision_page_cache.result          IS 'VisionCaption 4필드 직렬화 (type/ocr_text/caption/structured)';
COMMENT ON COLUMN vision_page_cache.estimated_cost  IS '원래 호출 시 추정 비용 (USD) — 절감 분석용';

-- ---------------- RLS ----------------
ALTER TABLE vision_page_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS vision_page_cache_service_role_all ON vision_page_cache;
CREATE POLICY vision_page_cache_service_role_all
    ON vision_page_cache
    FOR ALL
    TO service_role
    USING (TRUE)
    WITH CHECK (TRUE);

COMMIT;

-- ============================================================
-- 끝. 통합 코드는 D2 (_enrich_pdf_with_vision 가 lookup → miss 시 caption → upsert).
-- ============================================================
