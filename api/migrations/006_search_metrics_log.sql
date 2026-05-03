-- ============================================================
-- 006_search_metrics_log.sql — W15 Day 2 (한계 #61·#76·#81)
-- ============================================================
-- 배경
--   W3 Day 2 Phase 3 search_metrics 의 ring buffer (in-memory, 최근 500건) 가
--   프로세스 재시작 시 휘발 — W4-Q-16 부터 누적 추천된 DB 영속화 회수.
--   W14 Day 3 by_mode 분리 측정 ship 후 ablation 장기 추세 추적이 더 가치 있음.
--   W14 Day 2 monitor-search-slo workflow 가 in-memory snapshot artifact 30일 보관 한계 (#81).
--
-- 설계
--   - record_id BIGSERIAL PK
--   - recorded_at TIMESTAMPTZ — UTC, default now()
--   - took_ms INT — 검색 1회 처리 시간 (in-memory 와 동일)
--   - dense_hits / sparse_hits / fused INT — RPC 응답 카운트
--   - has_dense BOOLEAN — dense path 진입 여부
--   - fallback_reason TEXT NULL — transient_5xx / permanent_4xx / NULL (정상)
--   - embed_cache_hit BOOLEAN — W4-Q-3 LRU 결과
--   - mode TEXT — hybrid / dense / sparse (W14 Day 3 ablation)
--   - query_text TEXT — 단일 사용자 MVP, PII 위험 0 (검색어 자연어)
--                        멀티 사용자 도입 시 query_hash (MD5/SHA256) 로 변경 검토 (DE-21)
--
-- 인덱스
--   - PK record_id
--   - idx_search_metrics_log_recorded_at DESC — 최근 N건
--   - idx_search_metrics_log_mode — mode 별 ablation 비교 (composite with recorded_at)
--   - idx_search_metrics_log_fallback — fallback 발생 시점 빠른 조회 (partial)
--
-- RLS
--   - service_role only — 005 와 동일 정책
--
-- 적용 절차
--   Supabase Studio → SQL Editor → 본 파일 paste → Run.
--
-- 검증 SQL (적용 후)
--   INSERT INTO search_metrics_log
--     (took_ms, dense_hits, sparse_hits, fused, has_dense, mode, query_text)
--     VALUES (150, 10, 5, 10, TRUE, 'hybrid', 'test');
--   SELECT * FROM search_metrics_log ORDER BY recorded_at DESC LIMIT 5;
--   → 1 row (방금 insert)
--
--   DELETE FROM search_metrics_log WHERE query_text = 'test';
--   → cleanup
-- ============================================================

CREATE TABLE IF NOT EXISTS search_metrics_log (
    record_id          BIGSERIAL PRIMARY KEY,
    recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    took_ms            INT NOT NULL,
    dense_hits         INT NOT NULL DEFAULT 0,
    sparse_hits        INT NOT NULL DEFAULT 0,
    fused              INT NOT NULL DEFAULT 0,
    has_dense          BOOLEAN NOT NULL,
    fallback_reason    TEXT,
    embed_cache_hit    BOOLEAN NOT NULL DEFAULT FALSE,
    mode               TEXT NOT NULL DEFAULT 'hybrid'
                       CHECK (mode IN ('hybrid', 'dense', 'sparse')),
    query_text         TEXT
);

-- 최근 N건 / 시간 범위
CREATE INDEX IF NOT EXISTS idx_search_metrics_log_recorded_at
    ON search_metrics_log (recorded_at DESC);

-- mode 별 비교 — recorded_at 와 composite (mode 내 정렬도 유지)
CREATE INDEX IF NOT EXISTS idx_search_metrics_log_mode_recorded
    ON search_metrics_log (mode, recorded_at DESC);

-- fallback 발생 시점만 — partial (대부분 NULL)
CREATE INDEX IF NOT EXISTS idx_search_metrics_log_fallback
    ON search_metrics_log (recorded_at DESC)
    WHERE fallback_reason IS NOT NULL;

-- ---------------- RLS ----------------
ALTER TABLE search_metrics_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS search_metrics_log_service_role_all ON search_metrics_log;
CREATE POLICY search_metrics_log_service_role_all
    ON search_metrics_log
    FOR ALL
    TO service_role
    USING (TRUE)
    WITH CHECK (TRUE);

-- ============================================================
-- 끝. Python write-through 는 W15 Day 3+ (search_metrics 모듈 갱신).
-- ============================================================
