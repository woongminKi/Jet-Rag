# W2 — Gemini 유료 키 전환 + per-user Rate Limit 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 익명 데모·로그인 사용자 모두에 일일 사용량 상한을 걸어 Gemini 유료 키 전환 후 비용 폭주·남용을 방어하고, 베타 유저 업로드를 안전하게 재개방한다.

**Architecture:** DB `usage_counters` 테이블(마이그 021) + 원자적 증가 RPC 를 기반으로, FastAPI 의존성 `check_rate_limit(metric)` 이 요청당 카운터를 1 증가시키고 일일 상한 초과 시 429 를 던진다. 카운터 키는 로그인 사용자=`user_id`, 익명 데모=`ip:<주소>` 로 분리해 OWNER 본인 사용량과 익명 트래픽이 서로를 throttle 하지 않게 한다. 이 카운터 인프라는 W3-4 플랜/구독 미터링이 그대로 재사용한다(DRY). Gemini 유료 키 전환은 코드 변경 최소 — ENV 문서화 + retry 정리 위주.

**Tech Stack:** Python 3.12 / FastAPI / Supabase(Postgres RPC, service_role) / unittest + FastAPI TestClient / google-genai SDK

**전제 (기존 코드 사실 — 구현 전 이해 필수):**
- 답변 생성 엔드포인트는 **`GET /answer`** (`api/app/routers/answer.py:432`) — 여기서 `_get_llm().complete()` 호출(505). POST /answer* 들은 피드백/eval 이라 rate limit 대상 아님.
- 업로드는 **`POST /documents`**(`upload_document`, decorator 392-396, file 파라미터명 `file`) 와 **`POST /documents/url`**(`upload_url`, decorator 577-581).
- `get_current_user`(`api/app/auth/dependencies.py:80`) 는 3-way 분기: `auth_enabled=false`→default_user+authenticated / 토큰없음→owner_user_id+`is_authenticated=False`(익명 데모) / 유효 JWT→본인+authenticated / 무효 JWT→401.
- `CurrentUser`(dependencies.py:41) 는 frozen dataclass: `user_id: str`, `email: str|None=None`, `is_authenticated: bool=True`.
- `get_supabase_client()`(`api/app/db/client.py:10`) 는 **service_role** 클라이언트 (RLS 우회). RPC 호출: `client.rpc("name", {params}).execute().data`.
- 최신 마이그레이션 = `020_storage_per_user_prefix.sql`. **다음 번호 = 021**. (013 은 결번 — 012 다음 014.)
- 마이그레이션은 코드로 자동 적용되지 않음 — 파일 헤더의 "적용 절차"대로 Supabase Studio SQL Editor 에서 수동 실행.
- 카운터 선례: `vision_usage_log`(마이그 005) = service_role-only RLS. RPC 선례: `007_metrics_trend_rpc.sql` = `SECURITY DEFINER` + `GRANT EXECUTE ... TO service_role`.
- 테스트 실행: `cd api && uv run python -m unittest discover tests`. 단일: `cd api && uv run python -m unittest tests.test_rate_limit -v`.
- 테스트 baseline: 1332 tests, failures=4/errors=3/skipped=12 (기존 flaky embed_cache/hwp — 회귀 아님).

---

## 파일 구조

| 파일 | 책임 | 변경 |
|---|---|---|
| `api/migrations/021_usage_counters.sql` | 일일 카운터 테이블 + 원자적 증가 RPC + RLS | **Create** |
| `api/app/config.py` | rate limit 상한 ENV 2개 (`Settings` 필드 + parse) | Modify (필드 추가 + get_settings) |
| `api/app/services/rate_limit.py` | user_key 산출·IP 추출·enforce·의존성 팩토리 | **Create** |
| `api/app/routers/answer.py` | `GET /answer` 에 `check_rate_limit("answers")` 게이트 | Modify (432 decorator) |
| `api/app/routers/documents.py` | `POST /documents`·`/documents/url` 에 `check_rate_limit("docs")` 게이트 | Modify (396, 581 decorator) |
| `api/app/adapters/impl/_gemini_common.py` | 유료 키 retry 주석 정리 (동작 변경 없음) | Modify (28-29 주석) |
| `.env.example` | rate limit ENV + Gemini 유료 키 문서화 | Modify |
| `README.md` | 운영 모드에 rate limit 반영 | Modify |
| `api/tests/test_rate_limit.py` | `rate_limit` 서비스 단위 테스트 | **Create** |
| `api/tests/test_rate_limit_routes.py` | 라우터 429 게이트 통합 테스트 | **Create** |
| `api/tests/test_config.py` | rate limit 설정 default·parse 테스트 | Modify (케이스 추가) |

**설계 결정 (A안 — 사용자 확정 2026-07-06):**
1. **저장 = DB 카운터** — 재시작·다중 워커 안전 + W3-4 미터링 재사용. in-memory 기각.
2. **키 분리** — 로그인=`user_id`, 익명 데모=`ip:<X-Forwarded-For 첫 항목 or client.host>`. Railway 프록시 뒤라 XFF 파싱 필수.
3. **상한** — ENV `JETRAG_RATE_LIMIT_ANSWERS_PER_DAY`(기본 50), `JETRAG_RATE_LIMIT_DOCS_PER_DAY`(기본 30). **0/음수 = 무제한**(회복 토글 — `vision_page_cap_per_doc` 패턴 계승).
4. **초과 시 429** (`Too Many Requests`) + 한국어 업그레이드 안내. (402 는 W3-4 플랜 enforcement 용으로 예약.)
5. **적용 조건** — `auth_enabled=false`(로컬 dev)면 rate limit 전면 skip — 기존 동작·테스트 100% 보존 (`require_admin` 패턴 계승).
6. **원자적 increment-then-check** — RPC 가 UPSERT 로 +1 후 새 count 반환, count>cap 이면 429. 동시성 안전.
7. **fail-open** — RPC/DB 실패 시 통과(로그 warning). DB blip 으로 정상 사용자 차단 회피. (`budget_guard`·`search_metrics` graceful 패턴 계승.)
8. **increment 시점** — 요청당 1회(핸들러 진입 전 라우터 의존성). 검색 0건으로 LLM 미호출이어도 카운트 — 남용 방어 상한이므로 요청 자체를 센다.

---

## Task 1: usage_counters 마이그레이션 021

**Files:**
- Create: `api/migrations/021_usage_counters.sql`

> SQL 마이그레이션은 pytest 대상이 아님(코드가 자동 적용하지 않음). 검증은 Supabase SQL Editor 에서 verification SQL 실행으로 대체. Task 3 의 Python 단위 테스트가 RPC 계약(파라미터·반환)을 mock 으로 고정한다.

- [ ] **Step 1: 마이그레이션 파일 작성**

`api/migrations/021_usage_counters.sql`:

```sql
-- ============================================================
-- 021_usage_counters.sql — 수익화 W2 (per-user rate limit)
-- ============================================================
-- 배경
--   W2 = Gemini 유료 키 전환 + per-user rate limit. 익명 데모/로그인 사용자
--   모두에 일일 사용량 상한을 걸어 비용 폭주·남용을 방어한다.
--   W3-4 플랜/구독 미터링(usage enforcement, 402)이 본 테이블을 그대로 재사용.
--
-- 설계
--   - user_key TEXT — 로그인=user_id(uuid 문자열) / 익명=ip:<주소>. 두 형식 모두
--                     수용하려 TEXT (uuid 타입 아님).
--   - metric TEXT — 'answers' / 'docs'.
--   - period_date DATE — UTC 일 단위 버킷.
--   - count INTEGER — 해당 (키,metric,일) 누적 요청 수.
--   - PK (user_key, metric, period_date) — UPSERT 충돌 키.
--   - increment_usage_counter RPC — 원자적 +1 후 새 count 반환 (동시성 안전).
--
-- RLS
--   - service_role 만 (백엔드 전용). anon/authenticated 는 정책 부재로 차단.
--     (005 vision_usage_log 정책과 동일 패턴.)
--
-- 적용 절차
--   Supabase Studio → SQL Editor → 본 파일 paste → Run (단일 트랜잭션).
--
-- 검증 SQL (적용 후)
--   SELECT increment_usage_counter('ip:1.2.3.4', 'answers', CURRENT_DATE);  -- → 1
--   SELECT increment_usage_counter('ip:1.2.3.4', 'answers', CURRENT_DATE);  -- → 2
--   SELECT * FROM usage_counters WHERE user_key = 'ip:1.2.3.4';            -- → count=2
--   DELETE FROM usage_counters WHERE user_key = 'ip:1.2.3.4';             -- cleanup
-- ============================================================

CREATE TABLE IF NOT EXISTS usage_counters (
    user_key     TEXT NOT NULL,
    metric       TEXT NOT NULL,
    period_date  DATE NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_key, metric, period_date)
);

-- 날짜별 정리/조회용 (W3-4 미터링 리포트)
CREATE INDEX IF NOT EXISTS idx_usage_counters_period
    ON usage_counters (period_date);

-- ---------------- RLS ----------------
ALTER TABLE usage_counters ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS usage_counters_service_role_all ON usage_counters;
CREATE POLICY usage_counters_service_role_all
    ON usage_counters
    FOR ALL
    TO service_role
    USING (TRUE)
    WITH CHECK (TRUE);

-- ---------------- 원자적 증가 RPC ----------------
CREATE OR REPLACE FUNCTION increment_usage_counter(
    p_user_key   TEXT,
    p_metric     TEXT,
    p_period_date DATE
) RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    new_count INTEGER;
BEGIN
    INSERT INTO usage_counters (user_key, metric, period_date, count, updated_at)
    VALUES (p_user_key, p_metric, p_period_date, 1, now())
    ON CONFLICT (user_key, metric, period_date)
    DO UPDATE SET count = usage_counters.count + 1, updated_at = now()
    RETURNING count INTO new_count;
    RETURN new_count;
END;
$$;

-- 007 RPC 와 동일 — service_role 만 실행. anon/authenticated 차단.
REVOKE ALL ON FUNCTION increment_usage_counter(TEXT, TEXT, DATE) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION increment_usage_counter(TEXT, TEXT, DATE) TO service_role;

-- ============================================================
-- 끝. Python 연동은 Task 3 (app/services/rate_limit.py).
-- ============================================================
```

- [ ] **Step 2: 파일만 커밋 (production 적용은 Task 7 롤아웃에서 수동 실행)**

```bash
git add api/migrations/021_usage_counters.sql
git commit -m "feat(migration-w2): usage_counters 테이블 + 원자적 증가 RPC (rate limit 기반)"
```

---

## Task 2: config — rate limit 상한 설정

**Files:**
- Modify: `api/app/config.py` (Settings 필드 추가 + get_settings 파싱)
- Test: `api/tests/test_config.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_config.py` 하단에 추가:

```python
class RateLimitSettingsTest(unittest.TestCase):
    """수익화 W2 — rate limit 상한 ENV parse."""

    def _clear(self) -> None:
        for k in ("JETRAG_RATE_LIMIT_ANSWERS_PER_DAY", "JETRAG_RATE_LIMIT_DOCS_PER_DAY"):
            os.environ.pop(k, None)
        config.get_settings.cache_clear()

    def test_defaults(self) -> None:
        self._clear()
        try:
            s = config.get_settings()
            self.assertEqual(s.rate_limit_answers_per_day, 50)
            self.assertEqual(s.rate_limit_docs_per_day, 30)
        finally:
            self._clear()

    def test_env_override(self) -> None:
        self._clear()
        os.environ["JETRAG_RATE_LIMIT_ANSWERS_PER_DAY"] = "5"
        os.environ["JETRAG_RATE_LIMIT_DOCS_PER_DAY"] = "3"
        try:
            s = config.get_settings()
            self.assertEqual(s.rate_limit_answers_per_day, 5)
            self.assertEqual(s.rate_limit_docs_per_day, 3)
        finally:
            self._clear()

    def test_zero_means_unlimited_passthrough(self) -> None:
        # 0/음수는 그대로 저장 — enforce 단계가 무제한으로 해석 (회복 토글).
        self._clear()
        os.environ["JETRAG_RATE_LIMIT_ANSWERS_PER_DAY"] = "0"
        try:
            s = config.get_settings()
            self.assertEqual(s.rate_limit_answers_per_day, 0)
        finally:
            self._clear()
```

> `test_config.py` 상단이 `import os`, `from app import config` 를 이미 갖고 있는지 확인. 없으면 파일 상단 import 에 맞춰 조정. (기존 `config.get_settings.cache_clear()` 사용 패턴을 그대로 따른다.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_config.RateLimitSettingsTest -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'rate_limit_answers_per_day'`

- [ ] **Step 3: Settings 필드 추가**

`api/app/config.py` — `Settings` dataclass 의 `owner_user_id` 필드 바로 아래(86행 뒤)에 추가:

```python
    # 수익화 W2 (2026-07-06) — per-user 일일 rate limit 상한. 남용/비용 방어.
    # 0 또는 음수 = 무제한 (회복 토글 — vision_page_cap_per_doc 패턴 계승).
    # auth_enabled=false(로컬 dev) 면 enforce 단계에서 전면 skip.
    rate_limit_answers_per_day: int = 50
    rate_limit_docs_per_day: int = 30
```

- [ ] **Step 4: get_settings 파싱 추가**

`api/app/config.py` — `get_settings()` 의 `owner_user_id=...` 줄(198행) 바로 뒤, `return Settings(` 닫는 괄호 앞에 추가:

```python
        # 수익화 W2 — rate limit 상한. invalid ENV 는 default. 0/음수 그대로(무제한 토글).
        rate_limit_answers_per_day=_parse_int("JETRAG_RATE_LIMIT_ANSWERS_PER_DAY", 50),
        rate_limit_docs_per_day=_parse_int("JETRAG_RATE_LIMIT_DOCS_PER_DAY", 30),
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_config.RateLimitSettingsTest -v`
Expected: PASS (3 tests)

- [ ] **Step 6: 커밋**

```bash
git add api/app/config.py api/tests/test_config.py
git commit -m "feat(config-w2): rate limit 일일 상한 설정 2개 (answers/docs, 무제한 토글)"
```

---

## Task 3: rate_limit 서비스 + 의존성 팩토리

**Files:**
- Create: `api/app/services/rate_limit.py`
- Test: `api/tests/test_rate_limit.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_rate_limit.py`:

```python
"""수익화 W2 — app.services.rate_limit 단위 테스트.

stdlib unittest + MagicMock (Supabase RPC mock). 외부 I/O 0.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from fastapi import HTTPException

from app.auth.dependencies import CurrentUser
from app.config import Settings


def _make_settings(**over) -> Settings:
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
        rate_limit_answers_per_day=5,
        rate_limit_docs_per_day=3,
    )
    base.update(over)
    return Settings(**base)


class _FakeRequest:
    def __init__(self, headers=None, client_host="9.9.9.9"):
        self.headers = headers or {}
        self.client = type("C", (), {"host": client_host})()


class ClientIpTest(unittest.TestCase):
    def test_xff_first_wins(self) -> None:
        from app.services.rate_limit import _client_ip

        req = _FakeRequest(headers={"X-Forwarded-For": "1.1.1.1, 2.2.2.2"})
        self.assertEqual(_client_ip(req), "1.1.1.1")

    def test_fallback_to_client_host(self) -> None:
        from app.services.rate_limit import _client_ip

        self.assertEqual(_client_ip(_FakeRequest(client_host="3.3.3.3")), "3.3.3.3")


class BuildUserKeyTest(unittest.TestCase):
    def test_authenticated_uses_user_id(self) -> None:
        from app.services.rate_limit import build_user_key

        user = CurrentUser(user_id="uid-42", is_authenticated=True)
        self.assertEqual(build_user_key(user, _FakeRequest()), "uid-42")

    def test_anonymous_uses_ip_prefix(self) -> None:
        from app.services.rate_limit import build_user_key

        user = CurrentUser(user_id="owner", is_authenticated=False)
        req = _FakeRequest(headers={"X-Forwarded-For": "8.8.8.8"})
        self.assertEqual(build_user_key(user, req), "ip:8.8.8.8")


class EnforceRateLimitTest(unittest.TestCase):
    def _mock_client(self, returned_count: int) -> MagicMock:
        client = MagicMock()
        client.rpc.return_value.execute.return_value.data = returned_count
        return client

    def test_skips_when_auth_disabled(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(auth_enabled=False)
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client") as gc:
            rate_limit.enforce_rate_limit("answers", _FakeRequest(), user, settings)
            gc.assert_not_called()  # RPC 호출 자체가 없어야 함

    def test_unlimited_when_cap_zero(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_answers_per_day=0)
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client") as gc:
            rate_limit.enforce_rate_limit("answers", _FakeRequest(), user, settings)
            gc.assert_not_called()

    def test_under_cap_passes(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_answers_per_day=5)
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client", return_value=self._mock_client(5)):
            # count == cap → 통과 (cap 은 허용 최대치)
            rate_limit.enforce_rate_limit("answers", _FakeRequest(), user, settings)

    def test_over_cap_raises_429(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_answers_per_day=5)
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client", return_value=self._mock_client(6)):
            with self.assertRaises(HTTPException) as ctx:
                rate_limit.enforce_rate_limit("answers", _FakeRequest(), user, settings)
            self.assertEqual(ctx.exception.status_code, 429)

    def test_rpc_failure_fails_open(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_answers_per_day=5)
        user = CurrentUser(user_id="u", is_authenticated=True)
        client = MagicMock()
        client.rpc.side_effect = RuntimeError("db down")
        with patch.object(rate_limit, "get_supabase_client", return_value=client):
            # 예외 전파 없이 통과해야 함 (fail-open)
            rate_limit.enforce_rate_limit("answers", _FakeRequest(), user, settings)

    def test_docs_metric_uses_docs_cap(self) -> None:
        from app.services import rate_limit

        settings = _make_settings(rate_limit_docs_per_day=3)
        user = CurrentUser(user_id="u", is_authenticated=True)
        with patch.object(rate_limit, "get_supabase_client", return_value=self._mock_client(4)):
            with self.assertRaises(HTTPException) as ctx:
                rate_limit.enforce_rate_limit("docs", _FakeRequest(), user, settings)
            self.assertEqual(ctx.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_rate_limit -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.rate_limit'`

- [ ] **Step 3: rate_limit 서비스 구현**

`api/app/services/rate_limit.py`:

```python
"""수익화 W2 — per-user 일일 rate limit.

익명 데모(ip 키)·로그인 사용자(user_id 키) 모두에 일일 상한을 걸어
Gemini 유료 키 전환 후 비용 폭주·남용을 방어한다. 카운터는 DB(usage_counters,
마이그 021) 에 원자적으로 증가 — 재시작·다중 워커 안전. W3-4 미터링이 재사용.

정책
- auth_enabled=false(로컬 dev): 전면 skip — 기존 동작·테스트 보존.
- cap<=0: 무제한 (회복 토글).
- increment-then-check: RPC 가 +1 후 새 count 반환, count>cap 이면 429.
- fail-open: RPC/DB 실패 시 통과(로그 warning). DB blip 으로 정상 사용자 차단 회피.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from fastapi import Depends, HTTPException, Request, status

from app.auth.dependencies import CurrentUser, get_current_user
from app.config import Settings, get_settings
from app.db import get_supabase_client

logger = logging.getLogger(__name__)

_METRIC_ANSWERS = "answers"
_METRIC_DOCS = "docs"


def _client_ip(request: Request) -> str:
    """프록시(Railway) 뒤 실제 클라이언트 IP. X-Forwarded-For 첫 항목 우선.

    getattr 방어 — 단위 테스트의 fake request 처럼 .client 없는 객체도 graceful.
    """
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def build_user_key(current_user: CurrentUser, request: Request) -> str:
    """rate limit 카운터 키. 로그인=user_id / 익명 데모=ip:<주소>.

    익명 데모는 전부 owner_user_id 를 공유하므로 user_id 로 세면 OWNER 본인과
    뭉친다 — IP 로 분리해 익명 남용만 격리 카운트한다.
    """
    if current_user.is_authenticated:
        return current_user.user_id
    return f"ip:{_client_ip(request)}"


def _cap_for_metric(metric: str, settings: Settings) -> int:
    if metric == _METRIC_ANSWERS:
        return settings.rate_limit_answers_per_day
    if metric == _METRIC_DOCS:
        return settings.rate_limit_docs_per_day
    return 0  # 알 수 없는 metric → 무제한 (fail-open)


def enforce_rate_limit(
    metric: str,
    request: Request,
    current_user: CurrentUser,
    settings: Settings,
) -> None:
    """metric 의 일일 카운터를 1 증가시키고 상한 초과 시 429.

    부수효과: usage_counters 카운터 +1 (auth_enabled=true & cap>0 일 때만).
    """
    if not settings.auth_enabled:
        return  # 로컬 dev — 기존 동작 보존.
    cap = _cap_for_metric(metric, settings)
    if cap <= 0:
        return  # 무제한 (회복 토글).

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

    if isinstance(new_count, int) and new_count > cap:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"일일 사용 한도({cap}회)를 초과했습니다. "
                "내일 다시 시도하시거나 Pro 로 업그레이드해 주세요."
            ),
        )


def check_rate_limit(metric: str) -> Callable[..., None]:
    """라우터 레벨 rate limit 게이트 팩토리.

    사용: `@router.get(..., dependencies=[Depends(check_rate_limit("answers"))])`
    """

    def _dependency(
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        settings: Settings = Depends(get_settings),
    ) -> None:
        enforce_rate_limit(metric, request, current_user, settings)

    return _dependency
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_rate_limit -v`
Expected: PASS (9 tests)

- [ ] **Step 5: 커밋**

```bash
git add api/app/services/rate_limit.py api/tests/test_rate_limit.py
git commit -m "feat(rate-limit-w2): usage_counters 기반 per-user 일일 rate limit 서비스 + 의존성"
```

---

## Task 4: `GET /answer` rate limit 게이트

**Files:**
- Modify: `api/app/routers/answer.py` (import + 432 decorator)
- Test: `api/tests/test_rate_limit_routes.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_rate_limit_routes.py`:

```python
"""수익화 W2 — rate limit 라우터 게이트 통합 테스트.

전략: 상한 초과(429)는 라우터 의존성이 핸들러 진입 전에 short-circuit 하므로
검색/LLM 외부 I/O 0 로 검증 가능. 통과(under-cap) 경로는 test_rate_limit.py
단위 테스트가 커버 — 여기선 429 거절만 확인한다.
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
        rate_limit_answers_per_day=5,
        rate_limit_docs_per_day=3,
    )
    base.update(over)
    return Settings(**base)


def _over_cap_client() -> MagicMock:
    client = MagicMock()
    client.rpc.return_value.execute.return_value.data = 999  # cap 초과
    return client


class AnswerRateLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.authed = CurrentUser(user_id="uid-1", is_authenticated=True)
        app.dependency_overrides[get_current_user] = lambda: self.authed
        app.dependency_overrides[get_settings] = lambda: _settings()
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_over_cap_returns_429_before_handler(self) -> None:
        with patch("app.services.rate_limit.get_supabase_client", return_value=_over_cap_client()):
            resp = self.client.get("/answer", params={"q": "테스트 질문"})
        self.assertEqual(resp.status_code, 429)
        self.assertIn("한도", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
```

> `/answer` 실제 마운트 경로 확인 — `api/app/main.py` 의 `app.include_router(answer_router, ...)`. prefix 가 있으면 위 `self.client.get("/answer")` 를 그에 맞게 조정. (answer 라우터는 `APIRouter(tags=["answer"])` 로 prefix 없음 → `/answer` 그대로.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_rate_limit_routes.AnswerRateLimitTest -v`
Expected: FAIL — 429 아님(현재 게이트 미적용이라 핸들러 진입 후 검색/LLM 경로 진입 → 다른 status).

- [ ] **Step 3: answer.py 게이트 wiring**

`api/app/routers/answer.py` — import 블록(43-47행 `from app.auth import ...`) 아래에 추가:

```python
from app.services.rate_limit import check_rate_limit
```

이어서 432행 `@router.get("/answer", response_model=AnswerResponse)` 를 아래로 교체:

```python
@router.get(
    "/answer",
    response_model=AnswerResponse,
    dependencies=[Depends(check_rate_limit("answers"))],  # 수익화 W2 — 일일 답변 상한
)
```

> `Depends` 는 answer.py 34행에서 이미 import 됨.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_rate_limit_routes.AnswerRateLimitTest -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add api/app/routers/answer.py api/tests/test_rate_limit_routes.py
git commit -m "feat(answer-w2): GET /answer 에 일일 답변 rate limit 게이트 (429)"
```

---

## Task 5: `POST /documents` 업로드 rate limit 게이트

**Files:**
- Modify: `api/app/routers/documents.py` (import + 396, 581 decorator)
- Test: `api/tests/test_rate_limit_routes.py` (케이스 추가)

- [ ] **Step 1: 실패하는 테스트 추가**

`api/tests/test_rate_limit_routes.py` 에 클래스 추가 (`_settings` / `_over_cap_client` 재사용):

```python
class UploadRateLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.authed = CurrentUser(user_id="uid-1", is_authenticated=True)
        app.dependency_overrides[get_current_user] = lambda: self.authed
        app.dependency_overrides[get_settings] = lambda: _settings()
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_upload_over_cap_returns_429(self) -> None:
        files = {"file": ("t.pdf", b"%PDF-1.4 test", "application/pdf")}
        with patch("app.services.rate_limit.get_supabase_client", return_value=_over_cap_client()):
            resp = self.client.post("/documents", files=files)
        self.assertEqual(resp.status_code, 429)
        self.assertIn("한도", resp.json()["detail"])
```

> `/documents` 마운트 경로 확인 — `main.py` 에서 documents 라우터 prefix (일반적으로 `/documents`). upload_document 는 `@router.post("")` → prefix 그대로. prefix 가 다르면 조정.

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_rate_limit_routes.UploadRateLimitTest -v`
Expected: FAIL — 429 아님 (게이트 미적용).

- [ ] **Step 3: documents.py 게이트 wiring**

`api/app/routers/documents.py` — import 블록(40행 `require_authenticated_user,` 포함 블록) 아래 적절한 위치에 추가:

```python
from app.services.rate_limit import check_rate_limit
```

`upload_document` decorator(392-397) 의 `dependencies` 리스트에 추가:

```python
@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[
        Depends(require_authenticated_user),  # 쓰기 = 로그인 필수 (수익화 W1)
        Depends(check_rate_limit("docs")),    # 수익화 W2 — 일일 업로드 상한
    ],
)
```

`upload_url` decorator(577-582) 도 동일하게 `dependencies` 리스트에 `Depends(check_rate_limit("docs"))` 추가:

```python
@router.post(
    "/url",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[
        Depends(require_authenticated_user),  # 쓰기 = 로그인 필수 (수익화 W1)
        Depends(check_rate_limit("docs")),    # 수익화 W2 — 일일 업로드 상한
    ],
)
```

> 577-582 의 실제 decorator 인자를 Read 로 먼저 확인 후, 기존 인자를 보존하며 `dependencies` 만 확장할 것. reingest 라우트(778, 867)는 신규 문서 생성이 아니므로 docs 상한 대상에서 제외(YAGNI).

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_rate_limit_routes -v`
Expected: PASS (AnswerRateLimitTest + UploadRateLimitTest 전부)

- [ ] **Step 5: 커밋**

```bash
git add api/app/routers/documents.py api/tests/test_rate_limit_routes.py
git commit -m "feat(documents-w2): 업로드 2 endpoint 에 일일 rate limit 게이트 (429)"
```

---

## Task 6: Gemini 유료 키 문서화 + retry 정리

**Files:**
- Modify: `api/app/adapters/impl/_gemini_common.py` (28-29 주석만)
- Modify: `.env.example`
- Modify: `README.md`

> 코드 동작 변경 없음 — 유료 키는 ENV `GEMINI_API_KEY` 값 교체(Task 7 롤아웃)로 적용된다. 본 태스크는 문서·주석 정합만 맞춘다. 유료 키는 무료 tier RPD 20/일 상한이 사라지므로 `JETRAG_GEMINI_RETRY` 를 굳이 올릴 필요 없음 — default 1 유지.

- [ ] **Step 1: `_gemini_common.py` 주석 갱신**

`api/app/adapters/impl/_gemini_common.py:28-29` 의 주석을 아래로 교체 (동작 동일, `_MAX_ATTEMPTS` 값 불변):

```python
#   보장 → retry 1 로 충분. 회귀 발생 시 ENV `JETRAG_GEMINI_RETRY=3` 으로 즉시 회복.
# 수익화 W2 (2026-07-06) — 유료 pay-as-you-go 키 전환 후 무료 tier RPD 20/일 상한 소멸.
#   RESOURCE_EXHAUSTED(429) 는 계정 quota 가 아닌 순간 rate 만 남아 retry 1 로 충분.
_MAX_ATTEMPTS = int(os.environ.get("JETRAG_GEMINI_RETRY", "1"))
```

- [ ] **Step 2: `.env.example` 갱신**

`.env.example` 의 `GEMINI_API_KEY=` 줄 주석에 유료 키 안내 추가, 그리고 rate limit ENV 2개를 `JETRAG_*` 토글 근처에 추가:

```bash
# 수익화 W2 — per-user 일일 rate limit 상한 (0/음수 = 무제한). auth_enabled=true 에서만 적용.
# JETRAG_RATE_LIMIT_ANSWERS_PER_DAY=50
# JETRAG_RATE_LIMIT_DOCS_PER_DAY=30
```

> `GEMINI_API_KEY` 주석은 "무료 tier" 언급이 있으면 "유료 pay-as-you-go 권장 (RPD 상한 없음)" 으로 정정.

- [ ] **Step 3: `README.md` 운영 모드 갱신**

`README.md` 의 "현재 운영 모드 — 데모 병행 (수익화 W1)" 섹션에 한 줄 추가:

```markdown
- **일일 rate limit (W2)**: 익명 데모(IP 기준)·로그인 사용자(user_id 기준) 모두 일일 답변 50회 / 업로드 30회 상한 — 초과 시 429. `JETRAG_RATE_LIMIT_*` ENV 로 조정(0=무제한).
```

- [ ] **Step 4: 회귀 없음 확인 (문서·주석 변경이라 테스트 불변)**

Run: `cd api && uv run python -m unittest tests.test_gemini_retry_default -v`
Expected: PASS (retry default 동작 불변 검증)

- [ ] **Step 5: 커밋**

```bash
git add api/app/adapters/impl/_gemini_common.py .env.example README.md
git commit -m "docs(w2): Gemini 유료 키 전환 안내 + rate limit ENV 문서화"
```

---

## Task 7: production 롤아웃 (수동 — 사용자 액션 포함)

**Files:** 없음 (운영 절차). W1-T6 와 동일하게 문서화된 수동 단계.

> 이 태스크는 서브에이전트가 실행하지 않음. 컨트롤러가 사용자와 함께 수행.

- [ ] **Step 1: 마이그레이션 021 적용**

Supabase Studio(프로젝트 `mpmtydudhojpukuuadrd`) → SQL Editor → `api/migrations/021_usage_counters.sql` 전체 paste → Run. 이어서 파일 헤더의 검증 SQL 실행 (increment 2회 → count=2 확인 후 cleanup).

- [ ] **Step 2: Gemini 유료 키 교체 + rate limit ENV (선택)**

Railway → jetrag-api → Variables:
- `GEMINI_API_KEY` = 유료 pay-as-you-go 키로 교체 (기존 무료 키였다면).
- (선택) `JETRAG_RATE_LIMIT_ANSWERS_PER_DAY` / `JETRAG_RATE_LIMIT_DOCS_PER_DAY` — default(50/30)로 충분하면 미설정.
- 보라색 **Deploy** 버튼 클릭 (저장만으론 재배포 안 됨).

- [ ] **Step 3: `git push origin main` (백엔드 재배포 트리거)**

```bash
git push origin main
```

- [ ] **Step 4: production smoke**

```bash
# 익명 답변 1회 — 200 (또는 검색결과 없음 정상 응답)
curl -s -o /dev/null -w "answer:%{http_code}\n" "https://jetrag-api.woong-s.com/answer?q=테스트"
# usage_counters 에 ip:<...> answers row 1건 생겼는지 Supabase 에서 확인
```

- [ ] **Step 5: rate limit 실동작 확인 (선택, 신중)**

임시로 `JETRAG_RATE_LIMIT_ANSWERS_PER_DAY=2` 설정·Deploy 후 익명으로 `/answer` 3회 연속 호출 → 3번째 429 확인. 검증 후 원복(50 또는 미설정)·Deploy.

- [ ] **Step 6: 베타 유저 재초대**

업로드가 로그인+rate limit 하에 안전하게 개방됨 — 베타 안내문(`work-log/2026-05-28 베타테스터 안내문`) 재발송.

---

## Self-Review (플랜 작성자 체크리스트 결과)

**1. Spec coverage** (스펙 W2 = "Gemini 유료 키(pay-as-you-go) 전환 + per-user rate limit → 베타 유저 재초대"):
- Gemini 유료 키 전환 → Task 6(문서·주석) + Task 7 Step 2(ENV 교체). ✅
- per-user rate limit → Task 1(DB) + 2(config) + 3(서비스) + 4(answer) + 5(documents). ✅
- 베타 유저 재초대 → Task 7 Step 6. ✅
- 스펙 §1 usage_counters 재사용 방향 → Task 1 테이블이 W3-4 미터링 기반. ✅
- 트랙 B(이메일 도메인 MX)는 운영 서류라 코드 플랜 범위 밖 — 의도적 제외.

**2. Placeholder scan:** 모든 코드 스텝에 실제 코드·명령·기대 출력 포함. "확인 후 조정" 지시는 실제 라인(577-582 등)이 있는 곳에 한정 — 구현자가 Read 로 즉시 확정 가능. ✅

**3. Type consistency:** `enforce_rate_limit(metric, request, current_user, settings)` / `build_user_key(current_user, request)` / `check_rate_limit(metric)` / `_client_ip(request)` / RPC `increment_usage_counter(p_user_key, p_metric, p_period_date) RETURNS INTEGER` — 테스트·구현·SQL·라우터 전반에서 시그니처·이름·파라미터 일치. Settings 필드 `rate_limit_answers_per_day`/`rate_limit_docs_per_day` 도 config·서비스·테스트에서 동일. ✅

**주의 (구현자 확인 필요):**
- `/answer`·`/documents` 실제 마운트 prefix — `api/app/main.py` 의 include_router 로 확정 후 route 테스트 경로 조정.
- `upload_url` decorator(577-582) 기존 인자 — Read 후 `dependencies` 만 확장(기존 인자 보존).
- 라우터 레벨 `dependencies=[]` 와 body 검증(파일 파라미터)의 실행 순서 — 429 테스트는 유효 입력(q, 실제 multipart)을 주어 body 검증을 통과시키므로 순서와 무관하게 게이트가 fire 함.
