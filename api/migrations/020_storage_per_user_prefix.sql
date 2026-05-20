-- ============================================================
-- 020_storage_per_user_prefix.sql — D2 Storage per-user prefix 이관 (2026-05-20)
-- ============================================================
-- 배경
--   D2 (plan §3) — Storage 객체를 `<sha256>{ext}` 단일 namespace → `user/<uid>/...`
--   per-user prefix 로 이관. Storage RLS 정책 4 개 (SELECT/INSERT/UPDATE/DELETE on
--   storage.objects) 가 prefix 기반 격리를 강제 (한 user 의 anon/authenticated 키로
--   다른 user 의 객체 접근 차단).
--
-- 신규 업로드 경로 (auth_enabled 무관)
--   본 D2 코드는 `JETRAG_AUTH_ENABLED` 상태와 무관하게 신규 업로드를 항상
--   `user/<user_id>/...` prefix 로 저장한다 (D2 senior-qa P1#1).
--     - auth_enabled=true : <user_id> = 호출자 본인 Supabase UUID
--     - auth_enabled=false: <user_id> = settings.default_user_id (단일 사용자 fallback)
--   PART 1 의 `NOT LIKE 'user/%'` 가드가 이미 prefix 가 있는 신규 row 를 자연 skip
--   하므로, 본 마이그를 deploy 후 다시 실행해도 신규 row 에 영향 0 (멱등).
--
-- ──────────────────────────────────────────────────────────────
-- 패턴 A — 단순 (단일유저 베타 권장)
-- ──────────────────────────────────────────────────────────────
--   PART 1 (storage_path UPDATE)
--      → migrate_storage_to_per_user.py  (default 모드 = move + delete)
--      → PART 2 (Storage RLS 정책 활성화)
--   특성:
--     - PART 1 적용 직후 ~ script 실행 사이 짧은 (~1분) downtime.
--       documents.storage_path 는 새 위치를 가리키나 실제 객체는 옛 위치 →
--       이 window 에서 GET/signed_url 호출 시 404.
--     - 심야 실행 또는 maintenance 안내 + 단일 사용자 환경에서 운영 비용 최소.
--
-- ──────────────────────────────────────────────────────────────
-- 패턴 B — downtime 0 (멀티유저 운영 모드)
-- ──────────────────────────────────────────────────────────────
--   migrate_storage_to_per_user.py --copy-only  (old/new 양쪽 보존)
--      → PART 1 (storage_path UPDATE)
--      → migrate_storage_to_per_user.py --cleanup-only  (old path 제거)
--      → PART 2 (Storage RLS 정책 활성화)
--   특성:
--     - --copy-only 가 객체를 new path 에 복사하고 old 는 보존. PART 1 직전까지
--       응답은 old path 로 정상 (코드는 old 도 수용 — blob_id=path 직접 전달).
--     - PART 1 적용 즉시 응답은 new path 로 정상 — 객체가 이미 존재해서 404 0.
--     - --cleanup-only 가 사후 old path 만 제거 (DB 의 new path 와 일치하는 객체가
--       이미 존재함을 확인 후).
--     - PART 2 는 모든 객체가 user prefix 로 정렬된 후에 활성화 — 안전.
--   비용: copy-only 단계에서 Storage 객체가 일시적으로 2배 (사후 cleanup-only 로
--         원복). 베타 ~30 user × 평균 doc 수 N 정도면 무시 가능 수준.
--
-- ──────────────────────────────────────────────────────────────
-- 멱등성
--   - PART 1: `WHERE storage_path NOT LIKE 'user/%'` — 이미 prefix 있는 row 는 skip.
--   - PART 2: DROP POLICY IF EXISTS → CREATE POLICY. 재실행 시 no-op.
--   - 스크립트 default(move): _object_exists(new) 시 skip / _object_exists(old) 없으면 skip.
--   - 스크립트 --copy-only: new 이미 존재 시 skip (upload upsert=true 라 멱등).
--   - 스크립트 --cleanup-only: new 가 없으면 보수적으로 skip (old 도 제거 X).
--
-- ROLLBACK (사고 시 — 베타 공개 차단 후 1회성)
--   -- PART 2 정책 제거
--   DROP POLICY IF EXISTS "documents_select_own" ON storage.objects;
--   DROP POLICY IF EXISTS "documents_insert_own" ON storage.objects;
--   DROP POLICY IF EXISTS "documents_update_own" ON storage.objects;
--   DROP POLICY IF EXISTS "documents_delete_own" ON storage.objects;
--   -- PART 1 storage_path 복원 (user prefix 제거)
--   UPDATE documents
--      SET storage_path = regexp_replace(storage_path, '^user/[^/]+/', '')
--    WHERE storage_path LIKE 'user/%' AND deleted_at IS NULL;
--   -- Storage 객체는 별도 스크립트로 user prefix 제거 (현 구현 미제공)
--
-- 사전 요건
--   - D1 ship 완료 (Q4 게이트) + JETRAG_AUTH_ENABLED=true 전환 직전 또는 직후
--   - 018 (default_user_id → 본인 UUID 이관) 완료 — storage_path 에 적용될 user_id 가
--     모두 본인 UUID 가 되도록 보장.
--   - 019 (RLS 정책) 적용 권장 — DB layer 와 Storage layer 격리 동시 활성화.
-- ============================================================


-- ============================================================
-- PART 1 — documents.storage_path 일괄 prefix
-- ------------------------------------------------------------
-- 실행 시점: Step 2 (코드 deploy 직후, move 스크립트 실행 직전).
-- 영향: documents 의 storage_path 가 새 위치를 가리키게 갱신. 실제 Storage 객체는
--       Step 3 (move 스크립트) 가 이동.
-- 멱등: `NOT LIKE 'user/%'` 가드로 이미 갱신된 row 는 skip.
-- ============================================================
BEGIN;

UPDATE documents
    SET storage_path = 'user/' || user_id::text || '/' || storage_path
    WHERE storage_path NOT LIKE 'user/%'
      AND deleted_at IS NULL;

COMMIT;


-- ============================================================
-- PART 2 — Storage RLS 정책 4 개 활성화
-- ------------------------------------------------------------
-- 실행 시점: Step 4 (move 스크립트 완료 후).
-- 사전 검증 SQL (PART 2 실행 전 반드시 확인):
--   SELECT COUNT(*) FROM storage.objects
--    WHERE bucket_id = 'documents'
--      AND (
--        (storage.foldername(name))[1] IS DISTINCT FROM 'user'
--        OR (storage.foldername(name))[2] IS NULL
--      );
--   -- 0 row 이어야 함. 1+ row 면 move 스크립트 누락 — PART 2 적용 보류.
-- ============================================================

-- documents 버킷의 user prefix 매칭만 통과. (storage.foldername(name))[1]='user' AND
-- [2]=auth.uid()::text. authenticated 컨텍스트에서 본인 prefix 만 SELECT.
DROP POLICY IF EXISTS "documents_select_own" ON storage.objects;
CREATE POLICY "documents_select_own" ON storage.objects
    FOR SELECT TO authenticated
    USING (
        bucket_id = 'documents'
        AND (storage.foldername(name))[1] = 'user'
        AND (storage.foldername(name))[2] = auth.uid()::text
    );

DROP POLICY IF EXISTS "documents_insert_own" ON storage.objects;
CREATE POLICY "documents_insert_own" ON storage.objects
    FOR INSERT TO authenticated
    WITH CHECK (
        bucket_id = 'documents'
        AND (storage.foldername(name))[1] = 'user'
        AND (storage.foldername(name))[2] = auth.uid()::text
    );

DROP POLICY IF EXISTS "documents_update_own" ON storage.objects;
CREATE POLICY "documents_update_own" ON storage.objects
    FOR UPDATE TO authenticated
    USING (
        bucket_id = 'documents'
        AND (storage.foldername(name))[1] = 'user'
        AND (storage.foldername(name))[2] = auth.uid()::text
    )
    WITH CHECK (
        bucket_id = 'documents'
        AND (storage.foldername(name))[1] = 'user'
        AND (storage.foldername(name))[2] = auth.uid()::text
    );

DROP POLICY IF EXISTS "documents_delete_own" ON storage.objects;
CREATE POLICY "documents_delete_own" ON storage.objects
    FOR DELETE TO authenticated
    USING (
        bucket_id = 'documents'
        AND (storage.foldername(name))[1] = 'user'
        AND (storage.foldername(name))[2] = auth.uid()::text
    );

-- ============================================================
-- 적용 후 검증 SQL
-- ------------------------------------------------------------
-- 1) documents.storage_path 가 모두 user prefix 인지
--    SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL
--      AND storage_path NOT LIKE 'user/%';  -- 0 이어야 함
--
-- 2) storage.objects 가 모두 user prefix 인지 (move 스크립트 완료 검증)
--    SELECT COUNT(*) FROM storage.objects
--     WHERE bucket_id = 'documents'
--       AND name NOT LIKE 'user/%';  -- 0 이어야 함
--
-- 3) Storage RLS 정책 4 개 등록
--    SELECT policyname FROM pg_policies
--     WHERE schemaname = 'storage' AND tablename = 'objects'
--       AND policyname LIKE 'documents_%';  -- 4 row
-- ============================================================
