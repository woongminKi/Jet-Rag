# W5-6 카카오페이 정기결제 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pro 유저가 카카오페이 정기결제로 월 6,900원 구독을 등록·해지하고, 매일 배치가 만료 도래 구독을 자동결제하며 7일 grace 후 자동 해지하는 결제 시스템을 ship 한다.

**Architecture:** 기존 어댑터 5-part 패턴(`Protocol` + `impl/` + `factory`)을 계승해 `PaymentProvider`(KakaoPay open-api) 를 추가한다. 결제창→SID(빌링키) 발급은 `ready→approve` 2-hop redirect flow, SID 는 Fernet 대칭 암호화로 `subscriptions.billing_key` 에 저장한다. 월 자동결제는 얇은 스크립트(`scripts/billing_charge.py`, Railway cron)가 `billing.charge_due_subscriptions()` + `billing.sweep_past_due()` 를 호출한다. 상태 머신은 `active → (실패) past_due (7일 grace) → canceled`(Free 강등, 데이터 보존).

**Tech Stack:** Python 3.12 / FastAPI / Supabase(Postgres) / httpx(KakaoPay open-api) / cryptography.Fernet(SID 암호화) / Next.js 16 + React 19(구독 UI) / Railway cron(배치).

**테스트 실행:** `cd api && uv run python -m unittest tests.<module> -v`

**결정 이력(2026-07-07 세션 확정):**
1. CID 정책 — 개발/기본은 sandbox `TCSUBSCRIP`, production CID 는 심사 승인 후 ENV `JETRAG_KAKAOPAY_CID` 전환.
2. 가격 — 6,900원/월 (`plans.price_krw = 6900`, 마이그 022 seed 반영 완료).
3. 약관 — `/terms`, `/privacy` 정적 페이지 포함. **본문 초안은 사용자가 작성해 넘김**(플랜은 페이지 scaffold + 삽입 슬롯 제공).
4. `payment_history` 테이블 — 넣는다(배치 실패 이력·감사 로그).
5. past_due 기준 — 별도 `past_due_since TIMESTAMPTZ` 컬럼(`updated_at` 은 타 사유로도 갱신되어 모호).
6. Fernet key rotation — v1 단일 key, rotation 은 W7 이후.
7. 결제 실패 재시도 — 매일 재시도(7일 = 7회), 성공 시 즉시 active 복귀.

---

## File Structure

**신규 파일**
- `api/migrations/025_billing_subscription.sql` — subscriptions ALTER(pending_tid·past_due_since) + payment_history 테이블.
- `api/app/adapters/payment.py` — `PaymentProvider` Protocol + `ReadyResult`/`ApproveResult` dataclass + `PaymentError`.
- `api/app/adapters/impl/kakaopay.py` — KakaoPay open-api httpx 클라이언트(ready/approve/subscribe/inactivate).
- `api/app/adapters/payment_factory.py` — `get_payment_provider()` dispatch.
- `api/app/services/billing_crypto.py` — Fernet SID encrypt/decrypt.
- `api/app/services/billing.py` — start/approve/charge/sweep/cancel 서비스 로직.
- `api/app/routers/payments.py` — `/payments/subscribe/*` + `/billing/run` cron endpoint.
- `api/scripts/billing_charge.py` — Railway cron 진입점(얇은 wrapper).
- `web/src/components/jet-rag/subscription-section.tsx` — 구독 상태·구독/해지 버튼.
- `web/src/components/jet-rag/footer.tsx` — 전 페이지 공용 footer(약관 링크).
- `web/src/app/billing/success/page.tsx` + `web/src/app/billing/success/billing-approve.tsx` — approve 처리.
- `web/src/app/billing/fail/page.tsx` / `web/src/app/billing/cancel/page.tsx` — 실패·취소 안내.
- `web/src/app/terms/page.tsx` / `web/src/app/privacy/page.tsx` — 약관 정적 페이지.
- 테스트: `api/tests/test_billing_crypto.py`, `test_payment_adapter.py`, `test_billing_service.py`, `test_payments_routes.py`, `test_billing_cron_route.py`, `test_me_subscription.py` + `test_config.py` 확장.

**수정 파일**
- `api/app/config.py` — Settings 에 결제 ENV 6개 추가 + `get_settings()` 배선.
- `api/app/services/quota.py` — `SubscriptionView` + `get_subscription_view()` 추가.
- `api/app/routers/me.py` — `GET /me/subscription` 추가.
- `api/app/routers/__init__.py` + `api/app/main.py` — payments_router·billing_cron_router 등록.
- `web/src/app/settings/page.tsx` — `<SubscriptionSection />` 삽입.
- `web/src/app/layout.tsx` — `<Footer />` 삽입.
- `.env.example` + `README.md` — 새 ENV 블록 + 운영 가이드.

---

## Task 1: Migration 025 — 구독 결제 컬럼 + payment_history

**Files:**
- Create: `api/migrations/025_billing_subscription.sql`

정기결제가 subscriptions(마이그 022)를 재사용한다. `ready→approve` 사이에 `tid` 를 서버에 보관해야 하므로 `pending_tid` 를, 7일 grace 판정 기준으로 `past_due_since` 를 추가한다. 결제 이력은 `payment_history` 에 남긴다(감사·재시도 추적).

- [ ] **Step 1: 마이그레이션 SQL 작성**

```sql
-- ============================================================
-- 025_billing_subscription.sql — 수익화 W5-6 (카카오페이 정기결제)
-- ============================================================
-- 배경
--   마이그 022 subscriptions(user_id PK, plan_code, status, current_period_end,
--   billing_key, ...) 를 정기결제가 재사용한다. 본 마이그는 결제 flow 에 필요한
--   컬럼 2개를 추가하고, 결제 이력 테이블을 신설한다.
--
-- 추가 컬럼 (subscriptions)
--   - pending_tid  : ready→approve 사이 KakaoPay tid 보관 (approve 시 조회 후 clear).
--   - past_due_since: 결제 최초 실패 시각. 7일 grace sweep 판정 기준
--                     (updated_at 은 타 사유로도 갱신되어 모호 — 별도 컬럼).
--   - billing_key  : (기존) KakaoPay SID 의 Fernet 암호문. 평문 저장 금지.
--
-- payment_history
--   - 배치/승인/해지 이벤트 감사 로그. RLS 본인 SELECT + service_role full.
--
-- 적용 절차
--   Supabase Studio → SQL Editor → New query 빈 탭 → 본 파일 paste → Run.
--
-- 검증 SQL (적용 후)
--   SELECT column_name FROM information_schema.columns
--     WHERE table_name='subscriptions'
--       AND column_name IN ('pending_tid','past_due_since');   -- 2행
--   SELECT * FROM payment_history LIMIT 1;                     -- 빈 결과(에러 없음)
-- ============================================================

ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS pending_tid    TEXT,
    ADD COLUMN IF NOT EXISTS past_due_since TIMESTAMPTZ;

COMMENT ON COLUMN subscriptions.billing_key IS
    'KakaoPay SID(빌링키)의 Fernet 암호문. 평문 저장 금지 (app/services/billing_crypto.py).';

CREATE TABLE IF NOT EXISTS payment_history (
    id          BIGSERIAL PRIMARY KEY,
    user_id     UUID NOT NULL,
    event       TEXT NOT NULL
                CHECK (event IN ('subscribe', 'charge_success', 'charge_failed', 'cancel')),
    amount_krw  INTEGER,
    detail      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS payment_history_user_created_idx
    ON payment_history (user_id, created_at DESC);

ALTER TABLE payment_history ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS payment_history_select_own ON payment_history;
CREATE POLICY payment_history_select_own
    ON payment_history FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS payment_history_service_role_all ON payment_history;
CREATE POLICY payment_history_service_role_all
    ON payment_history FOR ALL
    TO service_role
    USING (TRUE) WITH CHECK (TRUE);

-- ============================================================
-- 끝. Python 연동은 app/services/billing.py (Task 7).
-- ============================================================
```

- [ ] **Step 2: 사용자에게 apply 요청**

이 마이그레이션은 사용자가 Supabase SQL Editor 에서 직접 실행한다(마이그 022~024 동일 절차 — SQL Editor 가 risk 최소). 실행 후 Step 1 하단 검증 SQL 로 컬럼 2개 + payment_history 존재 확인.

- [ ] **Step 3: Commit**

```bash
git add api/migrations/025_billing_subscription.sql
git commit -m "feat(migration-w5): 정기결제 컬럼(pending_tid·past_due_since) + payment_history"
```

---

## Task 2: Config ENV — 결제 설정 6개 필드

**Files:**
- Modify: `api/app/config.py:95-97` (email 필드 뒤에 추가), `api/app/config.py:214-215` (get_settings 배선)
- Test: `api/tests/test_config.py`

기존 `_parse_bool`/`_parse_int` + `@lru_cache get_settings()` 패턴 준수. 미설정(빈값)이면 결제 기능 비활성(라우터 503) — 무중단 graceful.

- [ ] **Step 1: 실패 테스트 작성**

`api/tests/test_config.py` 파일 하단에 아래 클래스를 추가한다.

```python
class KakaoPayConfigTest(unittest.TestCase):
    """수익화 W5-6 — 카카오페이 결제 ENV 파싱."""

    def test_defaults_when_unset(self) -> None:
        for key in (
            "JETRAG_PAYMENT_PROVIDER",
            "JETRAG_KAKAOPAY_SECRET_KEY",
            "JETRAG_KAKAOPAY_CID",
            "JETRAG_BILLING_KEY_ENCRYPTION_KEY",
            "JETRAG_BILLING_CRON_SECRET",
            "JETRAG_BILLING_REDIRECT_BASE",
        ):
            os.environ.pop(key, None)
        get_settings.cache_clear()
        s = get_settings()
        self.assertEqual(s.payment_provider, "kakaopay")
        self.assertEqual(s.kakaopay_secret_key, "")
        self.assertEqual(s.kakaopay_cid, "TCSUBSCRIP")
        self.assertEqual(s.billing_key_encryption_key, "")
        self.assertEqual(s.billing_cron_secret, "")
        self.assertEqual(s.billing_redirect_base, "https://jetrag.woong-s.com")

    def test_env_override(self) -> None:
        os.environ["JETRAG_KAKAOPAY_CID"] = "CID_PROD_1234"
        os.environ["JETRAG_KAKAOPAY_SECRET_KEY"] = "sk_test"
        get_settings.cache_clear()
        try:
            s = get_settings()
            self.assertEqual(s.kakaopay_cid, "CID_PROD_1234")
            self.assertEqual(s.kakaopay_secret_key, "sk_test")
        finally:
            os.environ.pop("JETRAG_KAKAOPAY_CID", None)
            os.environ.pop("JETRAG_KAKAOPAY_SECRET_KEY", None)
            get_settings.cache_clear()
```

파일 상단에 `import os` + `from app.config import get_settings` 가 이미 있는지 확인하고 없으면 추가한다(test_config.py 는 기존 EmailIngestSettingsTest 가 있어 이미 import 되어 있음).

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_config.KakaoPayConfigTest -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'payment_provider'`

- [ ] **Step 3: Settings 필드 추가**

`api/app/config.py` 의 `email_ingest_domain: str = "in.woong-s.com"` (line 97) 바로 뒤에 추가:

```python
    # 수익화 W5-6 (2026-07-07) — 카카오페이 정기결제.
    # 미설정(빈값) = 결제 기능 비활성 (payments 라우터 503). 무중단 graceful.
    # 미래 토스/Stripe swap 대비 provider 문자열(어댑터 factory 가 dispatch).
    payment_provider: str = "kakaopay"
    # KakaoPay open-api SECRET_KEY. Railway ENV. 심사 전 sandbox, 후 production 키.
    kakaopay_secret_key: str = ""
    # 정기결제 CID. default sandbox TCSUBSCRIP — 심사 승인 후 production CID 로 ENV 교체.
    kakaopay_cid: str = "TCSUBSCRIP"
    # SID(빌링키) Fernet 암호화 키 (Fernet.generate_key(), 32-byte urlsafe base64).
    # 빈값이면 billing_crypto 가 RuntimeError (평문 SID 저장 금지 — fail-fast).
    billing_key_encryption_key: str = ""
    # billing 배치 endpoint(POST /billing/run) 호출 gate. 빈값 = endpoint 503.
    billing_cron_secret: str = ""
    # 결제창 redirect base (프론트 도메인). approve/cancel/fail URL 조립.
    billing_redirect_base: str = "https://jetrag.woong-s.com"
```

- [ ] **Step 4: get_settings() 배선**

`api/app/config.py` 의 `email_ingest_domain=...` (line 215) 바로 뒤, `Settings(...)` 닫는 괄호 앞에 추가:

```python
        payment_provider=os.environ.get("JETRAG_PAYMENT_PROVIDER", "kakaopay"),
        kakaopay_secret_key=os.environ.get("JETRAG_KAKAOPAY_SECRET_KEY", ""),
        kakaopay_cid=os.environ.get("JETRAG_KAKAOPAY_CID", "TCSUBSCRIP"),
        billing_key_encryption_key=os.environ.get("JETRAG_BILLING_KEY_ENCRYPTION_KEY", ""),
        billing_cron_secret=os.environ.get("JETRAG_BILLING_CRON_SECRET", ""),
        billing_redirect_base=os.environ.get(
            "JETRAG_BILLING_REDIRECT_BASE", "https://jetrag.woong-s.com"
        ),
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_config.KakaoPayConfigTest -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add api/app/config.py api/tests/test_config.py
git commit -m "feat(config-w5): 카카오페이/Fernet/cron ENV 6개 + 파싱 테스트"
```

---

## Task 3: billing_crypto — Fernet SID 암호화

**Files:**
- Create: `api/app/services/billing_crypto.py`
- Test: `api/tests/test_billing_crypto.py`

`cryptography` 는 `pyjwt[crypto]` 로 이미 설치됨(pyproject.toml:26). SID 는 절대 평문 저장 금지 — encrypt 후 `subscriptions.billing_key` 에 저장, 결제 시 decrypt.

- [ ] **Step 1: 실패 테스트 작성**

```python
# api/tests/test_billing_crypto.py
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from cryptography.fernet import Fernet

from app.config import Settings, get_settings
from app.services import billing_crypto


def _settings_with_key(key: str) -> Settings:
    base = get_settings.__wrapped__  # 미사용 — 아래 직접 구성
    raise NotImplementedError


class BillingCryptoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.key = Fernet.generate_key().decode("utf-8")

    def _patched_settings(self):
        from app.config import get_settings as gs
        gs.cache_clear()
        return patch.dict(os.environ, {"JETRAG_BILLING_KEY_ENCRYPTION_KEY": self.key})

    def test_roundtrip(self) -> None:
        with self._patched_settings():
            get_settings.cache_clear()
            token = billing_crypto.encrypt_sid("S1234567890abcdef")
            self.assertNotEqual(token, "S1234567890abcdef")  # 암호문
            self.assertEqual(billing_crypto.decrypt_sid(token), "S1234567890abcdef")
        get_settings.cache_clear()

    def test_missing_key_raises(self) -> None:
        with patch.dict(os.environ, {"JETRAG_BILLING_KEY_ENCRYPTION_KEY": ""}):
            get_settings.cache_clear()
            with self.assertRaises(RuntimeError):
                billing_crypto.encrypt_sid("S123")
        get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
```

> 참고: 위 `_settings_with_key`/`base` 잔재는 제거하고 `_patched_settings` 만 사용한다(아래 최종본 기준). 정리 후 파일은 `_settings_with_key` 함수를 포함하지 않는다.

정리된 최종 테스트 파일:

```python
# api/tests/test_billing_crypto.py
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from cryptography.fernet import Fernet

from app.config import get_settings
from app.services import billing_crypto


class BillingCryptoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.key = Fernet.generate_key().decode("utf-8")

    def test_roundtrip(self) -> None:
        with patch.dict(os.environ, {"JETRAG_BILLING_KEY_ENCRYPTION_KEY": self.key}):
            get_settings.cache_clear()
            token = billing_crypto.encrypt_sid("S1234567890abcdef")
            self.assertNotEqual(token, "S1234567890abcdef")
            self.assertEqual(billing_crypto.decrypt_sid(token), "S1234567890abcdef")
        get_settings.cache_clear()

    def test_missing_key_raises(self) -> None:
        with patch.dict(os.environ, {"JETRAG_BILLING_KEY_ENCRYPTION_KEY": ""}):
            get_settings.cache_clear()
            with self.assertRaises(RuntimeError):
                billing_crypto.encrypt_sid("S123")
        get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_billing_crypto -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.billing_crypto'`

- [ ] **Step 3: 구현**

```python
# api/app/services/billing_crypto.py
"""수익화 W5-6 — 카카오페이 SID(빌링키) 대칭 암호화.

subscriptions.billing_key 에는 Fernet 암호문만 저장한다 (평문 SID 금지).
키 = ENV JETRAG_BILLING_KEY_ENCRYPTION_KEY (Fernet.generate_key() 결과).
v1 단일 key — rotation 은 W7 이후 (재암호화 배치 필요).
"""
from __future__ import annotations

from cryptography.fernet import Fernet

from app.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().billing_key_encryption_key
    if not key:
        raise RuntimeError(
            "JETRAG_BILLING_KEY_ENCRYPTION_KEY 미설정 — SID 암호화 불가. "
            "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' "
            "로 생성 후 ENV 설정 필요."
        )
    return Fernet(key.encode("utf-8"))


def encrypt_sid(sid: str) -> str:
    """SID 평문 → Fernet 암호문(str)."""
    return _fernet().encrypt(sid.encode("utf-8")).decode("utf-8")


def decrypt_sid(token: str) -> str:
    """Fernet 암호문 → SID 평문(str)."""
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_billing_crypto -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add api/app/services/billing_crypto.py api/tests/test_billing_crypto.py
git commit -m "feat(billing-w5): SID Fernet 암호화 유틸 + roundtrip 테스트"
```

---

## Task 4: PaymentProvider Protocol + 결과 타입

**Files:**
- Create: `api/app/adapters/payment.py`
- Test: `api/tests/test_payment_adapter.py` (Task 5 와 공유 — 이 태스크에서 파일 생성)

기존 `llm.py` 의 `LLMProvider(Protocol)` 패턴 계승. 결과는 frozen dataclass, 실패는 `PaymentError`.

- [ ] **Step 1: 실패 테스트 작성**

```python
# api/tests/test_payment_adapter.py
from __future__ import annotations

import os
import unittest

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from app.adapters.payment import ApproveResult, PaymentError, ReadyResult


class PaymentTypesTest(unittest.TestCase):
    def test_ready_result_fields(self) -> None:
        r = ReadyResult(tid="T1", redirect_url="https://k/pay")
        self.assertEqual(r.tid, "T1")
        self.assertEqual(r.redirect_url, "https://k/pay")

    def test_approve_result_fields(self) -> None:
        a = ApproveResult(sid="S1", tid="T1")
        self.assertEqual(a.sid, "S1")

    def test_payment_error_is_exception(self) -> None:
        self.assertTrue(issubclass(PaymentError, Exception))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_payment_adapter -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.adapters.payment'`

- [ ] **Step 3: 구현**

```python
# api/app/adapters/payment.py
"""수익화 W5-6 — 결제 공급자 Protocol (KakaoPay 기본, 토스/Stripe swap 대비).

기존 LLMProvider/VisionCaptioner 와 동일한 5-part 어댑터 패턴:
Protocol(본 파일) + impl/kakaopay.py + payment_factory.get_payment_provider().
호출처는 impl 을 직접 import 하지 말고 factory 로 받는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class PaymentError(Exception):
    """결제 공급자 호출 실패 (비 2xx 응답·네트워크·응답 형식 오류).

    배치(charge)는 이 예외를 잡아 해당 유저를 past_due 로 판정하고 다음 유저로 진행한다.
    """


@dataclass(frozen=True)
class ReadyResult:
    """결제창 준비 결과 — tid(승인 시 필요) + 사용자 redirect URL."""

    tid: str
    redirect_url: str


@dataclass(frozen=True)
class ApproveResult:
    """결제 승인 결과 — 정기결제 SID(빌링키) + tid."""

    sid: str
    tid: str


class PaymentProvider(Protocol):
    """정기결제 공급자. 4 메소드: ready→approve(등록) / subscribe(월결제) / inactivate(해지)."""

    def ready(
        self,
        *,
        partner_order_id: str,
        partner_user_id: str,
        approval_url: str,
        cancel_url: str,
        fail_url: str,
    ) -> ReadyResult: ...

    def approve(
        self,
        *,
        tid: str,
        partner_order_id: str,
        partner_user_id: str,
        pg_token: str,
    ) -> ApproveResult: ...

    def subscribe(
        self,
        *,
        sid: str,
        partner_order_id: str,
        partner_user_id: str,
    ) -> None: ...

    def inactivate(self, *, sid: str) -> None: ...
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_payment_adapter -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add api/app/adapters/payment.py api/tests/test_payment_adapter.py
git commit -m "feat(payment-w5): PaymentProvider Protocol + 결과 타입/에러"
```

---

## Task 5: KakaoPayImpl 어댑터

**Files:**
- Create: `api/app/adapters/impl/kakaopay.py`
- Test: `api/tests/test_payment_adapter.py` (Task 4 파일에 클래스 추가)

`httpx.Client` 는 이미 사용 중(bgem3_hf_embedding.py:73). KakaoPay open-api: Base `https://open-api.kakaopay.com`, Auth `Authorization: SECRET_KEY {secret}`.

- [ ] **Step 1: 실패 테스트 작성 (test_payment_adapter.py 에 추가)**

```python
from unittest.mock import MagicMock, patch

from app.adapters.impl.kakaopay import KakaoPayImpl


def _resp(status_code: int, body: dict) -> MagicMock:
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = body
    m.text = str(body)
    return m


class KakaoPayImplTest(unittest.TestCase):
    def _impl(self) -> KakaoPayImpl:
        return KakaoPayImpl(secret_key="sk_test", cid="TCSUBSCRIP")

    def test_ready_returns_tid_and_redirect(self) -> None:
        with patch("app.adapters.impl.kakaopay.httpx.Client") as MockClient:
            client = MockClient.return_value.__enter__.return_value
            client.post.return_value = _resp(200, {
                "tid": "T123",
                "next_redirect_pc_url": "https://kakao/pc",
                "next_redirect_mobile_url": "https://kakao/m",
            })
            result = self._impl().ready(
                partner_order_id="u1", partner_user_id="u1",
                approval_url="https://a", cancel_url="https://c", fail_url="https://f",
            )
        self.assertEqual(result.tid, "T123")
        self.assertEqual(result.redirect_url, "https://kakao/pc")
        # Auth 헤더 검증
        _, kwargs = client.post.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "SECRET_KEY sk_test")
        self.assertEqual(kwargs["json"]["cid"], "TCSUBSCRIP")
        self.assertEqual(kwargs["json"]["total_amount"], 6900)

    def test_approve_returns_sid(self) -> None:
        with patch("app.adapters.impl.kakaopay.httpx.Client") as MockClient:
            client = MockClient.return_value.__enter__.return_value
            client.post.return_value = _resp(200, {"sid": "S999", "tid": "T123"})
            result = self._impl().approve(
                tid="T123", partner_order_id="u1", partner_user_id="u1", pg_token="pg",
            )
        self.assertEqual(result.sid, "S999")

    def test_approve_missing_sid_raises(self) -> None:
        with patch("app.adapters.impl.kakaopay.httpx.Client") as MockClient:
            client = MockClient.return_value.__enter__.return_value
            client.post.return_value = _resp(200, {"tid": "T123"})  # sid 없음
            with self.assertRaises(PaymentError):
                self._impl().approve(
                    tid="T123", partner_order_id="u1", partner_user_id="u1", pg_token="pg",
                )

    def test_non_2xx_raises_payment_error(self) -> None:
        with patch("app.adapters.impl.kakaopay.httpx.Client") as MockClient:
            client = MockClient.return_value.__enter__.return_value
            client.post.return_value = _resp(400, {"error_code": -780})
            with self.assertRaises(PaymentError):
                self._impl().subscribe(sid="S1", partner_order_id="u1", partner_user_id="u1")

    def test_empty_secret_key_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            KakaoPayImpl(secret_key="", cid="TCSUBSCRIP")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_payment_adapter.KakaoPayImplTest -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.adapters.impl.kakaopay'`

- [ ] **Step 3: 구현**

```python
# api/app/adapters/impl/kakaopay.py
"""수익화 W5-6 — KakaoPay open-api 정기결제 어댑터.

Base: https://open-api.kakaopay.com
Auth: Authorization: SECRET_KEY {secret}
flow: ready → approve(sid 발급) → subscription(월 배치) / inactive(해지).
sandbox CID = TCSUBSCRIP (정기결제 테스트). production CID 는 심사 후 ENV 교체.
"""
from __future__ import annotations

import logging

import httpx

from app.adapters.payment import ApproveResult, PaymentError, ReadyResult

logger = logging.getLogger(__name__)

_BASE_URL = "https://open-api.kakaopay.com"
_ITEM_NAME = "Jet-Rag Pro 구독"
_TOTAL_AMOUNT = 6900  # plans.price_krw=6900 과 정합 (결정 이력 #2)
_TIMEOUT = 15.0


class KakaoPayImpl:
    """KakaoPay open-api 정기결제 클라이언트. PaymentProvider Protocol 구현."""

    def __init__(self, *, secret_key: str, cid: str, base_url: str = _BASE_URL) -> None:
        if not secret_key:
            raise RuntimeError(
                "KakaoPay secret_key 미설정 — JETRAG_KAKAOPAY_SECRET_KEY 필요."
            )
        self._cid = cid
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"SECRET_KEY {secret_key}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, body: dict) -> dict:
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.post(
                    f"{self._base_url}{path}", headers=self._headers, json=body
                )
        except httpx.HTTPError as exc:
            raise PaymentError(f"KakaoPay 네트워크 오류 ({path}): {exc}") from exc
        if resp.status_code >= 400:
            raise PaymentError(
                f"KakaoPay {resp.status_code} ({path}): {resp.text[:200]}"
            )
        return resp.json()

    def ready(
        self, *, partner_order_id, partner_user_id, approval_url, cancel_url, fail_url
    ) -> ReadyResult:
        data = self._post(
            "/online/v1/payment/ready",
            {
                "cid": self._cid,
                "partner_order_id": partner_order_id,
                "partner_user_id": partner_user_id,
                "item_name": _ITEM_NAME,
                "quantity": 1,
                "total_amount": _TOTAL_AMOUNT,
                "tax_free_amount": 0,
                "approval_url": approval_url,
                "cancel_url": cancel_url,
                "fail_url": fail_url,
            },
        )
        redirect = data.get("next_redirect_pc_url") or data.get("next_redirect_mobile_url")
        if not data.get("tid") or not redirect:
            raise PaymentError(f"KakaoPay ready 응답 불완전: keys={list(data.keys())}")
        return ReadyResult(tid=data["tid"], redirect_url=redirect)

    def approve(
        self, *, tid, partner_order_id, partner_user_id, pg_token
    ) -> ApproveResult:
        data = self._post(
            "/online/v1/payment/approve",
            {
                "cid": self._cid,
                "tid": tid,
                "partner_order_id": partner_order_id,
                "partner_user_id": partner_user_id,
                "pg_token": pg_token,
            },
        )
        sid = data.get("sid")
        if not sid:
            raise PaymentError(
                "KakaoPay approve 응답에 sid 없음 — 정기결제 CID(TCSUBSCRIP 계열) 확인 필요."
            )
        return ApproveResult(sid=sid, tid=tid)

    def subscribe(self, *, sid, partner_order_id, partner_user_id) -> None:
        self._post(
            "/online/v1/payment/subscription",
            {
                "cid": self._cid,
                "sid": sid,
                "partner_order_id": partner_order_id,
                "partner_user_id": partner_user_id,
                "item_name": _ITEM_NAME,
                "quantity": 1,
                "total_amount": _TOTAL_AMOUNT,
                "tax_free_amount": 0,
            },
        )

    def inactivate(self, *, sid) -> None:
        self._post(
            "/online/v1/payment/manage/subscription/inactive",
            {"cid": self._cid, "sid": sid},
        )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_payment_adapter -v`
Expected: PASS (전체 클래스)

- [ ] **Step 5: Commit**

```bash
git add api/app/adapters/impl/kakaopay.py api/tests/test_payment_adapter.py
git commit -m "feat(payment-w5): KakaoPay open-api 어댑터(ready/approve/subscribe/inactive)"
```

---

## Task 6: payment_factory — get_payment_provider()

**Files:**
- Create: `api/app/adapters/payment_factory.py`
- Test: `api/tests/test_payment_adapter.py` (클래스 추가)

`factory.py` 의 `_resolve_provider()` + lazy import 패턴 계승.

- [ ] **Step 1: 실패 테스트 작성 (test_payment_adapter.py 에 추가)**

```python
from app.adapters.impl.kakaopay import KakaoPayImpl as _KPImpl
from app.adapters.payment_factory import get_payment_provider
from app.config import get_settings


class PaymentFactoryTest(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_kakaopay_default(self) -> None:
        with patch.dict(os.environ, {
            "JETRAG_PAYMENT_PROVIDER": "kakaopay",
            "JETRAG_KAKAOPAY_SECRET_KEY": "sk_test",
        }):
            get_settings.cache_clear()
            provider = get_payment_provider()
        self.assertIsInstance(provider, _KPImpl)

    def test_unknown_provider_raises(self) -> None:
        with patch.dict(os.environ, {"JETRAG_PAYMENT_PROVIDER": "bogus"}):
            get_settings.cache_clear()
            with self.assertRaises(ValueError):
                get_payment_provider()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_payment_adapter.PaymentFactoryTest -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.adapters.payment_factory'`

- [ ] **Step 3: 구현**

```python
# api/app/adapters/payment_factory.py
"""수익화 W5-6 — PaymentProvider 팩토리. ENV JETRAG_PAYMENT_PROVIDER (default kakaopay).

LLM factory(app/adapters/factory.py) 와 동일 — lazy import 로 impl/httpx 로딩을
호출 시점으로 미룬다(단위 테스트가 불필요한 import 비용 회피).
"""
from __future__ import annotations

from app.adapters.payment import PaymentProvider
from app.config import get_settings


def get_payment_provider() -> PaymentProvider:
    settings = get_settings()
    provider = (settings.payment_provider or "kakaopay").strip().lower()
    if provider == "kakaopay":
        from app.adapters.impl.kakaopay import KakaoPayImpl

        return KakaoPayImpl(
            secret_key=settings.kakaopay_secret_key,
            cid=settings.kakaopay_cid,
        )
    raise ValueError(f"알 수 없는 결제 provider: {provider!r}")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_payment_adapter.PaymentFactoryTest -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add api/app/adapters/payment_factory.py api/tests/test_payment_adapter.py
git commit -m "feat(payment-w5): get_payment_provider 팩토리 dispatch"
```

---

## Task 7: billing 서비스 — 구독 lifecycle + 배치

**Files:**
- Create: `api/app/services/billing.py`
- Test: `api/tests/test_billing_service.py`

핵심 로직. provider(factory) + supabase(get_supabase_client) + billing_crypto 를 조합한다. 배치는 유저별 격리(1건 실패가 전체 중단 X). 월 이동은 stdlib `calendar` 로 정확히 계산.

- [ ] **Step 1: 실패 테스트 작성**

```python
# api/tests/test_billing_service.py
from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from app.adapters.payment import ApproveResult, PaymentError, ReadyResult
from app.services import billing


def _sb_select_returning(rows: list[dict]) -> MagicMock:
    """table().select()...execute().data = rows 형태 mock 빌더."""
    sb = MagicMock()
    chain = sb.table.return_value
    # select/eq/lte/in_/limit 등 어떤 체인도 self 반환하도록
    for attr in ("select", "eq", "lte", "in_", "order", "limit", "update", "insert", "upsert"):
        getattr(chain, attr).return_value = chain
    chain.execute.return_value.data = rows
    return sb


class StartSubscriptionTest(unittest.TestCase):
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_start_calls_ready_and_stores_pending_tid(self, mock_sb, mock_provider) -> None:
        provider = mock_provider.return_value
        provider.ready.return_value = ReadyResult(tid="T1", redirect_url="https://k/pay")
        sb = _sb_select_returning([])
        mock_sb.return_value = sb
        with patch("app.config.get_settings") as _:
            result = billing.start_subscription("u1")
        self.assertEqual(result.redirect_url, "https://k/pay")
        provider.ready.assert_called_once()
        sb.table.assert_any_call("subscriptions")


class ApproveSubscriptionTest(unittest.TestCase):
    @patch("app.services.billing.encrypt_sid", return_value="ENC(S1)")
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_approve_stores_encrypted_sid_and_activates(
        self, mock_sb, mock_provider, _enc
    ) -> None:
        provider = mock_provider.return_value
        provider.approve.return_value = ApproveResult(sid="S1", tid="T1")
        sb = _sb_select_returning([{"pending_tid": "T1"}])
        mock_sb.return_value = sb
        billing.approve_subscription("u1", "pg_token_x")
        provider.approve.assert_called_once()
        # billing_key 에 암호문이 들어간 update 가 호출됐는지
        update_calls = [c.args[0] for c in sb.table.return_value.update.call_args_list]
        self.assertTrue(any(d.get("billing_key") == "ENC(S1)" for d in update_calls))
        self.assertTrue(any(d.get("status") == "active" for d in update_calls))

    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_approve_without_pending_tid_raises(self, mock_sb, mock_provider) -> None:
        sb = _sb_select_returning([{"pending_tid": None}])
        mock_sb.return_value = sb
        with self.assertRaises(billing.SubscriptionNotPendingError):
            billing.approve_subscription("u1", "pg")


class ChargeDueTest(unittest.TestCase):
    @patch("app.services.billing.decrypt_sid", return_value="S1")
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_charge_success_advances_period(self, mock_sb, mock_provider, _dec) -> None:
        due = [{
            "user_id": "u1", "billing_key": "ENC", "status": "active",
            "current_period_end": "2026-07-01T00:00:00+00:00", "past_due_since": None,
        }]
        sb = _sb_select_returning(due)
        mock_sb.return_value = sb
        report = billing.charge_due_subscriptions(now=datetime(2026, 7, 7, tzinfo=timezone.utc))
        self.assertEqual(report.charged, 1)
        self.assertEqual(report.failed, 0)
        mock_provider.return_value.subscribe.assert_called_once()

    @patch("app.services.billing.decrypt_sid", return_value="S1")
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_charge_failure_sets_past_due(self, mock_sb, mock_provider, _dec) -> None:
        mock_provider.return_value.subscribe.side_effect = PaymentError("declined")
        due = [{
            "user_id": "u1", "billing_key": "ENC", "status": "active",
            "current_period_end": "2026-07-01T00:00:00+00:00", "past_due_since": None,
        }]
        sb = _sb_select_returning(due)
        mock_sb.return_value = sb
        report = billing.charge_due_subscriptions(now=datetime(2026, 7, 7, tzinfo=timezone.utc))
        self.assertEqual(report.charged, 0)
        self.assertEqual(report.failed, 1)


class SweepPastDueTest(unittest.TestCase):
    @patch("app.services.billing.decrypt_sid", return_value="S1")
    @patch("app.services.billing.get_payment_provider")
    @patch("app.services.billing.get_supabase_client")
    def test_sweep_cancels_after_grace(self, mock_sb, mock_provider, _dec) -> None:
        overdue = [{"user_id": "u1", "billing_key": "ENC",
                    "past_due_since": "2026-06-25T00:00:00+00:00"}]
        sb = _sb_select_returning(overdue)
        mock_sb.return_value = sb
        report = billing.sweep_past_due(now=datetime(2026, 7, 7, tzinfo=timezone.utc))
        self.assertEqual(report.canceled, 1)
        mock_provider.return_value.inactivate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_billing_service -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.billing'`

- [ ] **Step 3: 구현**

```python
# api/app/services/billing.py
"""수익화 W5-6 — 카카오페이 정기결제 서비스 로직.

lifecycle: start_subscription(ready) → approve_subscription(SID 저장·active)
배치: charge_due_subscriptions(만료 자동결제) + sweep_past_due(7일 grace 후 canceled)
해지: cancel_subscription(KakaoPay inactive + canceled)

상태 머신: active → (결제 실패) past_due (7일 grace) → canceled (Free 강등, 데이터 보존).
past_due 구독도 매일 재시도(current_period_end<=now) — 성공 시 즉시 active 복귀.
어떤 배치도 유저별 격리 — 1건 실패가 나머지 처리를 막지 않는다.
"""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.adapters.payment import PaymentError
from app.adapters.payment_factory import get_payment_provider
from app.config import get_settings
from app.db import get_supabase_client
from app.services.billing_crypto import decrypt_sid, encrypt_sid

logger = logging.getLogger(__name__)

_PRICE_KRW = 6900
_GRACE_DAYS = 7  # 결제 실패 후 canceled 까지 grace (결정 이력 #7)


class SubscriptionNotPendingError(Exception):
    """approve 호출인데 pending_tid 가 없음 (ready 미선행/중복 승인)."""


@dataclass(frozen=True)
class ChargeReport:
    charged: int
    failed: int
    user_ids_charged: list[str]
    user_ids_failed: list[str]


@dataclass(frozen=True)
class SweepReport:
    canceled: int
    user_ids: list[str]


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _add_one_month(dt: datetime) -> datetime:
    """월 1회 결제 주기 — 말일 clamp (stdlib only, dateutil 의존 회피)."""
    year = dt.year + (dt.month // 12)
    month = dt.month % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _log_history(user_id: str, event: str, *, detail: str = "") -> None:
    """payment_history 기록 (best-effort — 실패해도 결제 흐름 막지 않음)."""
    try:
        get_supabase_client().table("payment_history").insert(
            {
                "user_id": user_id,
                "event": event,
                "amount_krw": _PRICE_KRW if event in ("subscribe", "charge_success") else None,
                "detail": detail[:500] or None,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("payment_history 기록 실패 (user=%s, event=%s): %s", user_id, event, exc)


def _redirect_urls(user_id: str) -> tuple[str, str, str]:
    base = get_settings().billing_redirect_base.rstrip("/")
    # approval_url 에 KakaoPay 가 ?pg_token=... 을 append 한다. tid 는 서버(pending_tid)에서 조회.
    return (
        f"{base}/billing/success",
        f"{base}/billing/cancel",
        f"{base}/billing/fail",
    )


def start_subscription(user_id: str):
    """결제창 준비 — ready 호출 후 tid 를 subscriptions.pending_tid 에 보관.

    반환: ReadyResult (redirect_url 로 사용자를 KakaoPay 결제창으로 보낸다).
    """
    approval_url, cancel_url, fail_url = _redirect_urls(user_id)
    provider = get_payment_provider()
    result = provider.ready(
        partner_order_id=user_id,
        partner_user_id=user_id,
        approval_url=approval_url,
        cancel_url=cancel_url,
        fail_url=fail_url,
    )
    client = get_supabase_client()
    client.table("subscriptions").upsert(
        {
            "user_id": user_id,
            "plan_code": "pro",
            "status": "canceled",  # approve 완료 전까지 미활성 (기존 free 유지 의미)
            "pending_tid": result.tid,
            "updated_at": _now(None).isoformat(),
        },
        on_conflict="user_id",
    ).execute()
    return result


def approve_subscription(user_id: str, pg_token: str) -> None:
    """결제 승인 — pending_tid 로 approve 호출 → SID 암호화 저장 + active 전환."""
    client = get_supabase_client()
    rows = (
        client.table("subscriptions")
        .select("pending_tid")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    ) or []
    tid = rows[0].get("pending_tid") if rows else None
    if not tid:
        raise SubscriptionNotPendingError(f"진행 중인 결제 요청 없음 (user={user_id})")

    provider = get_payment_provider()
    approved = provider.approve(
        tid=tid,
        partner_order_id=user_id,
        partner_user_id=user_id,
        pg_token=pg_token,
    )
    period_end = _add_one_month(_now(None))
    client.table("subscriptions").update(
        {
            "plan_code": "pro",
            "status": "active",
            "billing_key": encrypt_sid(approved.sid),
            "current_period_end": period_end.isoformat(),
            "pending_tid": None,
            "past_due_since": None,
            "updated_at": _now(None).isoformat(),
        }
    ).eq("user_id", user_id).execute()
    _log_history(user_id, "subscribe", detail="구독 등록 완료")


def charge_due_subscriptions(now: datetime | None = None) -> ChargeReport:
    """만료 도래(current_period_end<=now) active/past_due 구독 자동결제.

    성공 → +1개월·past_due 해제·active. 실패 → past_due·past_due_since(최초만) 기록.
    """
    at = _now(now)
    client = get_supabase_client()
    due = (
        client.table("subscriptions")
        .select("user_id, billing_key, status, current_period_end, past_due_since")
        .in_("status", ["active", "past_due"])
        .lte("current_period_end", at.isoformat())
        .execute()
        .data
    ) or []

    charged: list[str] = []
    failed: list[str] = []
    provider = get_payment_provider()
    for row in due:
        user_id = str(row["user_id"])
        enc = row.get("billing_key")
        if not enc:
            logger.warning("billing_key 없음 — skip (user=%s)", user_id)
            continue
        try:
            sid = decrypt_sid(enc)
            provider.subscribe(
                sid=sid,
                partner_order_id=f"{user_id}-{at.strftime('%Y%m%d')}",
                partner_user_id=user_id,
            )
        except (PaymentError, Exception) as exc:  # noqa: BLE001 — 유저 격리
            failed.append(user_id)
            update = {"status": "past_due", "updated_at": at.isoformat()}
            if not row.get("past_due_since"):
                update["past_due_since"] = at.isoformat()
            client.table("subscriptions").update(update).eq("user_id", user_id).execute()
            _log_history(user_id, "charge_failed", detail=str(exc)[:400])
            continue
        new_end = _add_one_month(_parse_ts(row.get("current_period_end")) or at)
        client.table("subscriptions").update(
            {
                "status": "active",
                "current_period_end": new_end.isoformat(),
                "past_due_since": None,
                "updated_at": at.isoformat(),
            }
        ).eq("user_id", user_id).execute()
        charged.append(user_id)
        _log_history(user_id, "charge_success", detail="월 정기결제 성공")

    logger.info("billing charge — 성공 %d, 실패 %d", len(charged), len(failed))
    return ChargeReport(
        charged=len(charged), failed=len(failed),
        user_ids_charged=charged, user_ids_failed=failed,
    )


def sweep_past_due(now: datetime | None = None) -> SweepReport:
    """past_due_since 가 7일 초과한 구독 → canceled (Free 강등). SID inactive 처리."""
    at = _now(now)
    threshold = (at - timedelta(days=_GRACE_DAYS)).isoformat()
    client = get_supabase_client()
    overdue = (
        client.table("subscriptions")
        .select("user_id, billing_key, past_due_since")
        .eq("status", "past_due")
        .lte("past_due_since", threshold)
        .execute()
        .data
    ) or []

    canceled: list[str] = []
    provider = get_payment_provider()
    for row in overdue:
        user_id = str(row["user_id"])
        enc = row.get("billing_key")
        if enc:
            try:
                provider.inactivate(sid=decrypt_sid(enc))
            except Exception as exc:  # noqa: BLE001 — inactive 실패해도 로컬 canceled 진행
                logger.warning("SID inactive 실패 (user=%s): %s", user_id, exc)
        client.table("subscriptions").update(
            {"status": "canceled", "updated_at": at.isoformat()}
        ).eq("user_id", user_id).execute()
        canceled.append(user_id)
        _log_history(user_id, "cancel", detail="7일 grace 초과 자동 해지")

    logger.info("billing sweep — %d건 canceled", len(canceled))
    return SweepReport(canceled=len(canceled), user_ids=canceled)


def cancel_subscription(user_id: str) -> None:
    """사용자 요청 해지 — KakaoPay inactive + status canceled (즉시 Free, 데이터 보존)."""
    client = get_supabase_client()
    rows = (
        client.table("subscriptions")
        .select("billing_key")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    ) or []
    enc = rows[0].get("billing_key") if rows else None
    if enc:
        try:
            get_payment_provider().inactivate(sid=decrypt_sid(enc))
        except Exception as exc:  # noqa: BLE001 — 원격 실패해도 로컬 해지 진행
            logger.warning("해지 시 SID inactive 실패 (user=%s): %s", user_id, exc)
    client.table("subscriptions").update(
        {"status": "canceled", "updated_at": _now(None).isoformat()}
    ).eq("user_id", user_id).execute()
    _log_history(user_id, "cancel", detail="사용자 요청 해지")
```

> **주의:** `except (PaymentError, Exception)` 는 사실상 `except Exception` 과 동일하다(Exception 이 PaymentError 를 포함). 의도는 "결제 관련이든 예기치 못한 오류든 유저 격리"이므로 `except Exception` 로 써도 무방하나, 가독성상 명시. 린트가 중복 경고하면 `except Exception` 로 축약할 것.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_billing_service -v`
Expected: PASS (전체)

- [ ] **Step 5: Commit**

```bash
git add api/app/services/billing.py api/tests/test_billing_service.py
git commit -m "feat(billing-w5): 구독 lifecycle + 자동결제/grace sweep 배치"
```

---

## Task 8: payments 라우터 + cron endpoint

**Files:**
- Create: `api/app/routers/payments.py`
- Modify: `api/app/routers/__init__.py`, `api/app/main.py:158-168`
- Test: `api/tests/test_payments_routes.py`, `api/tests/test_billing_cron_route.py`

`/payments/*` 는 `require_authenticated_user` 라우터 게이트(me.py 패턴). cron endpoint `/billing/run` 은 **admin 라우터에 두지 않는다** — `require_admin` 게이트가 owner JWT 없는 cron 호출자를 403 하기 때문. 별도 라우터 + shared secret(email_ingest 패턴)로 gate.

- [ ] **Step 1: 실패 테스트 작성 (payments 라우터)**

```python
# api/tests/test_payments_routes.py
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from fastapi.testclient import TestClient

from app.adapters.payment import ReadyResult
from app.auth.dependencies import CurrentUser, get_current_user
from app.config import Settings, get_settings
from app.main import app


def _settings(**over) -> Settings:
    base = dict(
        supabase_url="https://x.supabase.co", supabase_key="", supabase_service_role_key="svc",
        supabase_storage_bucket="documents", gemini_api_key="", hf_api_token="",
        default_user_id="00000000-0000-0000-0000-000000000001",
        doc_budget_usd=0.1, daily_budget_usd=0.5, sliding_24h_budget_usd=0.5,
        budget_krw_per_usd=1380.0, vision_need_score_enabled=True, vision_page_cap_per_doc=50,
        auth_enabled=True, owner_user_id="00000000-0000-0000-0000-0000000000ff",
        kakaopay_secret_key="sk_test", billing_key_encryption_key="k",
    )
    base.update(over)
    return Settings(**base)


class PaymentsRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.authed = CurrentUser(user_id="uid-1", is_authenticated=True)
        app.dependency_overrides[get_current_user] = lambda: self.authed
        app.dependency_overrides[get_settings] = lambda: _settings()
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_ready_returns_redirect_url(self) -> None:
        with patch("app.routers.payments.billing.start_subscription",
                   return_value=ReadyResult(tid="T1", redirect_url="https://k/pay")):
            resp = self.client.post("/payments/subscribe/ready")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["redirect_url"], "https://k/pay")

    def test_ready_503_when_disabled(self) -> None:
        app.dependency_overrides[get_settings] = lambda: _settings(kakaopay_secret_key="")
        resp = self.client.post("/payments/subscribe/ready")
        self.assertEqual(resp.status_code, 503)

    def test_approve_ok(self) -> None:
        with patch("app.routers.payments.billing.approve_subscription") as m:
            resp = self.client.post("/payments/subscribe/approve?pg_token=pg_x")
        self.assertEqual(resp.status_code, 200)
        m.assert_called_once_with("uid-1", "pg_x")

    def test_approve_requires_pg_token(self) -> None:
        resp = self.client.post("/payments/subscribe/approve")
        self.assertEqual(resp.status_code, 422)  # query 필수

    def test_cancel_ok(self) -> None:
        with patch("app.routers.payments.billing.cancel_subscription") as m:
            resp = self.client.post("/payments/subscribe/cancel")
        self.assertEqual(resp.status_code, 200)
        m.assert_called_once_with("uid-1")

    def test_anonymous_blocked(self) -> None:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="uid-1", is_authenticated=False
        )
        resp = self.client.post("/payments/subscribe/cancel")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패 테스트 작성 (cron endpoint)**

```python
# api/tests/test_billing_cron_route.py
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.services.billing import ChargeReport, SweepReport


def _settings(**over) -> Settings:
    base = dict(
        supabase_url="https://x.supabase.co", supabase_key="", supabase_service_role_key="svc",
        supabase_storage_bucket="documents", gemini_api_key="", hf_api_token="",
        default_user_id="00000000-0000-0000-0000-000000000001",
        doc_budget_usd=0.1, daily_budget_usd=0.5, sliding_24h_budget_usd=0.5,
        budget_krw_per_usd=1380.0, vision_need_score_enabled=True, vision_page_cap_per_doc=50,
        billing_cron_secret="cron_secret_x",
    )
    base.update(over)
    return Settings(**base)


class BillingCronRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[get_settings] = lambda: _settings()
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_run_ok_with_secret(self) -> None:
        with patch("app.routers.payments.billing.charge_due_subscriptions",
                   return_value=ChargeReport(1, 0, ["u1"], [])), \
             patch("app.routers.payments.billing.sweep_past_due",
                   return_value=SweepReport(0, [])):
            resp = self.client.post("/billing/run", headers={"X-Billing-Cron-Secret": "cron_secret_x"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"charged": 1, "failed": 0, "canceled": 0})

    def test_run_401_wrong_secret(self) -> None:
        resp = self.client.post("/billing/run", headers={"X-Billing-Cron-Secret": "wrong"})
        self.assertEqual(resp.status_code, 401)

    def test_run_503_when_secret_unset(self) -> None:
        app.dependency_overrides[get_settings] = lambda: _settings(billing_cron_secret="")
        resp = self.client.post("/billing/run", headers={"X-Billing-Cron-Secret": "x"})
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_payments_routes tests.test_billing_cron_route -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.routers.payments'`

- [ ] **Step 4: payments 라우터 구현**

```python
# api/app/routers/payments.py
"""수익화 W5-6 — 카카오페이 정기결제 라우터.

- /payments/subscribe/* : 로그인 유저 구독 등록·승인·해지 (require_authenticated_user 게이트).
- /billing/run          : 배치 진입점(외부 cron fallback). shared secret gate — admin 라우터에
                          두지 않는 이유는 require_admin 이 owner JWT 없는 cron 호출자를 403 하기 때문.
                          주 경로는 scripts/billing_charge.py (Railway cron). 이 endpoint 는 수동/외부 트리거.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel

from app.auth import CurrentUserDep, LEGACY_DEFAULT_USER, require_authenticated_user
from app.config import Settings, get_settings
from app.services import billing

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/payments",
    tags=["payments"],
    dependencies=[Depends(require_authenticated_user)],
)

# cron endpoint — 별도 라우터(인증 dependency 없음, secret gate 로만 보호).
cron_router = APIRouter(prefix="/billing", tags=["billing-cron"])


class ReadyResponse(BaseModel):
    redirect_url: str


class StatusResponse(BaseModel):
    status: str


class BillingRunResponse(BaseModel):
    charged: int
    failed: int
    canceled: int


def _ensure_enabled(settings: Settings) -> None:
    if not settings.kakaopay_secret_key or not settings.billing_key_encryption_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="결제 기능이 비활성 상태입니다. 잠시 후 다시 시도해 주세요.",
        )


@router.post("/subscribe/ready", response_model=ReadyResponse)
def subscribe_ready(
    current_user: CurrentUserDep = LEGACY_DEFAULT_USER,
    settings: Settings = Depends(get_settings),
) -> ReadyResponse:
    """결제창 준비 — redirect_url 로 프론트가 사용자를 KakaoPay 결제창으로 보낸다."""
    _ensure_enabled(settings)
    try:
        result = billing.start_subscription(current_user.user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("subscribe ready 실패 (user=%s): %s", current_user.user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="결제창 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.",
        ) from exc
    return ReadyResponse(redirect_url=result.redirect_url)


@router.post("/subscribe/approve", response_model=StatusResponse)
def subscribe_approve(
    pg_token: str = Query(..., min_length=1),
    current_user: CurrentUserDep = LEGACY_DEFAULT_USER,
    settings: Settings = Depends(get_settings),
) -> StatusResponse:
    """결제 승인 — KakaoPay 가 approval_url 로 redirect 하며 append 한 pg_token 으로 승인."""
    _ensure_enabled(settings)
    try:
        billing.approve_subscription(current_user.user_id, pg_token)
    except billing.SubscriptionNotPendingError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="진행 중인 결제 요청이 없습니다. 다시 시도해 주세요.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("subscribe approve 실패 (user=%s): %s", current_user.user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="결제 승인에 실패했습니다. 다시 시도해 주세요.",
        ) from exc
    return StatusResponse(status="active")


@router.post("/subscribe/cancel", response_model=StatusResponse)
def subscribe_cancel(
    current_user: CurrentUserDep = LEGACY_DEFAULT_USER,
    settings: Settings = Depends(get_settings),
) -> StatusResponse:
    """구독 해지 — 즉시 Free 강등(데이터 보존). KakaoPay SID inactive."""
    _ensure_enabled(settings)
    try:
        billing.cancel_subscription(current_user.user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("subscribe cancel 실패 (user=%s): %s", current_user.user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="구독 해지에 실패했습니다. 잠시 후 다시 시도해 주세요.",
        ) from exc
    return StatusResponse(status="canceled")


@cron_router.post("/run", response_model=BillingRunResponse)
def billing_run(
    x_billing_cron_secret: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> BillingRunResponse:
    """배치 진입점 — 만료 자동결제 + 7일 grace sweep. shared secret gate."""
    if not settings.billing_cron_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="billing cron 이 비활성 상태입니다 (JETRAG_BILLING_CRON_SECRET 미설정).",
        )
    if not hmac.compare_digest(x_billing_cron_secret, settings.billing_cron_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="cron secret 불일치"
        )
    charge = billing.charge_due_subscriptions()
    sweep = billing.sweep_past_due()
    return BillingRunResponse(
        charged=charge.charged, failed=charge.failed, canceled=sweep.canceled
    )
```

- [ ] **Step 5: 라우터 등록**

`api/app/routers/__init__.py` 를 아래로 교체(payments 2개 router export 추가):

```python
from .admin import router as admin_router
from .answer import router as answer_router
from .auth import router as auth_router
from .documents import router as documents_router
from .email_ingest import router as email_ingest_router
from .me import router as me_router
from .payments import cron_router as billing_cron_router
from .payments import router as payments_router
from .search import router as search_router
from .stats import router as stats_router

__all__ = [
    "admin_router",
    "answer_router",
    "auth_router",
    "billing_cron_router",
    "documents_router",
    "email_ingest_router",
    "me_router",
    "payments_router",
    "search_router",
    "stats_router",
]
```

`api/app/main.py` 의 import 블록(line 30-39)에 `billing_cron_router,` 와 `payments_router,` 를 알파벳 순 위치에 추가하고, `app.include_router(email_ingest_router)` (line 168) 뒤에 추가:

```python
# 수익화 W5-6 — 카카오페이 정기결제(로그인 유저) + 배치 cron endpoint(shared secret).
app.include_router(payments_router)
app.include_router(billing_cron_router)
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_payments_routes tests.test_billing_cron_route -v`
Expected: PASS (전체)

- [ ] **Step 7: Commit**

```bash
git add api/app/routers/payments.py api/app/routers/__init__.py api/app/main.py \
        api/tests/test_payments_routes.py api/tests/test_billing_cron_route.py
git commit -m "feat(payments-w5): /payments/subscribe/* + /billing/run cron endpoint"
```

---

## Task 9: GET /me/subscription

**Files:**
- Modify: `api/app/services/quota.py` (SubscriptionView + get_subscription_view), `api/app/routers/me.py`
- Test: `api/tests/test_me_subscription.py`

프론트가 구독 status + 다음 결제일을 표시하려면 `/me/plan`(status 없음) 외에 구독 상태가 필요하다.

- [ ] **Step 1: 실패 테스트 작성**

```python
# api/tests/test_me_subscription.py
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from fastapi.testclient import TestClient

from app.auth.dependencies import CurrentUser, get_current_user
from app.config import Settings, get_settings
from app.main import app
from app.services.quota import SubscriptionView


def _settings() -> Settings:
    return Settings(
        supabase_url="https://x.supabase.co", supabase_key="", supabase_service_role_key="svc",
        supabase_storage_bucket="documents", gemini_api_key="", hf_api_token="",
        default_user_id="00000000-0000-0000-0000-000000000001",
        doc_budget_usd=0.1, daily_budget_usd=0.5, sliding_24h_budget_usd=0.5,
        budget_krw_per_usd=1380.0, vision_need_score_enabled=True, vision_page_cap_per_doc=50,
        auth_enabled=True, owner_user_id="00000000-0000-0000-0000-0000000000ff",
    )


class MeSubscriptionTest(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="uid-1", is_authenticated=True
        )
        app.dependency_overrides[get_settings] = _settings
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_returns_subscription_view(self) -> None:
        with patch(
            "app.routers.me.quota.get_subscription_view",
            return_value=SubscriptionView(
                plan_code="pro", status="active",
                current_period_end="2026-08-07T00:00:00+00:00",
            ),
        ):
            resp = self.client.get("/me/subscription")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "active")
        self.assertEqual(body["current_period_end"], "2026-08-07T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_me_subscription -v`
Expected: FAIL — `ImportError: cannot import name 'SubscriptionView' from 'app.services.quota'`

- [ ] **Step 3: quota.py 에 조회 헬퍼 추가**

`api/app/services/quota.py` 의 `PlanLimits` dataclass(line 73-77) 뒤에 추가:

```python
@dataclass(frozen=True)
class SubscriptionView:
    plan_code: str
    status: str  # active | past_due | canceled | none(행 없음)
    current_period_end: str | None


def get_subscription_view(user_id: str) -> SubscriptionView:
    """구독 표시용 (/me/subscription). 행 없음/실패 → free·none (fail-open)."""
    try:
        rows = (
            get_supabase_client()
            .table("subscriptions")
            .select("plan_code, status, current_period_end")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
            .data
        ) or []
        if not rows:
            return SubscriptionView(plan_code="free", status="none", current_period_end=None)
        r = rows[0]
        return SubscriptionView(
            plan_code=r.get("plan_code", "free"),
            status=r.get("status", "none"),
            current_period_end=r.get("current_period_end"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("구독 조회 실패 (user=%s): %s", user_id, exc)
        return SubscriptionView(plan_code="free", status="none", current_period_end=None)
```

- [ ] **Step 4: me.py 에 endpoint 추가**

`api/app/routers/me.py` 의 `me_plan` 함수(line 48) 뒤에 추가:

```python
class MeSubscriptionResponse(BaseModel):
    plan_code: str
    status: str
    current_period_end: str | None = None


@router.get("/subscription", response_model=MeSubscriptionResponse)
def me_subscription(
    current_user: CurrentUserDep = LEGACY_DEFAULT_USER,
) -> MeSubscriptionResponse:
    """본인 구독 상태 — 프론트 SubscriptionSection 이 다음 결제일·해지 버튼 렌더에 사용."""
    view = quota.get_subscription_view(current_user.user_id)
    return MeSubscriptionResponse(
        plan_code=view.plan_code,
        status=view.status,
        current_period_end=view.current_period_end,
    )
```

`me.py` 상단 import 에 `CurrentUserDep`, `LEGACY_DEFAULT_USER` 가 이미 있는지 확인(line 11-15 에 `LEGACY_DEFAULT_USER`, `CurrentUserDep` import 존재). `quota` 도 이미 import 됨(line 17).

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_me_subscription -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add api/app/services/quota.py api/app/routers/me.py api/tests/test_me_subscription.py
git commit -m "feat(me-w5): GET /me/subscription — 구독 상태·다음 결제일 조회"
```

---

## Task 10: billing_charge.py — Railway cron 스크립트

**Files:**
- Create: `api/scripts/billing_charge.py`

`scripts/monitor_search_slo.py` 의 얇은 wrapper 패턴(`_API_ROOT` sys.path insert). Railway cron 이 이 스크립트를 실행 — HTTP 왕복 없이 서비스 직접 호출(주 배치 경로).

- [ ] **Step 1: 구현**

```python
# api/scripts/billing_charge.py
"""수익화 W5-6 — 정기결제 배치 (Railway cron 진입점).

만료 도래 구독 자동결제 + 7일 초과 past_due 자동 해지 sweep.
HTTP 없이 서비스 직접 호출 (주 배치 경로 — /billing/run 은 외부 cron fallback).

사용
    cd api && uv run python scripts/billing_charge.py

전제 ENV (Railway cron 서비스에 주입)
    SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / SUPABASE_KEY
    JETRAG_KAKAOPAY_SECRET_KEY / JETRAG_KAKAOPAY_CID
    JETRAG_BILLING_KEY_ENCRYPTION_KEY
"""
from __future__ import annotations

import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.services import billing  # noqa: E402


def main() -> None:
    charge = billing.charge_due_subscriptions()
    sweep = billing.sweep_past_due()
    print(
        f"[billing] charged={charge.charged} failed={charge.failed} "
        f"canceled={sweep.canceled}"
    )
    if charge.user_ids_failed:
        print(f"[billing] failed users: {charge.user_ids_failed}")
    if sweep.user_ids:
        print(f"[billing] canceled users: {sweep.user_ids}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: import smoke 확인**

Run: `cd api && uv run python -c "import scripts.billing_charge as m; print(hasattr(m, 'main'))"`
Expected: `True` (모듈 로드 성공 — 실행은 DB/ENV 필요하므로 여기선 import 만)

- [ ] **Step 3: Commit**

```bash
git add api/scripts/billing_charge.py
git commit -m "feat(script-w5): billing_charge.py — Railway cron 배치 진입점"
```

---

## Task 11: 프론트 — SubscriptionSection

**Files:**
- Create: `web/src/components/jet-rag/subscription-section.tsx`
- Modify: `web/src/app/settings/page.tsx`

settings page 는 이미 `'use client'`. 별도 컴포넌트로 분리해 page 를 가볍게 유지. useEffect cancelled flag + handler 동기 setState 패턴(AGENTS.md §1·§2) 준수.

- [ ] **Step 1: 컴포넌트 구현**

```tsx
// web/src/components/jet-rag/subscription-section.tsx
'use client';

import { useEffect, useState } from 'react';
import { apiGet, apiPost } from '@/lib/api/client';

interface Subscription {
  plan_code: string;
  status: string; // active | past_due | canceled | none
  current_period_end: string | null;
}

function statusLabel(status: string): string {
  switch (status) {
    case 'active':
      return '구독 중';
    case 'past_due':
      return '결제 실패 (유예 기간)';
    case 'canceled':
      return '해지됨';
    default:
      return '미구독';
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return '-';
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? '-'
    : d.toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' });
}

export function SubscriptionSection() {
  const [sub, setSub] = useState<Subscription | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiGet<Subscription>('/me/subscription')
      .then((s) => {
        if (cancelled) return;
        setSub(s);
        setError(null);
      })
      .catch(() => {
        if (cancelled) return;
        setError('구독 정보를 불러오지 못했습니다.');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const subscribe = async () => {
    setBusy(true);
    setError(null);
    try {
      const { redirect_url } = await apiPost<{ redirect_url: string }>(
        '/payments/subscribe/ready',
      );
      window.location.href = redirect_url; // KakaoPay 결제창으로 이동
    } catch {
      setError('결제창을 여는 데 실패했습니다. 잠시 후 다시 시도해 주세요.');
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!window.confirm('구독을 해지하면 다음 결제일부터 Free 로 전환됩니다. 계속할까요?')) return;
    setBusy(true);
    setError(null);
    try {
      await apiPost('/payments/subscribe/cancel');
      setSub((prev) => (prev ? { ...prev, status: 'canceled' } : prev));
    } catch {
      setError('구독 해지에 실패했습니다. 잠시 후 다시 시도해 주세요.');
    } finally {
      setBusy(false);
    }
  };

  const isActive = sub?.status === 'active' || sub?.status === 'past_due';

  return (
    <section className="mt-6 rounded-lg border p-4">
      <h2 className="font-semibold">구독</h2>
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
      {sub ? (
        <>
          <ul className="mt-2 space-y-1 text-sm">
            <li>
              상태: <strong>{statusLabel(sub.status)}</strong>
            </li>
            {isActive && <li>다음 결제일: {formatDate(sub.current_period_end)}</li>}
            <li>Pro 요금: 월 6,900원 (문서 200개 · 답변 일 50회 · 이메일 인제스트)</li>
          </ul>
          {sub.status === 'past_due' && (
            <p className="mt-2 text-sm text-amber-600">
              결제에 실패했습니다. 7일 내 결제되지 않으면 자동 해지됩니다.
            </p>
          )}
          {isActive ? (
            <button
              type="button"
              onClick={() => void cancel()}
              disabled={busy}
              className="mt-3 rounded border px-3 py-1 text-sm disabled:opacity-50"
            >
              {busy ? '처리 중…' : '구독 해지'}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void subscribe()}
              disabled={busy}
              className="mt-3 rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
            >
              {busy ? '이동 중…' : 'Pro 구독하기 (월 6,900원)'}
            </button>
          )}
        </>
      ) : (
        <p className="mt-2 text-sm text-gray-500">불러오는 중…</p>
      )}
    </section>
  );
}
```

- [ ] **Step 2: settings page 에 삽입**

`web/src/app/settings/page.tsx` 상단 import 에 추가:

```tsx
import { SubscriptionSection } from '@/components/jet-rag/subscription-section';
```

그리고 "내 플랜" `</section>` (line 80) 과 "이메일로 문서 보내기" `<section>` (line 82) 사이에 삽입:

```tsx
      <SubscriptionSection />
```

- [ ] **Step 3: 빌드/lint 확인**

Run: `cd web && npm run lint`
Expected: PASS (react-hooks/set-state-in-effect 위반 없음 — 동기 setState 는 모두 handler 안)

- [ ] **Step 4: Commit**

```bash
git add web/src/components/jet-rag/subscription-section.tsx web/src/app/settings/page.tsx
git commit -m "feat(web-w5): 설정 페이지 구독 섹션(구독/해지 + 다음 결제일)"
```

---

## Task 12: 프론트 — /billing 결과 페이지

**Files:**
- Create: `web/src/app/billing/success/page.tsx`, `web/src/app/billing/success/billing-approve.tsx`
- Create: `web/src/app/billing/fail/page.tsx`, `web/src/app/billing/cancel/page.tsx`

KakaoPay 는 approval_url 로 redirect 하며 `?pg_token=...` 을 append 한다. success 페이지(서버 컴포넌트)가 searchParams(Promise — Next 16)에서 pg_token 을 읽어 client 자식에 넘기고, 자식이 approve 를 호출한다. tid 는 서버(pending_tid)가 보관하므로 프론트는 pg_token 만 전달.

- [ ] **Step 1: success 서버 페이지**

```tsx
// web/src/app/billing/success/page.tsx
import { BillingApprove } from './billing-approve';

// Next 16 — searchParams 는 Promise. await 후 client 자식에 전달.
export default async function BillingSuccessPage({
  searchParams,
}: {
  searchParams: Promise<{ pg_token?: string }>;
}) {
  const { pg_token } = await searchParams;
  return <BillingApprove pgToken={pg_token ?? null} />;
}
```

- [ ] **Step 2: approve client 자식**

```tsx
// web/src/app/billing/success/billing-approve.tsx
'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { apiPost } from '@/lib/api/client';

type Phase = 'processing' | 'done' | 'error';

export function BillingApprove({ pgToken }: { pgToken: string | null }) {
  const [phase, setPhase] = useState<Phase>(pgToken ? 'processing' : 'error');

  useEffect(() => {
    if (!pgToken) return;
    let cancelled = false;
    apiPost(`/payments/subscribe/approve?pg_token=${encodeURIComponent(pgToken)}`)
      .then(() => {
        if (!cancelled) setPhase('done');
      })
      .catch(() => {
        if (!cancelled) setPhase('error');
      });
    return () => {
      cancelled = true;
    };
  }, [pgToken]);

  return (
    <main className="mx-auto max-w-md px-4 py-16 text-center">
      {phase === 'processing' && (
        <p className="text-sm text-gray-500">결제를 확인하는 중입니다…</p>
      )}
      {phase === 'done' && (
        <>
          <h1 className="text-xl font-bold">구독이 완료되었습니다 🎉</h1>
          <p className="mt-2 text-sm">이제 Pro 기능을 이용할 수 있습니다.</p>
          <Link href="/settings" className="mt-4 inline-block rounded border px-4 py-2 text-sm">
            설정으로 이동
          </Link>
        </>
      )}
      {phase === 'error' && (
        <>
          <h1 className="text-xl font-bold">결제 승인에 실패했습니다</h1>
          <p className="mt-2 text-sm text-gray-500">
            결제가 완료되지 않았습니다. 다시 시도해 주세요.
          </p>
          <Link href="/settings" className="mt-4 inline-block rounded border px-4 py-2 text-sm">
            설정으로 돌아가기
          </Link>
        </>
      )}
    </main>
  );
}
```

- [ ] **Step 3: fail / cancel 정적 페이지**

```tsx
// web/src/app/billing/fail/page.tsx
import Link from 'next/link';

export default function BillingFailPage() {
  return (
    <main className="mx-auto max-w-md px-4 py-16 text-center">
      <h1 className="text-xl font-bold">결제에 실패했습니다</h1>
      <p className="mt-2 text-sm text-gray-500">
        결제가 처리되지 않았습니다. 잠시 후 다시 시도해 주세요.
      </p>
      <Link href="/settings" className="mt-4 inline-block rounded border px-4 py-2 text-sm">
        설정으로 돌아가기
      </Link>
    </main>
  );
}
```

```tsx
// web/src/app/billing/cancel/page.tsx
import Link from 'next/link';

export default function BillingCancelPage() {
  return (
    <main className="mx-auto max-w-md px-4 py-16 text-center">
      <h1 className="text-xl font-bold">결제를 취소했습니다</h1>
      <p className="mt-2 text-sm text-gray-500">언제든지 다시 구독할 수 있습니다.</p>
      <Link href="/settings" className="mt-4 inline-block rounded border px-4 py-2 text-sm">
        설정으로 돌아가기
      </Link>
    </main>
  );
}
```

- [ ] **Step 4: 빌드/lint 확인**

Run: `cd web && npm run lint`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/src/app/billing
git commit -m "feat(web-w5): /billing success·fail·cancel redirect 페이지"
```

---

## Task 13: 프론트 — Footer + 약관 페이지

**Files:**
- Create: `web/src/components/jet-rag/footer.tsx`, `web/src/app/terms/page.tsx`, `web/src/app/privacy/page.tsx`
- Modify: `web/src/app/layout.tsx`

카카오페이 가맹 요건상 이용약관·개인정보처리방침 노출 필요. footer 는 전 페이지 공용. **약관 본문은 사용자가 초안을 작성해 넘긴다** — 아래 페이지는 완성된 scaffold + 명확한 삽입 슬롯(`TERMS_BODY`/`PRIVACY_BODY` 상수)을 제공한다.

- [ ] **Step 1: Footer 컴포넌트**

```tsx
// web/src/components/jet-rag/footer.tsx
import Link from 'next/link';

export function Footer() {
  return (
    <footer className="mt-auto border-t border-border py-6 text-sm text-muted-foreground">
      <div className="container mx-auto flex flex-wrap items-center justify-between gap-3 px-4 md:px-6">
        <span>© 2026 Jet-Rag</span>
        <nav className="flex gap-4">
          <Link href="/terms" className="hover:text-foreground">
            이용약관
          </Link>
          <Link href="/privacy" className="hover:text-foreground">
            개인정보처리방침
          </Link>
        </nav>
      </div>
    </footer>
  );
}
```

- [ ] **Step 2: layout 에 Footer 삽입**

`web/src/app/layout.tsx` 상단 import 에 추가:

```tsx
import { Footer } from '@/components/jet-rag/footer';
```

`{children}` (line 64) 바로 뒤, `</ActiveDocsProvider>` 앞에 삽입:

```tsx
            <Footer />
```

(body 가 이미 `flex min-h-dvh flex-col` 이라 footer 의 `mt-auto` 가 하단 고정으로 동작.)

- [ ] **Step 3: 약관 페이지 (사용자 초안 삽입 슬롯)**

```tsx
// web/src/app/terms/page.tsx
import type { Metadata } from 'next';

export const metadata: Metadata = { title: '이용약관 · Jet-Rag' };

// ⚠️ 사용자 작성 슬롯 — 아래 문자열을 실제 이용약관 본문으로 교체한다.
// (플랜은 페이지 구조만 제공. 법적 본문은 사용자 초안으로 채운다 — 결정 이력 #3.)
const TERMS_BODY = `제1조(목적)
본 약관은 Jet-Rag(이하 "서비스")의 이용 조건을 규정합니다.

[사용자 초안으로 교체]`;

export default function TermsPage() {
  return (
    <main className="mx-auto max-w-2xl px-4 py-8">
      <h1 className="text-2xl font-bold">이용약관</h1>
      <p className="mt-2 text-sm text-muted-foreground">최종 개정일: 2026-07-07</p>
      <article className="mt-6 whitespace-pre-wrap text-sm leading-relaxed">
        {TERMS_BODY}
      </article>
    </main>
  );
}
```

```tsx
// web/src/app/privacy/page.tsx
import type { Metadata } from 'next';

export const metadata: Metadata = { title: '개인정보처리방침 · Jet-Rag' };

// ⚠️ 사용자 작성 슬롯 — 아래 문자열을 실제 개인정보처리방침 본문으로 교체한다.
const PRIVACY_BODY = `1. 수집하는 개인정보 항목
- 이메일 주소, 결제 정보(카카오페이 빌링키), 업로드 문서

[사용자 초안으로 교체]`;

export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-2xl px-4 py-8">
      <h1 className="text-2xl font-bold">개인정보처리방침</h1>
      <p className="mt-2 text-sm text-muted-foreground">최종 개정일: 2026-07-07</p>
      <article className="mt-6 whitespace-pre-wrap text-sm leading-relaxed">
        {PRIVACY_BODY}
      </article>
    </main>
  );
}
```

- [ ] **Step 4: 빌드/lint 확인**

Run: `cd web && npm run lint && npm run build`
Expected: PASS (라우트 /terms, /privacy, /billing/* 생성)

- [ ] **Step 5: Commit**

```bash
git add web/src/components/jet-rag/footer.tsx web/src/app/layout.tsx \
        web/src/app/terms web/src/app/privacy
git commit -m "feat(web-w5): 공용 footer + 이용약관/개인정보처리방침 페이지 scaffold"
```

---

## Task 14: 문서화 + Railway cron 설정 가이드

**Files:**
- Modify: `.env.example` (repo root), `README.md`

새 ENV 블록 + Railway cron 설정 절차 + 회복(rollback) 토글 문서화.

- [ ] **Step 1: .env.example 에 ENV 블록 추가**

`.env.example` (repo root — `.env` 는 repo root 에 있음) 하단에 추가:

```bash
# ── 수익화 W5-6: 카카오페이 정기결제 ──────────────────────────────
# 미설정 시 결제 기능 비활성 (payments 라우터 503). production 무중단 graceful.
JETRAG_PAYMENT_PROVIDER=kakaopay
# KakaoPay open-api SECRET_KEY (심사 전 sandbox 키, 후 production 키)
JETRAG_KAKAOPAY_SECRET_KEY=
# 정기결제 CID — sandbox 기본 TCSUBSCRIP, 심사 승인 후 production CID 로 교체
JETRAG_KAKAOPAY_CID=TCSUBSCRIP
# SID 암호화 Fernet 키 — 생성: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
JETRAG_BILLING_KEY_ENCRYPTION_KEY=
# 배치 endpoint(/billing/run) 호출 secret (외부 cron fallback 용)
JETRAG_BILLING_CRON_SECRET=
# 결제창 redirect base (프론트 도메인)
JETRAG_BILLING_REDIRECT_BASE=https://jetrag.woong-s.com
```

- [ ] **Step 2: README 에 W5-6 섹션 + 운영 가이드 추가**

`README.md` 의 수익화 sprint 섹션(W4 항목 뒤)에 추가:

```markdown
### W5-6 — 카카오페이 정기결제

- 구독 등록: `/settings` → "Pro 구독하기" → KakaoPay 결제창 → `/billing/success` (approve).
- 상태 머신: `active → (결제 실패) past_due (7일 grace) → canceled` (Free 강등, 데이터 보존).
- 월 배치: **Railway cron** 이 매일 실행 (아래 설정 참고).

#### Railway cron 설정 (월 자동결제)

1. Railway 프로젝트 → API 서비스 → **Settings → Cron Schedule**.
2. 스케줄: `0 18 * * *` (UTC 18:00 = KST 새벽 3시).
3. 서비스 시작 커맨드(또는 cron 전용 서비스 커맨드): `cd api && uv run python scripts/billing_charge.py`
4. cron 서비스에 결제 ENV(위 .env.example 블록) + `SUPABASE_*` 주입 확인.
   - Railway ENV 변경은 좌상단 보라색 **Deploy(Apply N changes)** 클릭해야 반영됨.

**외부 cron fallback** (Railway cron 미사용 시): cron-job.org / Cloudflare Workers cron 이
`POST https://<api>/billing/run` 을 `X-Billing-Cron-Secret: <JETRAG_BILLING_CRON_SECRET>` 헤더로 호출.

#### 회복(rollback) 토글

- 결제 전면 비활성: `JETRAG_KAKAOPAY_SECRET_KEY` 또는 `JETRAG_BILLING_KEY_ENCRYPTION_KEY` 를 빈값으로 → payments 라우터 503 (기존 유저 구독 상태는 DB 유지).
- 배치 중단: `JETRAG_BILLING_CRON_SECRET` 빈값 → `/billing/run` 503. (Railway cron 은 스케줄 자체를 끄면 됨.)
- 심사 지연 시 수동 결제 fallback: `POST /admin/subscriptions` 로 admin 이 `status=active` 수동 upsert (마이그 022 경로 그대로).
```

- [ ] **Step 3: Commit**

```bash
git add .env.example README.md
git commit -m "docs(w5): 카카오페이 ENV + Railway cron 설정 + 회복 토글 가이드"
```

---

## Task 15: 전체 회귀 + 통합 스모크

**Files:** (없음 — 검증 전용)

- [ ] **Step 1: 백엔드 전체 테스트**

Run: `cd api && uv run python -m unittest discover tests`
Expected: 전체 PASS (신규 test_billing_*, test_payment_*, test_payments_*, test_me_subscription 포함, 기존 회귀 0)

- [ ] **Step 2: 프론트 빌드**

Run: `cd web && npm run lint && npm run build`
Expected: PASS

- [ ] **Step 3: 로컬 API 부팅 스모크**

Run: `cd api && uv run python -c "from app.main import app; print([r.path for r in app.routes if 'payment' in r.path or 'billing' in r.path or 'subscription' in r.path])"`
Expected: `/payments/subscribe/ready`, `/payments/subscribe/approve`, `/payments/subscribe/cancel`, `/billing/run`, `/me/subscription` 노출 확인

- [ ] **Step 4: 최종 커밋 (필요 시)**

검증만 통과하면 별도 커밋 불필요. 실패 시 해당 태스크로 복귀.

---

## Self-Review

**1. Spec coverage** (spec §3 카카오페이 정기결제):
- ✅ 구조: 프론트 결제창(Task 11) → SID 발급(Task 5·7) → payments 라우터 암호화 저장(Task 3·7·8) → 월 배치(Task 10, Railway cron Task 14).
- ✅ 상태 머신 active→past_due(7일)→canceled: Task 7 (charge/sweep).
- ✅ 어댑터 패턴 유지 (PaymentProvider Protocol + KakaoPayImpl): Task 4·5·6.
- ✅ 테스트: sandbox CID + unittest mock: Task 4~9 전 태스크 TDD.
- ✅ 약관 페이지(가맹 요건): Task 13.
- ✅ 결정 이력 7건 전부 반영.

**2. Placeholder scan:** 약관 본문(`TERMS_BODY`/`PRIVACY_BODY`)은 사용자가 초안 작성(결정 이력 #3)하는 설계상 슬롯 — 페이지 코드 자체는 완성. 그 외 TBD/TODO 없음. Task 3 Step 1 의 초기 잔재(`_settings_with_key`)는 같은 Step 에 "정리된 최종본"으로 교체 명시.

**3. Type consistency:**
- `ReadyResult(tid, redirect_url)` / `ApproveResult(sid, tid)` — Task 4 정의, Task 5·7 사용 일치.
- `PaymentProvider` 메소드명 `ready`/`approve`/`subscribe`/`inactivate` — Task 4·5·7 일치.
- `billing.start_subscription`/`approve_subscription(user_id, pg_token)`/`charge_due_subscriptions(now)`/`sweep_past_due(now)`/`cancel_subscription(user_id)` + `SubscriptionNotPendingError` + `ChargeReport`/`SweepReport` — Task 7 정의, Task 8·10 사용 일치.
- `SubscriptionView(plan_code, status, current_period_end)` + `get_subscription_view` — Task 9 정의·사용 일치.
- 프론트 `/me/subscription` 응답(plan_code/status/current_period_end) — Task 9(백) ↔ Task 11(프론트 interface) 일치.
- `/payments/subscribe/ready` 응답 `{redirect_url}` — Task 8 ↔ Task 11 일치.
- approve 는 query `pg_token` (body 없음) — Task 8 ↔ Task 12 일치 (기존 apiPost body-less 로 호출 가능, apiPostJson 불필요).

---

## 실행 중 지켜야 할 프로젝트 관례 (재확인)

- `.env` 는 **repo root** (api/ 아님).
- `_parse_*` helpers + `@lru_cache get_settings()` — 테스트는 `get_settings.cache_clear()` 후 검증.
- 어댑터는 `Protocol` + `impl/` + factory lazy import 5-part.
- 테스트는 unittest.TestCase + `app.dependency_overrides` + MagicMock supabase. `os.environ.setdefault("GEMINI_API_KEY", ...)` 상단 필수.
- 스크립트는 `_API_ROOT` sys.path insert 얇은 wrapper.
- 프론트 useEffect 는 cancelled flag(AGENTS.md §1), 동기 setState 는 handler 에서만(§2).
- 커밋: `feat({scope}-w5): ...` / `fix(...)` / `docs(...)` / `feat(migration-w5): ...`.
- Railway ENV 변경은 보라색 Deploy 클릭 필수(회복 토글 문서 참고).
