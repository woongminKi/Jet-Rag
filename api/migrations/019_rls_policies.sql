-- ============================================================
-- 019_rls_policies.sql — D2 멀티유저 RLS 정책 + chunks stats RPC (2026-05-20)
-- ============================================================
-- 배경
--   D2 (plan §2 / §4) — 백엔드는 service_role 로 자연 bypass 유지하되, DB layer 의
--   방어 심도(defense in depth)로 7 테이블에 per-user RLS 정책을 적용한다. anon /
--   authenticated 키가 노출되어도 user 간 데이터 격리가 DB 차원에서 보장된다.
--
-- 적용 시점 (plan §0 / Q4 게이트):
--   D1 ship 완료 + 018(default_user_id 이관) 적용 + JETRAG_AUTH_ENABLED=true 전환
--   "직후" — applied at D2 deploy after D1 ship. 단, service_role bypass 라 어떤
--   순서로 적용해도 백엔드 회귀 0 (auth_enabled=false 환경에서도 안전).
--
-- 7 테이블 정책 요약 (plan §2 표)
--   직접 매칭 (user_id = auth.uid())
--     - documents          : SELECT/INSERT/UPDATE/DELETE 4정책 + SELECT 는 deleted_at IS NULL
--     - answer_feedback    : 4정책 (user_id NULL 허용 — INSERT WITH CHECK 는 user_id=auth.uid() 강제)
--     - answer_ragas_evals : 4정책 (동일)
--   EXISTS JOIN (documents 경유)
--     - chunks             : doc_id → documents.user_id = auth.uid() + deleted_at IS NULL
--     - ingest_jobs        : 동일 (Realtime publication 도 자동 격리)
--   2-hop JOIN (ingest_jobs → documents)
--     - ingest_logs        : job_id → ingest_jobs.doc_id → documents.user_id
--   초대 코드 (제한적 SELECT)
--     - invite_codes       : SELECT used_by=auth.uid() 만 / I/U/D 차단 (service_role 만 redeem)
--
--   글로벌 운영 (정책 불요 — 002 패턴 + service_role only):
--     - vision_usage_log / search_metrics_log / vision_page_cache / embed_query_cache
--
-- 멱등성
--   DROP POLICY IF EXISTS → CREATE POLICY 패턴. 마이그 재실행 시 no-op.
--   RPC 는 CREATE OR REPLACE FUNCTION.
--
-- ROLLBACK (운영 게이트 — 베타 공개 차단)
--   ALTER TABLE <name> DISABLE ROW LEVEL SECURITY;  -- 7 테이블 + Storage 정책은 020 에서
--   DROP POLICY IF EXISTS <name> ON <table>;
-- ============================================================

BEGIN;

-- ============================================================
-- documents : 4정책 (user_id = auth.uid())
-- ============================================================
-- SELECT 는 deleted_at IS NULL 추가 — soft-delete 된 row 는 본인이라도 안 보임.
DROP POLICY IF EXISTS documents_select_own ON documents;
CREATE POLICY documents_select_own ON documents
    FOR SELECT TO authenticated
    USING (user_id = auth.uid() AND deleted_at IS NULL);

DROP POLICY IF EXISTS documents_insert_own ON documents;
CREATE POLICY documents_insert_own ON documents
    FOR INSERT TO authenticated
    WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS documents_update_own ON documents;
CREATE POLICY documents_update_own ON documents
    FOR UPDATE TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS documents_delete_own ON documents;
CREATE POLICY documents_delete_own ON documents
    FOR DELETE TO authenticated
    USING (user_id = auth.uid());

-- ============================================================
-- chunks : EXISTS JOIN documents (doc_id 경유, user_id = auth.uid() + deleted_at IS NULL)
-- ============================================================
DROP POLICY IF EXISTS chunks_select_own ON chunks;
CREATE POLICY chunks_select_own ON chunks
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = chunks.doc_id
              AND d.user_id = auth.uid()
              AND d.deleted_at IS NULL
        )
    );

DROP POLICY IF EXISTS chunks_insert_own ON chunks;
CREATE POLICY chunks_insert_own ON chunks
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = chunks.doc_id
              AND d.user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS chunks_update_own ON chunks;
CREATE POLICY chunks_update_own ON chunks
    FOR UPDATE TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = chunks.doc_id
              AND d.user_id = auth.uid()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = chunks.doc_id
              AND d.user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS chunks_delete_own ON chunks;
CREATE POLICY chunks_delete_own ON chunks
    FOR DELETE TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = chunks.doc_id
              AND d.user_id = auth.uid()
        )
    );

-- ============================================================
-- ingest_jobs : EXISTS JOIN documents
-- ============================================================
-- doc_id 가 NULL 인 row 는 정책 통과 X (EXISTS 가 false) — D2 미보강 (plan Q10).
DROP POLICY IF EXISTS ingest_jobs_select_own ON ingest_jobs;
CREATE POLICY ingest_jobs_select_own ON ingest_jobs
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = ingest_jobs.doc_id
              AND d.user_id = auth.uid()
              AND d.deleted_at IS NULL
        )
    );

DROP POLICY IF EXISTS ingest_jobs_insert_own ON ingest_jobs;
CREATE POLICY ingest_jobs_insert_own ON ingest_jobs
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = ingest_jobs.doc_id
              AND d.user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS ingest_jobs_update_own ON ingest_jobs;
CREATE POLICY ingest_jobs_update_own ON ingest_jobs
    FOR UPDATE TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = ingest_jobs.doc_id
              AND d.user_id = auth.uid()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = ingest_jobs.doc_id
              AND d.user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS ingest_jobs_delete_own ON ingest_jobs;
CREATE POLICY ingest_jobs_delete_own ON ingest_jobs
    FOR DELETE TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = ingest_jobs.doc_id
              AND d.user_id = auth.uid()
        )
    );

-- ============================================================
-- ingest_logs : 2-hop JOIN (ingest_jobs → documents)
-- ============================================================
-- ingest_logs.job_id → ingest_jobs.doc_id → documents.user_id.
-- 2-hop 성능은 idx_ingest_logs_job + idx_ingest_jobs_doc + idx_documents_user_created
-- 가 모두 활용 — production 측정 후 LIMIT 1 / RPC 우회 검토 (plan §7).
DROP POLICY IF EXISTS ingest_logs_select_own ON ingest_logs;
CREATE POLICY ingest_logs_select_own ON ingest_logs
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1
              FROM ingest_jobs j
              JOIN documents d ON d.id = j.doc_id
            WHERE j.id = ingest_logs.job_id
              AND d.user_id = auth.uid()
              AND d.deleted_at IS NULL
        )
    );

DROP POLICY IF EXISTS ingest_logs_insert_own ON ingest_logs;
CREATE POLICY ingest_logs_insert_own ON ingest_logs
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1
              FROM ingest_jobs j
              JOIN documents d ON d.id = j.doc_id
            WHERE j.id = ingest_logs.job_id
              AND d.user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS ingest_logs_update_own ON ingest_logs;
CREATE POLICY ingest_logs_update_own ON ingest_logs
    FOR UPDATE TO authenticated
    USING (
        EXISTS (
            SELECT 1
              FROM ingest_jobs j
              JOIN documents d ON d.id = j.doc_id
            WHERE j.id = ingest_logs.job_id
              AND d.user_id = auth.uid()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1
              FROM ingest_jobs j
              JOIN documents d ON d.id = j.doc_id
            WHERE j.id = ingest_logs.job_id
              AND d.user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS ingest_logs_delete_own ON ingest_logs;
CREATE POLICY ingest_logs_delete_own ON ingest_logs
    FOR DELETE TO authenticated
    USING (
        EXISTS (
            SELECT 1
              FROM ingest_jobs j
              JOIN documents d ON d.id = j.doc_id
            WHERE j.id = ingest_logs.job_id
              AND d.user_id = auth.uid()
        )
    );

-- ============================================================
-- answer_feedback : user_id = auth.uid()
-- ============================================================
-- user_id 컬럼은 NULL 허용(011) 이지만 INSERT WITH CHECK 가 NULL 을 차단 — authenticated
-- 컨텍스트에서는 항상 본인 UUID 가 강제 채워진다.
DROP POLICY IF EXISTS answer_feedback_select_own ON answer_feedback;
CREATE POLICY answer_feedback_select_own ON answer_feedback
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS answer_feedback_insert_own ON answer_feedback;
CREATE POLICY answer_feedback_insert_own ON answer_feedback
    FOR INSERT TO authenticated
    WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS answer_feedback_update_own ON answer_feedback;
CREATE POLICY answer_feedback_update_own ON answer_feedback
    FOR UPDATE TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS answer_feedback_delete_own ON answer_feedback;
CREATE POLICY answer_feedback_delete_own ON answer_feedback
    FOR DELETE TO authenticated
    USING (user_id = auth.uid());

-- ============================================================
-- answer_ragas_evals : user_id = auth.uid()
-- ============================================================
DROP POLICY IF EXISTS answer_ragas_evals_select_own ON answer_ragas_evals;
CREATE POLICY answer_ragas_evals_select_own ON answer_ragas_evals
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS answer_ragas_evals_insert_own ON answer_ragas_evals;
CREATE POLICY answer_ragas_evals_insert_own ON answer_ragas_evals
    FOR INSERT TO authenticated
    WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS answer_ragas_evals_update_own ON answer_ragas_evals;
CREATE POLICY answer_ragas_evals_update_own ON answer_ragas_evals
    FOR UPDATE TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS answer_ragas_evals_delete_own ON answer_ragas_evals;
CREATE POLICY answer_ragas_evals_delete_own ON answer_ragas_evals
    FOR DELETE TO authenticated
    USING (user_id = auth.uid());

-- ============================================================
-- invite_codes : SELECT used_by=auth.uid() 만 / I/U/D 는 service_role 만 (정책 없음)
-- ============================================================
-- 초대 코드의 INSERT/UPDATE/DELETE 는 운영자 라우터(/auth/redeem-invite) 가
-- service_role 로만 수행. authenticated 는 자신이 소진한 코드 1건만 조회 가능
-- (이력 노출 용도 — 현재 직접 endpoint 없으나 D3+ 확장 대비).
DROP POLICY IF EXISTS invite_codes_select_own ON invite_codes;
CREATE POLICY invite_codes_select_own ON invite_codes
    FOR SELECT TO authenticated
    USING (used_by = auth.uid());
-- I/U/D 정책 명시적 차단 — authenticated 는 redeem 불가 (service_role 만 가능).
-- 정책 없음 = anon/authenticated 자연 차단 (001 패턴).

-- ============================================================
-- chunks stats RPC : P1#2 누출 차단 (plan §4 / D2 senior-qa P1#2)
-- ============================================================
-- stats.py 가 chunks count("exact") 를 user 필터 없이 호출 → 전역 카운트 노출
-- (P1#2). 본 RPC 는 호출자의 documents.user_id 와 일치하는 chunks 만 집계.
-- SECURITY DEFINER 로 RLS bypass — 단 user_id_arg 와 호출자의 일치는 service_role
-- 가 백엔드 라우터에서 보장 (CurrentUser.user_id 만 전달).
--
-- 이중 방어 (D2 senior-qa P1#2):
--   1) GRANT EXECUTE TO service_role 만 — authenticated 호출 자체를 DB layer 에서 차단
--      (백엔드는 service_role 로 호출하므로 회귀 0).
--   2) 함수 본문 진입부 caller mismatch 가드 — 만일 future GRANT 가 authenticated 로
--      확장되더라도, auth.uid() ≠ user_id_arg 호출은 즉시 raise. service_role 호출 시
--      auth.uid() 는 NULL → 가드 통과. authenticated 호출은 본인 UUID 일치만 통과.
DROP FUNCTION IF EXISTS get_chunks_stats_for_user(UUID);
CREATE OR REPLACE FUNCTION get_chunks_stats_for_user(user_id_arg UUID)
RETURNS TABLE(
    total BIGINT,
    filtered BIGINT,
    breakdown JSONB
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    -- 이중 방어 — auth.uid() 가 호출자 본인이 아닐 때 차단 (cross-user 누출 방어).
    -- service_role 호출은 auth.uid() = NULL 이므로 자연 통과.
    IF auth.uid() IS NOT NULL AND auth.uid() <> user_id_arg THEN
        RAISE EXCEPTION 'unauthorized: caller mismatch';
    END IF;

    RETURN QUERY
    WITH user_chunks AS (
        SELECT c.flags
          FROM chunks c
          JOIN documents d ON d.id = c.doc_id
         WHERE d.user_id = user_id_arg
           AND d.deleted_at IS NULL
    ),
    counts AS (
        SELECT
            COUNT(*) AS c_total,
            COUNT(*) FILTER (WHERE flags ? 'filtered_reason') AS c_filtered
          FROM user_chunks
    ),
    reasons AS (
        SELECT
            flags->>'filtered_reason' AS reason,
            COUNT(*) AS n
          FROM user_chunks
         WHERE flags ? 'filtered_reason'
           AND flags->>'filtered_reason' IS NOT NULL
         GROUP BY flags->>'filtered_reason'
    )
    SELECT
        counts.c_total,
        counts.c_filtered,
        COALESCE(
            (SELECT jsonb_object_agg(reason, n) FROM reasons),
            '{}'::jsonb
        )
      FROM counts;
END;
$$;

-- D2 senior-qa P1#2 — GRANT 를 service_role 로 좁힘 (authenticated 제거).
-- 백엔드 라우터는 service_role 로만 호출하므로 회귀 0. anon/authenticated 키 직접
-- 호출은 DB layer 에서 즉시 거부 → cross-user chunks 카운트 누출 자연 차단.
GRANT EXECUTE ON FUNCTION get_chunks_stats_for_user(UUID) TO service_role;

COMMIT;

-- ============================================================
-- 검증 SQL (적용 후 수동 실행 권장)
-- ------------------------------------------------------------
-- 1) 정책 7 테이블 × 4 정책 = 28 + invite_codes SELECT 1 = 29 (chunks_*/invite_codes_select_own 포함)
--    SELECT tablename, policyname FROM pg_policies
--     WHERE schemaname = 'public'
--       AND tablename IN ('documents','chunks','ingest_jobs','ingest_logs',
--                         'answer_feedback','answer_ragas_evals','invite_codes')
--     ORDER BY tablename, policyname;
--
-- 2) RPC 등록 확인
--    SELECT proname FROM pg_proc WHERE proname = 'get_chunks_stats_for_user';  -- 1 row
--
-- 3) RPC 동작 (service_role 로 직접 호출)
--    SELECT * FROM get_chunks_stats_for_user('00000000-0000-0000-0000-000000000001');
-- ============================================================
