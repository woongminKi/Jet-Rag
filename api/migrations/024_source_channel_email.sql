-- ============================================================
-- 024_source_channel_email.sql — 수익화 W4 (이메일 인제스트)
-- ============================================================
-- 배경
--   documents.source_channel CHECK 제약(001_init.sql)에 'email' 이 없어
--   이메일 인제스트로 수집된 문서 insert 가 23514 로 실패한다.
--   'email' 을 허용 목록에 추가 — 023 과 함께, webhook secret 설정 전에
--   반드시 적용해야 한다.
--
-- 적용 절차
--   Supabase Studio → SQL Editor → New query 빈 탭 → paste → Run.
--
-- 검증 SQL (적용 후)
--   SELECT pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conname = 'documents_source_channel_check';   -- 'email' 포함 확인
-- ============================================================

ALTER TABLE documents
    DROP CONSTRAINT IF EXISTS documents_source_channel_check;

ALTER TABLE documents
    ADD CONSTRAINT documents_source_channel_check CHECK (source_channel IN
        ('drag-drop','os-share','clipboard','url','camera','api','email'));

-- ============================================================
-- 끝.
-- ============================================================
