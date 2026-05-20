"""D2 — RLS 정책 SQL 텍스트 검증 + (선택적) anon E2E (plan §8).

본 단위 테스트는 외부 의존성 0 — 마이그 019 SQL 파일을 read 해서 정책 7 테이블 × 4
정책 + RPC 등록 키워드의 존재를 검증한다. 실제 RLS 동작은 remote Supabase 환경에서만
E2E 가능 — `JETRAG_REMOTE_RLS_E2E=1` ENV 가드 (현 환경에서는 자동 skip).

실행: `python -m unittest tests.test_rls_isolation`
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "migrations" / "019_rls_policies.sql"
)

# plan §2 표 — 정책 7 테이블 × 4 정책 = 28 + invite_codes SELECT 1 = 29.
# (invite_codes 는 SELECT 1 정책만, I/U/D 는 정책 없음 = service_role only.)
_TABLES_WITH_FULL_CRUD = (
    "documents",
    "chunks",
    "ingest_jobs",
    "ingest_logs",
    "answer_feedback",
    "answer_ragas_evals",
)
_OPERATIONS = ("select", "insert", "update", "delete")


def _read_migration_sql() -> str:
    return _MIGRATION_PATH.read_text(encoding="utf-8")


class RlsPolicySqlTest(unittest.TestCase):
    """마이그 019 SQL 파일이 plan §2 표를 모두 표현하는지 검증."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = _read_migration_sql()
        # 비교 단순화 — 소문자 정규화.
        cls.sql_lower = cls.sql.lower()

    def test_file_exists(self) -> None:
        self.assertTrue(_MIGRATION_PATH.exists(), "019_rls_policies.sql 파일이 존재해야 한다.")

    def test_transaction_wrap(self) -> None:
        """전체가 BEGIN; ... COMMIT; 트랜잭션으로 감싸져야 한다."""
        self.assertIn("begin;", self.sql_lower)
        self.assertIn("commit;", self.sql_lower)

    def test_each_table_has_4_policies(self) -> None:
        """6 테이블 × 4 작업 = 24 정책 — CREATE POLICY 키워드 매칭."""
        for table in _TABLES_WITH_FULL_CRUD:
            for op in _OPERATIONS:
                expected_name = f"{table}_{op}_own"
                with self.subTest(table=table, op=op):
                    self.assertIn(
                        expected_name.lower(),
                        self.sql_lower,
                        f"정책 {expected_name} 가 SQL 에 없음.",
                    )

    def test_invite_codes_select_only(self) -> None:
        """invite_codes 는 SELECT 정책 1개만 — I/U/D 는 service_role 만."""
        self.assertIn("invite_codes_select_own", self.sql_lower)
        # I/U/D 정책 자체가 정의되면 안 됨 (정책 없음 = 차단).
        for op in ("insert", "update", "delete"):
            self.assertNotIn(
                f"invite_codes_{op}_own", self.sql_lower,
                f"invite_codes 에 {op} 정책이 있으면 안 됨 (service_role 만).",
            )

    def test_documents_select_includes_deleted_at_null(self) -> None:
        """documents SELECT 정책은 soft-delete 된 row 를 제외해야 한다."""
        # documents_select_own 블록 안에 deleted_at IS NULL 이 있는지.
        m = re.search(
            r"create\s+policy\s+documents_select_own\s+on\s+documents.*?(?:;|\Z)",
            self.sql_lower,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "documents_select_own 정책 블록을 찾을 수 없음.")
        self.assertIn("deleted_at is null", m.group(0))

    def test_chunks_uses_exists_join_documents(self) -> None:
        """chunks 정책은 documents JOIN EXISTS 패턴이어야 한다."""
        for op in _OPERATIONS:
            with self.subTest(op=op):
                pattern = rf"create\s+policy\s+chunks_{op}_own.*?from\s+documents"
                self.assertIsNotNone(
                    re.search(pattern, self.sql_lower, re.DOTALL),
                    f"chunks_{op}_own 가 documents 서브쿼리를 사용하지 않음.",
                )

    def test_ingest_logs_uses_2_hop_join(self) -> None:
        """ingest_logs 정책은 ingest_jobs JOIN documents 2-hop 이어야 한다."""
        for op in _OPERATIONS:
            with self.subTest(op=op):
                pattern = (
                    rf"create\s+policy\s+ingest_logs_{op}_own.*?ingest_jobs.*?documents"
                )
                self.assertIsNotNone(
                    re.search(pattern, self.sql_lower, re.DOTALL),
                    f"ingest_logs_{op}_own 가 2-hop JOIN 패턴이 아님.",
                )

    def test_all_policies_target_authenticated(self) -> None:
        """모든 CREATE POLICY 가 TO authenticated 로 service_role 자연 bypass 위임해야."""
        creates = re.findall(
            r"create\s+policy\s+\w+\s+on\s+\w+.*?(?=create\s+policy|drop\s+function|commit;)",
            self.sql_lower,
            re.DOTALL,
        )
        self.assertGreaterEqual(len(creates), 25, "정책 25 개 이상 존재해야.")
        for idx, block in enumerate(creates):
            with self.subTest(idx=idx):
                self.assertIn("to authenticated", block, f"블록 #{idx} 가 TO authenticated 누락.")

    def test_drop_policy_if_exists_idempotent(self) -> None:
        """각 CREATE POLICY 앞에 DROP POLICY IF EXISTS 가 있어야 멱등.

        주석 안의 DROP POLICY (rollback 가이드) 는 카운트 제외 — `--` 로 시작하는
        라인을 lstrip 후 검사한다.
        """
        # 주석 라인 제거 후 카운트.
        non_comment = "\n".join(
            line for line in self.sql.splitlines()
            if not line.lstrip().startswith("--")
        ).lower()
        drops = re.findall(r"drop\s+policy\s+if\s+exists", non_comment)
        creates = re.findall(r"create\s+policy", non_comment)
        self.assertEqual(
            len(drops), len(creates),
            f"DROP POLICY IF EXISTS ({len(drops)}) 와 CREATE POLICY ({len(creates)}) 수 불일치.",
        )

    def test_chunks_stats_rpc_registered(self) -> None:
        """P1#2 차단 RPC `get_chunks_stats_for_user(user_id_arg UUID)` 가 등록되어야 한다."""
        self.assertIn("create or replace function get_chunks_stats_for_user", self.sql_lower)
        self.assertIn("security definer", self.sql_lower)
        self.assertIn("set search_path = public", self.sql_lower)

    def test_chunks_stats_rpc_joins_documents_user_id(self) -> None:
        """RPC 본문이 documents JOIN user_id_arg 격리를 강제해야 한다."""
        # 함수 본문 내 user_id_arg + JOIN documents 키워드.
        self.assertIn("user_id_arg", self.sql_lower)
        # JOIN 또는 FROM documents 구문 + d.user_id = user_id_arg
        self.assertTrue(
            re.search(r"join\s+documents.*?user_id\s*=\s*user_id_arg", self.sql_lower, re.DOTALL)
            is not None,
            "RPC 가 documents.user_id = user_id_arg 격리를 강제하지 않음.",
        )

    def test_chunks_stats_rpc_grant_only_service_role(self) -> None:
        """D2 senior-qa P1#2 — GRANT 라인이 service_role 만 (authenticated 제거).

        주석 라인을 제거한 본문에서 GRANT EXECUTE 라인을 검사한다. authenticated 가
        포함되면 anon/authenticated 키 직접 호출 가능 → cross-user 누출 위험.
        """
        # 주석 라인 제거 (-- 로 시작하는 라인).
        non_comment = "\n".join(
            line for line in self.sql.splitlines()
            if not line.lstrip().startswith("--")
        ).lower()
        # GRANT EXECUTE ON FUNCTION get_chunks_stats_for_user(uuid) TO ... ;
        grant_matches = re.findall(
            r"grant\s+execute\s+on\s+function\s+get_chunks_stats_for_user\s*\([^)]*\)\s+to\s+([^;]+);",
            non_comment,
            re.DOTALL,
        )
        self.assertEqual(
            len(grant_matches), 1,
            f"GRANT EXECUTE 라인이 정확히 1건이어야 함 (실제 {len(grant_matches)}).",
        )
        grantees = grant_matches[0].strip()
        # service_role 키워드 존재 + authenticated 부재.
        self.assertIn("service_role", grantees, f"GRANT 에 service_role 없음: {grantees!r}")
        self.assertNotIn(
            "authenticated", grantees,
            f"GRANT 에 authenticated 가 포함됨 (P1#2 누출 위험): {grantees!r}",
        )

    def test_chunks_stats_rpc_caller_mismatch_guard(self) -> None:
        """D2 senior-qa P1#2 — RPC 본문에 caller mismatch 가드가 있어야 한다.

        이중 방어 — GRANT 가 향후 authenticated 로 확장되더라도 auth.uid() ≠
        user_id_arg 호출은 즉시 raise.
        """
        # RPC 본문 추출 ($$ ... $$ 사이).
        m = re.search(
            r"create\s+or\s+replace\s+function\s+get_chunks_stats_for_user.*?\$\$(.*?)\$\$",
            self.sql_lower,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "RPC 본문 ($$ ... $$) 을 찾을 수 없음.")
        body = m.group(1)
        # auth.uid() IS NOT NULL AND auth.uid() <> user_id_arg → RAISE EXCEPTION 'unauthorized: caller mismatch'
        self.assertIn("auth.uid()", body, "본문에 auth.uid() 호출 없음 (가드 누락).")
        self.assertIn("user_id_arg", body)
        self.assertIn(
            "unauthorized: caller mismatch", body,
            "본문에 'unauthorized: caller mismatch' RAISE 가 없음 (가드 누락).",
        )


@unittest.skipUnless(
    os.environ.get("JETRAG_REMOTE_RLS_E2E") == "1",
    "remote RLS E2E 는 ENV 가드 (JETRAG_REMOTE_RLS_E2E=1) 가 있을 때만 실행.",
)
class RlsRemoteE2ETest(unittest.TestCase):
    """remote Supabase 환경에서 anon key 로 RLS 격리 검증.

    환경: `JETRAG_REMOTE_RLS_E2E=1`, anon/service_role 키, 두 user 의 sample row 가
    이미 존재한다고 가정. 본 sprint 의 deploy 시점에 수동 검증 (plan §8 manual smoke).
    """

    def test_anon_key_returns_empty_documents(self) -> None:
        # anon key 로 클라이언트 생성 후 documents SELECT — 정책 매칭 X → 0 row.
        # 실제 구현은 deploy 시점에 채움. 본 placeholder 는 ENV 가드로 skip.
        self.skipTest("deploy 시점 수동 검증 — manual smoke 참조 (plan §8)")


if __name__ == "__main__":
    unittest.main()
