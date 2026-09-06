-- ============================================================
-- 027_ingest_artifacts.sql — Phase 3 단계 간 중간 산출물 + 보관 개수 래퍼
-- ============================================================
-- 왜 필요한가
--   `extract` 를 페이지 단위로 쪼개면 그 결과를 **어딘가 모아 뒀다가** 청킹해야 한다.
--   청킹(`chunk.py:96`)이 `_merge_short_sections` 로 **인접 섹션을 병합**하기 때문에,
--   페이지별로 따로 청킹하면 경계에서 병합이 안 일어나 청크가 달라진다.
--   → "페이지 추출 → 전부 모아 청킹" 이어야 하고, 중간 텍스트를 둘 자리가 필요하다.
--
-- 저장 위치를 셋 중에서 골랐다
--   1) **새 테이블(채택)** — `doc_id` CASCADE 로 정리가 자동이고(샌드박스 하네스가 이미
--      그 체인을 따라간다), 작업 완료와 산출물 저장이 한 트랜잭션에 들어간다. 크기가
--      문제되면 나중에 payload 를 Storage 참조로 바꿔도 스키마는 그대로다.
--   2) Storage 경로 — 크기 무제한이지만 정리가 수동이다.
--   3) `ingest_jobs.stage_progress` — **부적절.** 실시간 진행 표시용이고 Realtime push
--      가 걸려 있어(마이그 009) 큰 페이로드를 넣으면 갱신마다 프론트로 밀린다.
--
-- 저장 대상 (`ExtractionResult`, `adapters/parser.py:19`)
--   source_type · sections[{text,page,section_title,bbox,metadata}] · raw_text
--   · warnings[] · metadata{}
--
-- 적용 절차 / 되돌리기: 파일 하단.
-- ============================================================


-- ------------------------------------------------------------
-- STEP 1. 중간 산출물 테이블
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingest_artifacts (
    id         BIGSERIAL PRIMARY KEY,
    job_id     UUID NOT NULL REFERENCES ingest_jobs(id) ON DELETE CASCADE,
    -- `doc_id` 도 함께 둔다. 잡이 지워져도 문서 기준으로 찾을 수 있어야 하고,
    -- 샌드박스 정리 하네스가 `doc_id` 를 출발점으로 삼는다.
    doc_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    stage      TEXT NOT NULL,
    -- 페이지·배치 순번. 모아서 이어붙일 때 **이 순서가 곧 문서 순서**다.
    seq        INTEGER NOT NULL,
    payload    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 같은 작업이 두 번 배달돼도(vt 만료 재배달) 행이 두 개가 되면 안 된다.
    -- 워커는 이 제약에 기대 upsert 한다 — **멱등성의 근거가 여기 있다.**
    UNIQUE (job_id, stage, seq)
);

-- 모아 읽기: `WHERE job_id=? AND stage=? ORDER BY seq`
CREATE INDEX IF NOT EXISTS idx_ingest_artifacts_job
    ON ingest_artifacts (job_id, stage, seq);
CREATE INDEX IF NOT EXISTS idx_ingest_artifacts_doc
    ON ingest_artifacts (doc_id);

-- RLS: 사용자 데이터가 아니라 **파이프라인 내부 산출물**이다. 클라이언트가 볼 이유가 없다.
ALTER TABLE ingest_artifacts ENABLE ROW LEVEL SECURITY;
-- 정책을 하나도 만들지 않는다 → anon/authenticated 는 아무 것도 못 본다.
-- service_role 은 RLS 를 우회하므로 워커는 그대로 읽고 쓴다.
REVOKE ALL ON TABLE ingest_artifacts FROM PUBLIC, anon, authenticated;
GRANT ALL  ON TABLE ingest_artifacts TO service_role;
GRANT USAGE, SELECT ON SEQUENCE ingest_artifacts_id_seq TO service_role;


-- ------------------------------------------------------------
-- STEP 2. 보관 개수 래퍼 — 026 에서 미검증으로 남긴 것
-- ------------------------------------------------------------
-- `pgmq.a_ingest_tasks` 는 PostgREST 에 안 보인다. 그래서 워커가 "보관했다" 고 보고해도
-- **독립 신호로 확인할 방법이 없었다**(2026-09-07). depth 처럼 래퍼를 둬서 대조를 닫는다.
CREATE OR REPLACE FUNCTION public.ingest_queue_archived_count()
RETURNS bigint
LANGUAGE sql
SECURITY DEFINER
SET search_path = pgmq, public
AS $$
  SELECT count(*)::bigint FROM pgmq.a_ingest_tasks;
$$;

-- 026 에서 배운 것: **PUBLIC 만 회수하면 anon 이 그대로 통과한다.**
-- Supabase 가 `public` 스키마 새 함수에 anon/authenticated 로 직접 EXECUTE 를 부여한다.
REVOKE ALL ON FUNCTION public.ingest_queue_archived_count() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.ingest_queue_archived_count() TO service_role;


-- ------------------------------------------------------------
-- STEP 3. 검증 (적용 후)
-- ------------------------------------------------------------
-- 1) 테이블·제약
--    SELECT to_regclass('public.ingest_artifacts');                       -- NOT NULL
--    SELECT conname FROM pg_constraint
--     WHERE conrelid='ingest_artifacts'::regclass AND contype='u';        -- UNIQUE 1건
--
-- 2) RLS 가 켜져 있고 정책이 없다
--    SELECT relrowsecurity FROM pg_class WHERE relname='ingest_artifacts';  -- t
--    SELECT count(*) FROM pg_policies WHERE tablename='ingest_artifacts';   -- 0
--
-- 3) 권한 — anon 은 못 읽어야, service_role 은 돼야
--    BEGIN; SET LOCAL ROLE anon;         SELECT count(*) FROM ingest_artifacts; ROLLBACK;
--      → permission denied 여야 정상
--    BEGIN; SET LOCAL ROLE service_role; SELECT count(*) FROM ingest_artifacts; ROLLBACK;
--      → 성공해야 한다(대조군)
--
-- 4) 보관 래퍼
--    SELECT public.ingest_queue_archived_count();                         -- 숫자
--    BEGIN; SET LOCAL ROLE anon; SELECT public.ingest_queue_archived_count(); ROLLBACK;
--      → permission denied 여야 정상


-- ------------------------------------------------------------
-- 롤백
-- ------------------------------------------------------------
-- DROP FUNCTION IF EXISTS public.ingest_queue_archived_count();
-- DROP TABLE IF EXISTS ingest_artifacts;   -- CASCADE 로 딸린 행도 사라진다
