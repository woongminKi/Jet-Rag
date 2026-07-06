# W3 — plans/subscriptions + 사용량 미터링(Free/Pro enforcement) 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Free(문서 10 · 답변 일 5회)/Pro(문서 200 · 답변 일 50회) 플랜 한도를 DB 로 정의하고, 로그인 사용자의 답변·업로드에 플랜 quota(402)를 W2 rate limit 게이트에 통합 적용한다.

**Architecture:** 마이그 022 로 `plans`(한도 정의) + `subscriptions`(유저↔플랜) 테이블을 추가하고, `app/services/quota.py` 가 유효 플랜 해석(`get_effective_plan`)·보유 문서 수 카운트를 담당한다. 기존 `app/services/rate_limit.py` 의 `enforce_rate_limit` 을 **통합 게이트**로 확장 — usage_counters increment 1회 결과로 플랜 cap(402, 로그인 유저만)과 abuse cap(429, 전체)을 순차 판정한다 (별도 dependency 로 만들면 카운터가 요청당 +2 되는 버그 소지 — 사용자 확정 2026-07-06). `GET /me/plan` 과 admin 수동 구독 upsert(W5-6 결제 전 베타 Pro 체험 부여용)를 추가한다.

**Tech Stack:** Python 3.12 / FastAPI / Supabase(Postgres, service_role) / unittest + FastAPI TestClient

**전제 (기존 코드 사실 — 구현 전 이해 필수):**
- W2 배포 완료: `usage_counters(user_key TEXT, metric TEXT, period_date DATE, count INT)` + `increment_usage_counter(p_user_key, p_metric, p_period_date) RETURNS INTEGER` RPC (마이그 021). `app/services/rate_limit.py` 가 `GET /answer`("answers") · `POST /documents`·`/documents/url`("docs") 에 라우터 의존성으로 걸려 있음 (429).
- `CurrentUser`(`api/app/auth/dependencies.py:41`) = frozen dataclass `user_id: str, email: str|None=None, is_authenticated: bool=True`. 익명 데모 = `is_authenticated=False`(owner uid 공유).
- `Settings`(`api/app/config.py`) frozen dataclass. W2 까지 필수 필드 17개 (테스트 `_settings()` 헬퍼 참조). `_parse_bool`/`_parse_int` 헬퍼 존재. `get_settings()` 는 `@lru_cache`.
- `get_supabase_client()`(`api/app/db/client.py:9`) = service_role (RLS 우회), `@lru_cache`. 사용: `client.table("x").select(...).execute()` / `client.rpc("name", {...}).execute()`.
- documents 테이블: `user_id UUID`, `deleted_at TIMESTAMPTZ`(soft-delete), `flags JSONB`(`flags.failed`). 보유 문서 수 = `COUNT WHERE user_id=? AND deleted_at IS NULL`.
- admin 라우터(`api/app/routers/admin.py:34`): `APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])` — 라우터 레벨 게이트.
- 라우터 등록: `api/app/routers/__init__.py` 에 `from .x import router as x_router` + `__all__`, `api/app/main.py:156-162` 에 `app.include_router(x_router)`.
- 최신 마이그레이션 = 021. **다음 번호 = 022.** 마이그레이션은 Supabase Studio SQL Editor 수동 실행 (코드 자동 적용 없음).
- RLS 선례: 019(`documents.user_id = auth.uid()` 본인 SELECT) / 021(service_role only). RPC 선례: 021 `SECURITY DEFINER` + `REVOKE FROM PUBLIC` + `GRANT TO service_role`.
- 테스트: `cd api && uv run python -m unittest discover tests`. baseline 1348 (failures=4/errors=3/skipped=12 — 기존 flaky embed_cache/hwp, 회귀 아님).
- CORS `allow_methods=["GET", "POST"]`(main.py:152) — 새 endpoint 는 GET/POST 만.

**설계 결정 (사용자 확정 2026-07-06):**
1. **문서 한도 = 보유 문서 수** — `deleted_at IS NULL` COUNT ≥ `max_documents` 면 업로드 402. 삭제하면 다시 업로드 가능. W2 일일 docs rate limit(남용 방어)과 역할 분리.
2. **통합 게이트** — `enforce_rate_limit` 확장. increment 1회 → ① 로그인 유저 플랜 cap 초과 → **402** ② abuse cap(ENV) 초과 → **429**. 익명 데모는 플랜 없음 → ①skip, ②만.
3. **OWNER bypass** — `user_id == settings.owner_user_id` 면 quota skip (admin 본인). abuse cap 은 기존대로 적용.
4. **fail-open** — 플랜 조회/문서 카운트 실패 시 quota skip + warning (W2 rate limit 과 동일 철학). 회복 토글 `JETRAG_QUOTA_ENFORCEMENT_ENABLED`(기본 true).
5. **한도 숫자 = DB seed** (Free 10/5, Pro 200/50, 6,900원 — 스펙 가안). 확정(오픈 이슈 #1) 시 `UPDATE plans` 만으로 조정 — 재배포 불필요.
6. **W2 동작 변화 1건**: abuse cap=0(무제한)이어도 quota 대상이면 카운터는 increment 됨 (기존: cap=0 이면 increment 자체 skip). 기존 테스트 1건 수정 필요 (Task 4 Step 1).
7. 402 는 W5-6 결제 연동 전이지만 스펙 예약값 그대로 사용 — 프론트는 detail 메시지(한국어 업그레이드 안내)를 표시.

---

## 파일 구조

| 파일 | 책임 | 변경 |
|---|---|---|
| `api/migrations/022_plans_subscriptions.sql` | plans/subscriptions 테이블 + seed + RLS | **Create** |
| `api/app/config.py` | `quota_enforcement_enabled` 토글 | Modify |
| `api/app/services/quota.py` | 유효 플랜 해석 · 보유 문서 수 · 금일 사용량 조회 | **Create** |
| `api/app/services/rate_limit.py` | 통합 게이트 (402 quota 판정 추가) | Modify |
| `api/app/routers/me.py` | `GET /me/plan` (플랜 + 사용량) | **Create** |
| `api/app/routers/admin.py` | 구독 수동 upsert/조회 endpoint | Modify |
| `api/app/routers/__init__.py`, `api/app/main.py` | me 라우터 등록 | Modify |
| `api/tests/test_config.py` | quota 토글 parse 테스트 | Modify |
| `api/tests/test_quota.py` | quota 서비스 단위 테스트 | **Create** |
| `api/tests/test_rate_limit.py` | 통합 게이트 402 단위 테스트 + 기존 1건 수정 | Modify |
| `api/tests/test_quota_routes.py` | /answer·/documents 402 + /me/plan 통합 테스트 | **Create** |
| `api/tests/test_admin_subscriptions.py` | admin 구독 endpoint 테스트 | **Create** |

---

## Task 1: 마이그레이션 022 — plans/subscriptions

**Files:**
- Create: `api/migrations/022_plans_subscriptions.sql`

> SQL 마이그레이션은 pytest 대상 아님. 검증은 Task 7 롤아웃에서 SQL Editor 검증 SQL 로 대체. Task 2~3 의 Python 테스트가 테이블 계약(컬럼명)을 mock 으로 고정한다.

- [ ] **Step 1: 마이그레이션 파일 작성**

`api/migrations/022_plans_subscriptions.sql`:

```sql
-- ============================================================
-- 022_plans_subscriptions.sql — 수익화 W3 (플랜·구독 + quota 기반)
-- ============================================================
-- 배경
--   W3 = Free/Pro 플랜 한도 정의 + 사용량 enforcement(402).
--   W5-6 카카오페이 정기결제가 subscriptions(status/billing_key/current_period_end)
--   를 그대로 재사용한다. 결제 전까지는 admin 수동 upsert 로 Pro 부여 (베타 체험).
--
-- 설계
--   - plans: code PK. 한도 숫자는 스펙 가안(오픈 이슈 #1) — UPDATE 만으로 조정 가능.
--     max_documents = 보유 문서 수 상한 (deleted_at IS NULL COUNT).
--     answers_per_day = 일일 답변 상한 (usage_counters 'answers' 재사용).
--     0 이하 = 해당 한도 무제한.
--   - subscriptions: user_id PK (유저당 1행). status active/past_due 만 유효 플랜.
--     past_due = 결제 실패 7일 grace (W5-6 상태 머신 예약). canceled/행 없음 = free.
--     billing_key 는 W5-6 카카오페이 SID 저장용 (지금은 NULL).
--
-- RLS
--   - plans: 가격표 = 공개 정보. authenticated/anon SELECT 허용.
--   - subscriptions: 본인 SELECT only (019 documents 패턴). 쓰기는 service_role 만.
--
-- 적용 절차
--   Supabase Studio → SQL Editor → New query 빈 탭 → 본 파일 paste → Run.
--
-- 검증 SQL (적용 후)
--   SELECT * FROM plans ORDER BY price_krw;                       -- free/pro 2행
--   INSERT INTO subscriptions (user_id, plan_code) VALUES
--     ('00000000-0000-0000-0000-00000000dead', 'pro');
--   SELECT * FROM subscriptions;                                  -- 1행, status=active
--   DELETE FROM subscriptions
--     WHERE user_id = '00000000-0000-0000-0000-00000000dead';     -- cleanup
-- ============================================================

CREATE TABLE IF NOT EXISTS plans (
    code            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    max_documents   INTEGER NOT NULL,
    answers_per_day INTEGER NOT NULL,
    price_krw       INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- seed — 스펙 가안 (2026-07-05 sprint 디자인). 확정 시 UPDATE 로 조정.
INSERT INTO plans (code, name, max_documents, answers_per_day, price_krw)
VALUES
    ('free', 'Free', 10, 5, 0),
    ('pro',  'Pro',  200, 50, 6900)
ON CONFLICT (code) DO NOTHING;

CREATE TABLE IF NOT EXISTS subscriptions (
    user_id            UUID PRIMARY KEY,
    plan_code          TEXT NOT NULL REFERENCES plans(code),
    status             TEXT NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'past_due', 'canceled')),
    current_period_end TIMESTAMPTZ,
    billing_key        TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------- RLS ----------------
ALTER TABLE plans ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS plans_select_all ON plans;
CREATE POLICY plans_select_all
    ON plans FOR SELECT
    TO authenticated, anon
    USING (TRUE);

DROP POLICY IF EXISTS plans_service_role_all ON plans;
CREATE POLICY plans_service_role_all
    ON plans FOR ALL
    TO service_role
    USING (TRUE) WITH CHECK (TRUE);

ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS subscriptions_select_own ON subscriptions;
CREATE POLICY subscriptions_select_own
    ON subscriptions FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS subscriptions_service_role_all ON subscriptions;
CREATE POLICY subscriptions_service_role_all
    ON subscriptions FOR ALL
    TO service_role
    USING (TRUE) WITH CHECK (TRUE);

-- ============================================================
-- 끝. Python 연동은 app/services/quota.py (Task 3).
-- ============================================================
```

- [ ] **Step 2: 파일만 커밋 (production 적용은 Task 7)**

```bash
git add api/migrations/022_plans_subscriptions.sql
git commit -m "feat(migration-w3): plans/subscriptions 테이블 + Free/Pro seed (quota 기반)"
```

---

## Task 2: config — quota enforcement 토글

**Files:**
- Modify: `api/app/config.py`
- Test: `api/tests/test_config.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_config.py` 하단(기존 `RateLimitSettingsTest` 뒤)에 추가:

```python
class QuotaSettingsTest(unittest.TestCase):
    """수익화 W3 — quota enforcement 토글 parse."""

    def _clear(self) -> None:
        os.environ.pop("JETRAG_QUOTA_ENFORCEMENT_ENABLED", None)
        config.get_settings.cache_clear()

    def test_default_true(self) -> None:
        self._clear()
        try:
            self.assertTrue(config.get_settings().quota_enforcement_enabled)
        finally:
            self._clear()

    def test_env_false(self) -> None:
        self._clear()
        os.environ["JETRAG_QUOTA_ENFORCEMENT_ENABLED"] = "false"
        try:
            self.assertFalse(config.get_settings().quota_enforcement_enabled)
        finally:
            self._clear()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_config.QuotaSettingsTest -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'quota_enforcement_enabled'`

- [ ] **Step 3: Settings 필드 + 파싱 추가**

`api/app/config.py` — `Settings` 의 `rate_limit_docs_per_day: int = 30` 바로 아래에 추가:

```python
    # 수익화 W3 (2026-07-06) — 플랜 quota(402) 회복 토글. false 면 플랜 한도 전면 skip
    # (W2 abuse rate limit 429 는 별개로 유지). plans/subscriptions 장애 시 즉시 회복용.
    quota_enforcement_enabled: bool = True
```

`get_settings()` 의 `rate_limit_docs_per_day=...` 줄 바로 뒤에 추가:

```python
        quota_enforcement_enabled=_parse_bool("JETRAG_QUOTA_ENFORCEMENT_ENABLED", True),
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_config.QuotaSettingsTest -v`
Expected: PASS (2 tests)

- [ ] **Step 5: 커밋**

```bash
git add api/app/config.py api/tests/test_config.py
git commit -m "feat(config-w3): quota enforcement 회복 토글 ENV"
```

---

## Task 3: quota 서비스 — 유효 플랜 해석 + 카운트

**Files:**
- Create: `api/app/services/quota.py`
- Test: `api/tests/test_quota.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_quota.py`:

```python
"""수익화 W3 — app.services.quota 단위 테스트. MagicMock Supabase, 외부 I/O 0."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")


def _table_client(tables: dict[str, list[dict]]) -> MagicMock:
    """table(name) 별로 지정된 data 를 반환하는 mock. 체이닝 전부 흡수."""
    client = MagicMock()

    def _table(name: str) -> MagicMock:
        t = MagicMock()
        resp = MagicMock()
        resp.data = tables.get(name, [])
        resp.count = len(tables.get(name, []))
        # select().eq()...execute() 어떤 체인이든 마지막 execute 가 resp 반환
        t.select.return_value = t
        t.eq.return_value = t
        t.is_.return_value = t
        t.limit.return_value = t
        t.execute.return_value = resp
        return t

    client.table.side_effect = _table
    return client


class GetEffectivePlanTest(unittest.TestCase):
    def test_no_subscription_falls_back_to_free(self) -> None:
        from app.services import quota

        client = _table_client({
            "subscriptions": [],
            "plans": [{"code": "free", "max_documents": 10, "answers_per_day": 5}],
        })
        with patch.object(quota, "get_supabase_client", return_value=client):
            plan = quota.get_effective_plan("uid-1")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.code, "free")
        self.assertEqual(plan.max_documents, 10)
        self.assertEqual(plan.answers_per_day, 5)

    def test_active_pro_subscription(self) -> None:
        from app.services import quota

        client = _table_client({
            "subscriptions": [{"plan_code": "pro", "status": "active"}],
            "plans": [{"code": "pro", "max_documents": 200, "answers_per_day": 50}],
        })
        with patch.object(quota, "get_supabase_client", return_value=client):
            plan = quota.get_effective_plan("uid-1")
        self.assertEqual(plan.code, "pro")

    def test_past_due_still_effective(self) -> None:
        # W5-6 grace period 예약 — past_due 는 아직 유효 플랜.
        from app.services import quota

        client = _table_client({
            "subscriptions": [{"plan_code": "pro", "status": "past_due"}],
            "plans": [{"code": "pro", "max_documents": 200, "answers_per_day": 50}],
        })
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertEqual(quota.get_effective_plan("uid-1").code, "pro")

    def test_canceled_falls_back_to_free(self) -> None:
        from app.services import quota

        client = _table_client({
            "subscriptions": [{"plan_code": "pro", "status": "canceled"}],
            "plans": [{"code": "free", "max_documents": 10, "answers_per_day": 5}],
        })
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertEqual(quota.get_effective_plan("uid-1").code, "free")

    def test_db_error_fails_open_none(self) -> None:
        from app.services import quota

        client = MagicMock()
        client.table.side_effect = RuntimeError("db down")
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertIsNone(quota.get_effective_plan("uid-1"))

    def test_missing_plan_row_returns_none(self) -> None:
        from app.services import quota

        client = _table_client({"subscriptions": [], "plans": []})
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertIsNone(quota.get_effective_plan("uid-1"))


class CountActiveDocumentsTest(unittest.TestCase):
    def test_returns_count(self) -> None:
        from app.services import quota

        client = _table_client({"documents": [{"id": "a"}, {"id": "b"}]})
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertEqual(quota.count_active_documents("uid-1"), 2)

    def test_db_error_returns_none(self) -> None:
        from app.services import quota

        client = MagicMock()
        client.table.side_effect = RuntimeError("db down")
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertIsNone(quota.count_active_documents("uid-1"))


class GetTodaysCountTest(unittest.TestCase):
    def test_returns_zero_when_no_row(self) -> None:
        from app.services import quota

        client = _table_client({"usage_counters": []})
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertEqual(quota.get_todays_count("uid-1", "answers"), 0)

    def test_returns_count_value(self) -> None:
        from app.services import quota

        client = _table_client({"usage_counters": [{"count": 7}]})
        with patch.object(quota, "get_supabase_client", return_value=client):
            self.assertEqual(quota.get_todays_count("uid-1", "answers"), 7)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_quota -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.quota'`

- [ ] **Step 3: quota 서비스 구현**

`api/app/services/quota.py`:

```python
"""수익화 W3 — 플랜(Free/Pro) 해석 + 사용량 카운트 조회.

plans/subscriptions(마이그 022) 를 읽어 유저의 유효 플랜 한도를 산출한다.
enforcement 는 app/services/rate_limit.py 통합 게이트가 담당(402) —
본 모듈은 조회 전용. W4 이메일 인제스트(Pro 게이트)·W5-6 결제가 재사용.

정책
- 구독 행 없음 / status='canceled' → free.
- status IN ('active', 'past_due') → 해당 plan_code (past_due = grace, W5-6 예약).
- 어떤 DB 실패든 None 반환 (fail-open — 호출측이 quota skip + warning).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.db import get_supabase_client

logger = logging.getLogger(__name__)

_EFFECTIVE_STATUSES = ("active", "past_due")


@dataclass(frozen=True)
class PlanLimits:
    code: str
    max_documents: int
    answers_per_day: int


def get_effective_plan(user_id: str) -> PlanLimits | None:
    """유저의 유효 플랜 한도. 실패 시 None (fail-open)."""
    try:
        client = get_supabase_client()
        sub_rows = (
            client.table("subscriptions")
            .select("plan_code, status")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
            .data
        ) or []
        code = "free"
        if sub_rows and sub_rows[0].get("status") in _EFFECTIVE_STATUSES:
            code = sub_rows[0]["plan_code"]

        plan_rows = (
            client.table("plans")
            .select("code, max_documents, answers_per_day")
            .eq("code", code)
            .limit(1)
            .execute()
            .data
        ) or []
        if not plan_rows:
            logger.warning("plans 테이블에 code=%s 없음 — quota fail-open", code)
            return None
        row = plan_rows[0]
        return PlanLimits(
            code=row["code"],
            max_documents=int(row["max_documents"]),
            answers_per_day=int(row["answers_per_day"]),
        )
    except Exception as exc:  # noqa: BLE001 — 조회 실패는 fail-open
        logger.warning("플랜 조회 실패 — quota fail-open (user=%s): %s", user_id, exc)
        return None


def count_active_documents(user_id: str) -> int | None:
    """보유 문서 수 (deleted_at IS NULL). 실패 시 None (fail-open)."""
    try:
        resp = (
            get_supabase_client()
            .table("documents")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
        return int(resp.count or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("문서 수 카운트 실패 — quota fail-open (user=%s): %s", user_id, exc)
        return None


def get_todays_count(user_key: str, metric: str) -> int:
    """usage_counters 의 금일 카운트 (표시용 — /me/plan). 실패 시 0."""
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        rows = (
            get_supabase_client()
            .table("usage_counters")
            .select("count")
            .eq("user_key", user_key)
            .eq("metric", metric)
            .eq("period_date", today)
            .limit(1)
            .execute()
            .data
        ) or []
        return int(rows[0]["count"]) if rows else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("금일 사용량 조회 실패 — 0 반환 (key=%s): %s", user_key, exc)
        return 0
```

> mock 주의: 테스트의 `_table_client` 는 `resp.count = len(data)` 로 count 를 흉내낸다. `count_active_documents` 는 `.limit(1)` 을 걸어도 `count="exact"` 라 전체 count 가 오는 실제 PostgREST 동작과 일치.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_quota -v`
Expected: PASS (10 tests)

- [ ] **Step 5: 커밋**

```bash
git add api/app/services/quota.py api/tests/test_quota.py
git commit -m "feat(quota-w3): 플랜 해석·보유 문서 수·금일 사용량 조회 서비스"
```

---

## Task 4: rate_limit 통합 게이트 — 플랜 quota 402

**Files:**
- Modify: `api/app/services/rate_limit.py`
- Modify: `api/tests/test_rate_limit.py`

- [ ] **Step 1: 기존 테스트 정비 + 실패하는 테스트 추가**

`api/tests/test_rate_limit.py` 수정 3건:

(a) `_make_settings` 의 base dict 에 한 줄 추가 (`rate_limit_docs_per_day=3,` 뒤):

```python
        quota_enforcement_enabled=True,
```

(b) `EnforceRateLimitTest.test_unlimited_when_cap_zero` 를 아래로 교체 — W3 부터 quota 대상이면 cap=0 이어도 increment 하므로, "완전 무제한" 시나리오는 quota off 로 표현:

```python
    def test_unlimited_when_cap_zero_and_quota_off(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(
            rate_limit_answers_per_day=0, quota_enforcement_enabled=False
        )
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client") as gc:
            rate_limit.enforce_rate_limit("answers", _FakeRequest(), user, settings)
            gc.assert_not_called()
```

(c) `EnforceRateLimitTest` 에 `setUp` 추가 — 기존 케이스들이 실 플랜 조회를 타지 않게 격리 (외부 I/O 0 유지):

```python
    def setUp(self) -> None:
        from app.services import rate_limit

        patcher = patch.object(rate_limit.quota, "get_effective_plan", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
```

이어서 새 클래스 추가 (파일 하단, `if __name__` 위):

```python
class QuotaGateTest(unittest.TestCase):
    """수익화 W3 — 통합 게이트의 플랜 quota(402) 판정."""

    def _mock_client(self, returned_count: int) -> MagicMock:
        client = MagicMock()
        client.rpc.return_value.execute.return_value.data = returned_count
        return client

    def _free_plan(self):
        from app.services.quota import PlanLimits

        return PlanLimits(code="free", max_documents=10, answers_per_day=5)

    def test_answers_over_plan_cap_raises_402(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_answers_per_day=50)
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client", return_value=self._mock_client(6)), \
             patch.object(rate_limit.quota, "get_effective_plan", return_value=self._free_plan()):
            with self.assertRaises(HTTPException) as ctx:
                rate_limit.enforce_rate_limit("answers", _FakeRequest(), user, settings)
        self.assertEqual(ctx.exception.status_code, 402)

    def test_answers_at_plan_cap_passes(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_answers_per_day=50)
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client", return_value=self._mock_client(5)), \
             patch.object(rate_limit.quota, "get_effective_plan", return_value=self._free_plan()):
            rate_limit.enforce_rate_limit("answers", _FakeRequest(), user, settings)

    def test_anonymous_skips_quota_but_keeps_abuse_cap(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_answers_per_day=5)
        anon = CurrentUser(user_id="owner", is_authenticated=False)
        with patch.object(rate_limit, "get_supabase_client", return_value=self._mock_client(6)), \
             patch.object(rate_limit.quota, "get_effective_plan") as gp:
            with self.assertRaises(HTTPException) as ctx:
                rate_limit.enforce_rate_limit("answers", _FakeRequest(), anon, settings)
            gp.assert_not_called()
        self.assertEqual(ctx.exception.status_code, 429)

    def test_owner_bypasses_quota(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_answers_per_day=50)
        owner = CurrentUser(
            user_id="00000000-0000-0000-0000-0000000000ff", is_authenticated=True
        )
        with patch.object(rate_limit, "get_supabase_client", return_value=self._mock_client(6)), \
             patch.object(rate_limit.quota, "get_effective_plan") as gp:
            rate_limit.enforce_rate_limit("answers", _FakeRequest(), owner, settings)
            gp.assert_not_called()

    def test_quota_toggle_off_skips_plan_check(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(
            rate_limit_answers_per_day=50, quota_enforcement_enabled=False
        )
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client", return_value=self._mock_client(6)), \
             patch.object(rate_limit.quota, "get_effective_plan") as gp:
            rate_limit.enforce_rate_limit("answers", _FakeRequest(), user, settings)
            gp.assert_not_called()

    def test_plan_lookup_failure_fails_open(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_answers_per_day=50)
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client", return_value=self._mock_client(6)), \
             patch.object(rate_limit.quota, "get_effective_plan", return_value=None):
            rate_limit.enforce_rate_limit("answers", _FakeRequest(), user, settings)

    def test_docs_retention_cap_raises_402(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_docs_per_day=30)
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client", return_value=self._mock_client(1)), \
             patch.object(rate_limit.quota, "get_effective_plan", return_value=self._free_plan()), \
             patch.object(rate_limit.quota, "count_active_documents", return_value=10):
            with self.assertRaises(HTTPException) as ctx:
                rate_limit.enforce_rate_limit("docs", _FakeRequest(), user, settings)
        self.assertEqual(ctx.exception.status_code, 402)

    def test_docs_under_retention_cap_passes(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_docs_per_day=30)
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client", return_value=self._mock_client(1)), \
             patch.object(rate_limit.quota, "get_effective_plan", return_value=self._free_plan()), \
             patch.object(rate_limit.quota, "count_active_documents", return_value=9):
            rate_limit.enforce_rate_limit("docs", _FakeRequest(), user, settings)

    def test_doc_count_failure_fails_open(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_docs_per_day=30)
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client", return_value=self._mock_client(1)), \
             patch.object(rate_limit.quota, "get_effective_plan", return_value=self._free_plan()), \
             patch.object(rate_limit.quota, "count_active_documents", return_value=None):
            rate_limit.enforce_rate_limit("docs", _FakeRequest(), user, settings)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_rate_limit -v`
Expected: FAIL — `QuotaGateTest` 전부 (rate_limit 에 `quota` 속성 없음 `AttributeError`), 기존 케이스는 PASS 유지.

- [ ] **Step 3: enforce_rate_limit 확장**

`api/app/services/rate_limit.py` 수정 2곳.

(a) import 블록 `from app.db import get_supabase_client` 아래에 추가:

```python
from app.services import quota
```

(b) `enforce_rate_limit` 본문 전체를 아래로 교체 (시그니처·docstring 유지, 내부만):

```python
def enforce_rate_limit(
    metric: str,
    request: Request,
    current_user: CurrentUser,
    settings: Settings,
) -> None:
    """metric 의 일일 카운터를 1 증가시키고 상한 초과 시 402/429.

    통합 게이트 (수익화 W3) — increment 1회 결과로 두 상한을 순차 판정:
      ① 플랜 quota (로그인 유저만, OWNER 제외) → 402 + 업그레이드 안내
      ② abuse cap (ENV, 익명 포함 전체) → 429
    부수효과: usage_counters +1 (auth_enabled=true & 게이트 활성 시).
    """
    if not settings.auth_enabled:
        return  # 로컬 dev — 기존 동작 보존.

    abuse_cap = _cap_for_metric(metric, settings)
    quota_active = (
        settings.quota_enforcement_enabled
        and current_user.is_authenticated
        and current_user.user_id != (settings.owner_user_id or "")
    )
    if abuse_cap <= 0 and not quota_active:
        return  # 완전 무제한 (회복 토글).

    user_key = build_user_key(current_user, request)
    period_date = datetime.now(timezone.utc).date().isoformat()
    try:
        resp = get_supabase_client().rpc(
            "increment_usage_counter",
            {
                "p_user_key": user_key,
                "p_metric": metric,
                "p_period_date": period_date,
            },
        ).execute()
        new_count = resp.data
    except Exception as exc:  # noqa: BLE001 — DB 실패는 fail-open
        logger.warning("rate_limit RPC 실패 — fail-open (metric=%s): %s", metric, exc)
        return

    # ---- ① 플랜 quota (W3, 402) ----
    if quota_active:
        plan = quota.get_effective_plan(current_user.user_id)
        if plan is not None:
            if (
                metric == _METRIC_ANSWERS
                and plan.answers_per_day > 0
                and isinstance(new_count, int)
                and new_count > plan.answers_per_day
            ):
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=(
                        f"{plan.code} 플랜의 일일 답변 한도({plan.answers_per_day}회)를 "
                        "초과했습니다. 내일 다시 이용하시거나 Pro 로 업그레이드해 주세요."
                    ),
                )
            if metric == _METRIC_DOCS and plan.max_documents > 0:
                doc_count = quota.count_active_documents(current_user.user_id)
                if doc_count is not None and doc_count >= plan.max_documents:
                    raise HTTPException(
                        status_code=status.HTTP_402_PAYMENT_REQUIRED,
                        detail=(
                            f"{plan.code} 플랜의 보유 문서 한도({plan.max_documents}개)에 "
                            "도달했습니다. 기존 문서를 삭제하시거나 Pro 로 업그레이드해 주세요."
                        ),
                    )

    # ---- ② abuse cap (W2, 429) ----
    if isinstance(new_count, int) and abuse_cap > 0 and new_count > abuse_cap:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"일일 사용 한도({abuse_cap}회)를 초과했습니다. "
                "내일 다시 시도하시거나 Pro 로 업그레이드해 주세요."
            ),
        )
```

> 모듈 docstring 의 "정책" 단락에도 한 줄 추가: `- W3 통합 게이트: increment 1회로 플랜 quota(402, 로그인 유저)·abuse cap(429) 순차 판정. OWNER 는 quota bypass.`

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_rate_limit tests.test_rate_limit_routes -v`
Expected: PASS 전부 (기존 429 route 테스트 포함 — quota 는 플랜 조회 실패 시 fail-open 이므로 무영향이나, Step 5 에서 명시 격리 확인)

- [ ] **Step 5: 기존 route 테스트 격리 보강**

`api/tests/test_rate_limit_routes.py` 의 `AnswerRateLimitTest.setUp`/`UploadRateLimitTest.setUp` 각각 끝에 추가 (실 DB 접근 원천 차단):

```python
        quota_patcher = patch(
            "app.services.quota.get_effective_plan", return_value=None
        )
        quota_patcher.start()
        self.addCleanup(quota_patcher.stop)
```

Run: `cd api && uv run python -m unittest tests.test_rate_limit_routes -v`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add api/app/services/rate_limit.py api/tests/test_rate_limit.py api/tests/test_rate_limit_routes.py
git commit -m "feat(quota-w3): rate limit 통합 게이트에 플랜 quota 402 판정 추가"
```

---

## Task 5: `GET /me/plan` + 402 route 통합 테스트

**Files:**
- Create: `api/app/routers/me.py`
- Modify: `api/app/routers/__init__.py`, `api/app/main.py`
- Test: `api/tests/test_quota_routes.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_quota_routes.py`:

```python
"""수익화 W3 — 402 게이트 route 통합 + GET /me/plan.

429 route 테스트(test_rate_limit_routes.py)와 동일 전략 — 게이트가 핸들러 진입 전
short-circuit 하므로 검색/LLM I/O 0.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from fastapi.testclient import TestClient

from app.auth.dependencies import CurrentUser, get_current_user
from app.config import Settings, get_settings
from app.main import app
from app.services.quota import PlanLimits


def _settings(**over) -> Settings:
    base = dict(
        supabase_url="https://x.supabase.co",
        supabase_key="",
        supabase_service_role_key="svc",
        supabase_storage_bucket="documents",
        gemini_api_key="",
        hf_api_token="",
        default_user_id="00000000-0000-0000-0000-000000000001",
        doc_budget_usd=0.1,
        daily_budget_usd=0.5,
        sliding_24h_budget_usd=0.5,
        budget_krw_per_usd=1380.0,
        vision_need_score_enabled=True,
        vision_page_cap_per_doc=50,
        auth_enabled=True,
        owner_user_id="00000000-0000-0000-0000-0000000000ff",
        rate_limit_answers_per_day=50,
        rate_limit_docs_per_day=30,
        quota_enforcement_enabled=True,
    )
    base.update(over)
    return Settings(**base)


_FREE = PlanLimits(code="free", max_documents=10, answers_per_day=5)


def _counter_client(count: int) -> MagicMock:
    client = MagicMock()
    client.rpc.return_value.execute.return_value.data = count
    return client


class QuotaRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.authed = CurrentUser(user_id="uid-1", is_authenticated=True)
        app.dependency_overrides[get_current_user] = lambda: self.authed
        app.dependency_overrides[get_settings] = lambda: _settings()
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_answer_over_plan_cap_returns_402(self) -> None:
        with patch(
            "app.services.rate_limit.get_supabase_client",
            return_value=_counter_client(6),
        ), patch("app.services.quota.get_effective_plan", return_value=_FREE):
            resp = self.client.get("/answer", params={"q": "테스트 질문"})
        self.assertEqual(resp.status_code, 402)
        self.assertIn("업그레이드", resp.json()["detail"])

    def test_upload_at_doc_retention_cap_returns_402(self) -> None:
        files = {"file": ("t.pdf", b"%PDF-1.4 test", "application/pdf")}
        with patch(
            "app.services.rate_limit.get_supabase_client",
            return_value=_counter_client(1),
        ), patch("app.services.quota.get_effective_plan", return_value=_FREE), patch(
            "app.services.quota.count_active_documents", return_value=10
        ):
            resp = self.client.post("/documents", files=files)
        self.assertEqual(resp.status_code, 402)
        self.assertIn("문서 한도", resp.json()["detail"])


class MePlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.authed = CurrentUser(user_id="uid-1", is_authenticated=True)
        app.dependency_overrides[get_current_user] = lambda: self.authed
        app.dependency_overrides[get_settings] = lambda: _settings()
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_returns_plan_and_usage(self) -> None:
        with patch(
            "app.routers.me.quota.get_effective_plan", return_value=_FREE
        ), patch(
            "app.routers.me.quota.get_todays_count", return_value=3
        ), patch(
            "app.routers.me.quota.count_active_documents", return_value=7
        ):
            resp = self.client.get("/me/plan")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["plan_code"], "free")
        self.assertEqual(body["answers_per_day"], 5)
        self.assertEqual(body["answers_used_today"], 3)
        self.assertEqual(body["documents_count"], 7)
        self.assertEqual(body["max_documents"], 10)

    def test_anonymous_gets_401(self) -> None:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="owner", is_authenticated=False
        )
        resp = self.client.get("/me/plan")
        self.assertEqual(resp.status_code, 401)

    def test_plan_lookup_failure_returns_503(self) -> None:
        with patch("app.routers.me.quota.get_effective_plan", return_value=None):
            resp = self.client.get("/me/plan")
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_quota_routes -v`
Expected: `QuotaRouteTest` 2건 PASS (Task 4 에서 이미 게이트 적용됨), `MePlanTest` FAIL — `/me/plan` 404 (라우터 없음). `app.routers.me` import 에러가 먼저 나면 그것으로 실패 확인 OK.

- [ ] **Step 3: me 라우터 구현**

`api/app/routers/me.py`:

```python
"""수익화 W3 — 로그인 사용자 본인 플랜·사용량 조회.

프론트가 402 업그레이드 안내·사용량 표시에 사용. W4 이메일 인제스트
설정(/me/email-ingest)이 이 라우터에 추가된다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import (
    LEGACY_DEFAULT_USER,
    CurrentUserDep,
    require_authenticated_user,
)
from app.services import quota

router = APIRouter(
    prefix="/me",
    tags=["me"],
    dependencies=[Depends(require_authenticated_user)],
)


class MePlanResponse(BaseModel):
    plan_code: str
    max_documents: int
    answers_per_day: int
    answers_used_today: int
    documents_count: int


@router.get("/plan", response_model=MePlanResponse)
def me_plan(current_user: CurrentUserDep = LEGACY_DEFAULT_USER) -> MePlanResponse:
    plan = quota.get_effective_plan(current_user.user_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="플랜 정보를 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.",
        )
    return MePlanResponse(
        plan_code=plan.code,
        max_documents=plan.max_documents,
        answers_per_day=plan.answers_per_day,
        answers_used_today=quota.get_todays_count(current_user.user_id, "answers"),
        documents_count=quota.count_active_documents(current_user.user_id) or 0,
    )
```

> `app.auth` 에서 `LEGACY_DEFAULT_USER`/`CurrentUserDep`/`require_authenticated_user` 를 export 하는지 확인 — documents.py:37-41 이 같은 import 를 쓰므로 동일하게 가능.

- [ ] **Step 4: 라우터 등록**

`api/app/routers/__init__.py` — alphabetical 위치에 추가:

```python
from .me import router as me_router
```

`__all__` 에 `"me_router",` 추가 (documents_router 뒤).

`api/app/main.py` — (a) 30-37행 import 목록에 `me_router,` 추가, (b) `app.include_router(auth_router)` 아래에 추가:

```python
# 수익화 W3 — /me/plan. 로그인 필수 (require_authenticated_user 라우터 게이트).
app.include_router(me_router)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_quota_routes -v`
Expected: PASS (5 tests)

- [ ] **Step 6: 커밋**

```bash
git add api/app/routers/me.py api/app/routers/__init__.py api/app/main.py api/tests/test_quota_routes.py
git commit -m "feat(me-w3): GET /me/plan — 플랜·금일 사용량·보유 문서 수"
```

---

## Task 6: admin 구독 수동 upsert (W5-6 결제 전 Pro 부여)

**Files:**
- Modify: `api/app/routers/admin.py`
- Test: `api/tests/test_admin_subscriptions.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_admin_subscriptions.py`:

```python
"""수익화 W3 — admin 구독 수동 upsert/조회. require_admin 게이트는 라우터 레벨."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from fastapi.testclient import TestClient

from app.auth.dependencies import CurrentUser, get_current_user, require_admin
from app.main import app

_ADMIN = CurrentUser(user_id="00000000-0000-0000-0000-0000000000ff", is_authenticated=True)


class AdminSubscriptionsTest(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        app.dependency_overrides[get_current_user] = lambda: _ADMIN
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_upsert_subscription(self) -> None:
        mock_client = MagicMock()
        mock_client.table.return_value.upsert.return_value.execute.return_value.data = [
            {"user_id": "u-1", "plan_code": "pro", "status": "active"}
        ]
        with patch(
            "app.routers.admin.get_supabase_client", return_value=mock_client
        ):
            resp = self.client.post(
                "/admin/subscriptions",
                json={"user_id": "u-1", "plan_code": "pro"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["plan_code"], "pro")
        mock_client.table.assert_called_with("subscriptions")

    def test_list_subscriptions(self) -> None:
        mock_client = MagicMock()
        (
            mock_client.table.return_value.select.return_value.order.return_value
            .limit.return_value.execute.return_value
        ).data = [
            {
                "user_id": "u-1",
                "plan_code": "pro",
                "status": "active",
                "current_period_end": None,
                "updated_at": "2026-07-06T00:00:00+00:00",
            }
        ]
        with patch(
            "app.routers.admin.get_supabase_client", return_value=mock_client
        ):
            resp = self.client.get("/admin/subscriptions")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["items"]), 1)


if __name__ == "__main__":
    unittest.main()
```

> `admin.py` 가 `get_supabase_client` 를 이미 import 하는지 Read 로 확인 — 없으면 Step 3 에서 import 추가. patch 대상은 `app.routers.admin.get_supabase_client` (모듈 로컬 이름).

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_admin_subscriptions -v`
Expected: FAIL — 404 (endpoint 없음)

- [ ] **Step 3: admin endpoint 구현**

`api/app/routers/admin.py` — 파일 하단에 추가 (import 에 `get_supabase_client`·`BaseModel`·`Literal`·`Query` 등 누락분 보충):

```python
# ============================================================
# 수익화 W3 — 구독 수동 관리 (W5-6 결제 연동 전 베타 Pro 부여)
# ============================================================
class SubscriptionUpsertRequest(BaseModel):
    user_id: str
    plan_code: Literal["free", "pro"]
    status: Literal["active", "past_due", "canceled"] = "active"
    current_period_end: str | None = None  # ISO8601. 수동 부여는 보통 None(무기한).


class SubscriptionItem(BaseModel):
    user_id: str
    plan_code: str
    status: str
    current_period_end: str | None = None
    updated_at: str | None = None


class SubscriptionListResponse(BaseModel):
    items: list[SubscriptionItem]


@router.post("/subscriptions", response_model=SubscriptionItem)
def admin_upsert_subscription(payload: SubscriptionUpsertRequest) -> SubscriptionItem:
    """유저 구독 수동 upsert. 결제 연동(W5-6) 전 베타 Pro 체험 부여·회수용."""
    row = {
        "user_id": payload.user_id,
        "plan_code": payload.plan_code,
        "status": payload.status,
        "current_period_end": payload.current_period_end,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    resp = (
        get_supabase_client()
        .table("subscriptions")
        .upsert(row, on_conflict="user_id")
        .execute()
    )
    saved = (resp.data or [row])[0]
    return SubscriptionItem(
        user_id=str(saved["user_id"]),
        plan_code=saved["plan_code"],
        status=saved["status"],
        current_period_end=saved.get("current_period_end"),
        updated_at=saved.get("updated_at"),
    )


@router.get("/subscriptions", response_model=SubscriptionListResponse)
def admin_list_subscriptions() -> SubscriptionListResponse:
    rows = (
        get_supabase_client()
        .table("subscriptions")
        .select("user_id, plan_code, status, current_period_end, updated_at")
        .order("updated_at", desc=True)
        .limit(100)
        .execute()
        .data
    ) or []
    return SubscriptionListResponse(
        items=[
            SubscriptionItem(
                user_id=str(r["user_id"]),
                plan_code=r["plan_code"],
                status=r["status"],
                current_period_end=r.get("current_period_end"),
                updated_at=r.get("updated_at"),
            )
            for r in rows
        ]
    )
```

> `datetime`/`timezone` import 가 admin.py 에 없으면 `from datetime import datetime, timezone` 추가.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_admin_subscriptions -v`
Expected: PASS (2 tests)

- [ ] **Step 5: 전체 회귀 확인**

Run: `cd api && uv run python -m unittest discover tests 2>&1 | tail -5`
Expected: baseline(1348) + 신규 테스트 수. failures/errors 는 기존 flaky(embed_cache/hwp) 범위 그대로 — 신규 실패 0.

- [ ] **Step 6: 커밋**

```bash
git add api/app/routers/admin.py api/tests/test_admin_subscriptions.py
git commit -m "feat(admin-w3): 구독 수동 upsert/조회 endpoint (결제 전 Pro 부여)"
```

---

## Task 7: 문서화 + production 롤아웃 (수동 — 사용자 액션 포함)

> 이 태스크는 서브에이전트가 실행하지 않음. 컨트롤러가 사용자와 함께 수행. (Step 1 문서화만 서브에이전트 가능.)

- [ ] **Step 1: 문서화**

- `.env.example`: rate limit ENV 주석 근처에 추가:

```bash
# 수익화 W3 — 플랜 quota(402) 회복 토글. false 면 플랜 한도 skip (429 abuse cap 은 유지).
# JETRAG_QUOTA_ENFORCEMENT_ENABLED=true
```

- `README.md` "현재 운영 모드" 섹션에 bullet 추가:

```markdown
- **플랜 quota (W3)**: 로그인 사용자는 Free(보유 문서 10 · 답변 일 5회) / Pro(200 · 50회) 한도 — 초과 시 402 + 업그레이드 안내. 한도는 `plans` 테이블 seed(UPDATE 로 조정). 익명 데모는 W2 rate limit(429)만 적용.
```

```bash
git add .env.example README.md
git commit -m "docs(w3): 플랜 quota 운영 모드·회복 토글 문서화"
```

- [ ] **Step 2: 마이그레이션 022 적용**

Supabase Studio(`mpmtydudhojpukuuadrd`) → SQL Editor → **New query 빈 탭** ("Unable to find snippet" 팝업 회피) → `api/migrations/022_plans_subscriptions.sql` paste → Run. 헤더의 검증 SQL 실행 (plans 2행 확인 → 테스트 구독 insert/delete).

- [ ] **Step 3: (선택) 베타 유저 Pro 부여**

베타 체험 대상 유저에게 SQL Editor 로 upsert (admin endpoint 는 프론트 admin UI 없어 curl+JWT 필요 — SQL 이 간편):

```sql
INSERT INTO subscriptions (user_id, plan_code)
VALUES ('<베타 유저 uuid>', 'pro')
ON CONFLICT (user_id) DO UPDATE SET plan_code = 'pro', status = 'active', updated_at = now();
```

> OWNER 본인은 코드 bypass 라 불필요하지만, `/me/plan` 표시 정합을 위해 pro upsert 권장 (`jetrag_owner_identity.md` 의 UUID 사용).

- [ ] **Step 4: `git push origin main` (Railway 자동 재배포)**

```bash
git push origin main
```

- [ ] **Step 5: production smoke**

```bash
# 익명 답변 — 200 (quota 미적용, 회귀 0)
curl -s -o /dev/null -w "answer(anon):%{http_code}\n" "https://jetrag-api.woong-s.com/answer?q=테스트"
# 익명 업로드 — 401 (W1 게이트 유지)
curl -s -o /dev/null -w "upload(anon):%{http_code}\n" -X POST "https://jetrag-api.woong-s.com/documents"
# /me/plan — 익명 401
curl -s -o /dev/null -w "me/plan(anon):%{http_code}\n" "https://jetrag-api.woong-s.com/me/plan"
```

로그인 유저(웹)로: 답변 1회 후 `/me/plan` 이 `answers_used_today` 증가를 반영하는지 확인.

- [ ] **Step 6: 402 실동작 확인 (선택, 신중)**

SQL Editor 로 임시 `UPDATE plans SET answers_per_day = 1 WHERE code = 'free';` → free 로그인 유저로 답변 2회 → 2번째 402 확인 → `UPDATE plans SET answers_per_day = 5 WHERE code = 'free';` 원복. (재배포 불필요 — DB 값이라 즉시 반영.)

- [ ] **Step 7: 프론트 402 표시 확인**

웹 ask 페이지가 402 응답의 `detail`(한국어 안내)을 에러 메시지로 노출하는지 확인. 노출 안 되면 후속 이슈로 기록 (W5-6 구독 UI 에서 정식 처리).

---

## Self-Review (플랜 작성자 체크리스트 결과)

**1. Spec coverage** (스펙 §1 "플랜·사용량 미터링 W3-4"):
- plans/subscriptions 테이블 → Task 1. ✅ (스펙의 "usage_counters 마이그 021~022" 는 W2 에서 021 로 선반영 — daily 카운터를 그대로 재사용, 월 단위 필요 시 SUM. 스키마 변경 없음.)
- `check_quota(metric)` dependency → 통합 게이트로 구현 방식 변경 (사용자 확정 — 카운터 이중 increment 방지). 적용 지점 answers/docs 는 스펙과 동일. ✅
- 402 + 한국어 업그레이드 안내 → Task 4. ✅
- Vision stage quota → **의도적 제외**: vision 은 기존 `vision_usage_log` + budget cap 으로 이미 방어 중, plans.vision 한도는 W5-6 이후 필요 시 (YAGNI).
- `subscriptions.billing_key` 암호화 → 컬럼만 예약(NULL). 암호화는 W5-6 결제 플랜 범위.

**2. Placeholder scan:** 모든 코드 스텝에 실제 코드·명령·기대 출력 포함. "확인 후 조정" 은 admin.py import 보충·`app.auth` export 확인 등 실제 위치가 명시된 곳에 한정. ✅

**3. Type consistency:** `PlanLimits(code, max_documents, answers_per_day)` / `get_effective_plan(user_id) -> PlanLimits|None` / `count_active_documents(user_id) -> int|None` / `get_todays_count(user_key, metric) -> int` / `quota_enforcement_enabled` — 서비스·게이트·라우터·테스트 전반 일치. rate_limit 은 `from app.services import quota` 모듈 참조라 테스트의 `patch.object(rate_limit.quota, ...)`/`patch("app.services.quota....")` 모두 유효. ✅

**주의 (구현자 확인 필요):**
- `app.auth` 패키지가 `LEGACY_DEFAULT_USER`/`CurrentUserDep`/`require_authenticated_user` 를 re-export 하는지 — documents.py:37 과 동일 import 이므로 가능하나 Read 로 확정.
- admin.py 의 기존 import 블록 — `BaseModel`/`Literal`/`get_supabase_client`/`datetime` 누락분만 보충 (기존 인자 보존).
- W4 이메일 인제스트 플랜(`2026-07-06-w4-email-ingest.md`)이 본 플랜의 `quota.get_effective_plan`(Pro 게이트)과 `me.py` 라우터에 의존 — **본 플랜 먼저 실행**.
