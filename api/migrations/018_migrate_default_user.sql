-- ============================================================
-- 018_migrate_default_user.sql — D1 기존 단일-유저 데이터 본인 계정 이관 (2026-05-20)
-- ============================================================
-- 배경
--   D1 (plan §6) — 기존 single-user MVP 의 모든 row 는 default_user_id
--   (00000000-0000-0000-0000-000000000001) 로 적재됨. 본인 Supabase 가입 후
--   포트폴리오 demo 데이터를 본인 계정으로 귀속시킨다 (1회성).
--
-- 실행 시점 (plan §4)
--   본인 가입 + OWNER_USER_ID 확보 후, JETRAG_AUTH_ENABLED=true 전환 "직전" 1회.
--   (전환 전이면 default_user_id fallback 이라 본인 로그인 시 0건 노출 회피.)
--
-- 사용법 (Supabase SQL Editor — psql \set 미지원이므로 직접 치환)
--   1. 아래 두 placeholder 를 실제 UUID 로 치환:
--        :owner  → 본인 Supabase user UUID (Authentication > Users 에서 복사)
--        :legacy → 00000000-0000-0000-0000-000000000001 (default_user_id)
--   2. SQL Editor 에서 전체 실행.
--
-- 멱등성
--   user_id = :legacy 인 row 만 UPDATE → 재실행 시 이미 :owner 로 바뀐 row 는
--   매칭 0 (no-op). chunks 는 user_id 컬럼 없음 (doc_id 종속) — 이관 불요 (plan §6 / F2).
--
-- ⚠️ 대상 테이블 확인 (마이그 005/006/014 검수 결과 — 2026-05-20):
--   user_id 컬럼 보유 = documents(001) / answer_feedback(011) / answer_ragas_evals(012)
--   user_id 컬럼 미보유 = vision_usage_log(005) / search_metrics_log(006)
--     → "단일 사용자 MVP — user_id 컬럼 미도입" (005 주석). 이관 대상 아님.
--     → 운영 메트릭은 admin (OWNER 전용) 에서 전역 조회하므로 user 격리 불요.
--
-- ROLLBACK (필요 시 :owner ↔ :legacy 역방향 1회):
--   UPDATE documents          SET user_id = ':legacy' WHERE user_id = ':owner';
--   UPDATE answer_feedback    SET user_id = ':legacy' WHERE user_id = ':owner';
--   UPDATE answer_ragas_evals SET user_id = ':legacy' WHERE user_id = ':owner';
-- ============================================================

BEGIN;

UPDATE documents
    SET user_id = ':owner'
    WHERE user_id = ':legacy';

UPDATE answer_feedback
    SET user_id = ':owner'
    WHERE user_id = ':legacy';

UPDATE answer_ragas_evals
    SET user_id = ':owner'
    WHERE user_id = ':legacy';

COMMIT;
