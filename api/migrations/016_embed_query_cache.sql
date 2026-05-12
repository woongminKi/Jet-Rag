-- ============================================================
-- 016_embed_query_cache.sql — S4-B 후속 (2026-05-12)
-- ============================================================
-- 배경
--   S4-B ablation 발견 — HF BGE-M3 Inference API 가 같은 query 텍스트에
--   미세하게 다른 dense 벡터를 반환 → dense_rank → RRF 재정렬 → 세션 간
--   top-10 순위 churn (집계 점수는 ±0.005 상쇄, 회귀 아님. 단 평가셋
--   측정 재현성 결함). + D6 의 HF Inference free-tier scale-to-zero
--   cold-start. → query 텍스트(sha256) → dense 벡터 영구 캐시 = 첫 fetch
--   벡터를 canonical 로 freeze → 워밍 후 측정 결정적 + HF 호출 0회.
--   현 in-process LRU 512 는 프로세스 종료 시 소실 → eval 매 실행마다 HF
--   재호출 → 비결정. (vision_page_cache / 마이그 015 선례 그대로.)
--
-- 설계
--   - text_sha256 + model_id 2-tuple UNIQUE — 정확히 같은 입력에만 hit
--   - text_sha256 = sha256(unicodedata.normalize("NFC", text.strip())) 64 hex
--     (PII 우려로 query 원문은 저장하지 않음 — vision_page_cache 와 동일 정책)
--   - model_id 'BAAI/bge-m3' — 모델 교체 시 자동 invalidate (현재는 고정)
--   - vector JSONB float array [v0, v1, ...] — 이 테이블에선 벡터 연산 안 함
--     (pgvector extension 의존 최소화. dense 검색은 chunks.embedding 컬럼에서)
--   - dim 1024 — read 시 len(vector) 검증용 (불일치 row 는 무시·HF 재호출)
--   - read/write 실패 시 graceful (app 측 _warn_first), write 는 best-effort
--
-- 적용 절차
--   supabase-jetrag MCP — list_migrations 로 다음 번호(016) 확인 후
--   apply_migration(name="016_embed_query_cache", query=<본 파일>).
--   (마이그 015 와 동일 방식. 마이그 파일은 repo 에도 보관 — DB↔repo 동기.)
--
-- 검증 SQL (적용 후)
--   INSERT INTO embed_query_cache (text_sha256, model_id, dim, vector)
--     VALUES (repeat('a', 64), 'BAAI/bge-m3', 3, '[0.1, 0.2, 0.3]'::jsonb)
--     ON CONFLICT (text_sha256, model_id) DO NOTHING;
--   SELECT id, text_sha256, model_id, dim, jsonb_array_length(vector) AS vec_len
--     FROM embed_query_cache WHERE text_sha256 = repeat('a', 64);
--   → 1 row, vec_len = 3.
--   DELETE FROM embed_query_cache WHERE text_sha256 = repeat('a', 64);
--
-- 캐시 비우는 법 (새 측정 baseline 필요 시)
--   DELETE FROM embed_query_cache;   -- 또는 model_id 별 부분 삭제
--   (+ 앱 ENV JETRAG_EMBED_QUERY_CACHE=0 으로 일시 우회 가능 — eval ablation)
-- ============================================================

BEGIN;

CREATE TABLE IF NOT EXISTS embed_query_cache (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    text_sha256   TEXT NOT NULL,            -- sha256(NFC(text.strip())), 64 hex
    model_id      TEXT NOT NULL,            -- 'BAAI/bge-m3'
    dim           INT  NOT NULL,            -- 1024
    vector        JSONB NOT NULL,           -- [v0, v1, ...] float array
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (text_sha256, model_id)
);

-- lookup 가속 — UNIQUE 가 자동 인덱스를 만들지만, 명시적 보조 인덱스로 의도 표현.
CREATE INDEX IF NOT EXISTS idx_embed_query_cache_lookup
    ON embed_query_cache (text_sha256, model_id);

COMMENT ON TABLE  embed_query_cache             IS 'BGE-M3 embed_query dense 벡터 영구 캐시 — 첫 fetch 벡터를 canonical 로 freeze (eval 재현성 + HF 호출 0)';
COMMENT ON COLUMN embed_query_cache.text_sha256 IS 'sha256(unicodedata.normalize("NFC", text.strip())) — PII 우려로 query 원문 미저장';
COMMENT ON COLUMN embed_query_cache.model_id    IS '임베딩 모델 ID (BAAI/bge-m3) — 모델 교체 시 자동 invalidate';
COMMENT ON COLUMN embed_query_cache.dim         IS '벡터 차원 (1024) — read 시 길이 검증용';
COMMENT ON COLUMN embed_query_cache.vector      IS 'dense 벡터 float array (JSONB) — 이 테이블에선 벡터 연산 없음';

-- ---------------- RLS ----------------
ALTER TABLE embed_query_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS embed_query_cache_service_role_all ON embed_query_cache;
CREATE POLICY embed_query_cache_service_role_all
    ON embed_query_cache
    FOR ALL
    TO service_role
    USING (TRUE)
    WITH CHECK (TRUE);

COMMIT;

-- ============================================================
-- 끝. 통합 코드 — embed_query_cache.py (lookup/upsert 헬퍼) +
--   bgem3_hf_embedding.embed_query 의 영구 캐시 레이어 (in-process LRU 아래 계층).
-- ============================================================
