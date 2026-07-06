# W4 — 이메일 인제스트 (Cloudflare Email Routing → webhook → 기존 파이프라인) 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pro 유저가 `u-{토큰}@in.woong-s.com` 으로 메일을 보내면 첨부파일(PDF/HWP/HWPX/DOCX/이미지)이 기존 9-stage 인제스트 파이프라인으로 자동 수집된다 — 베타 피드백 1순위(업로드 마찰 제거) 해소.

**Architecture:** Cloudflare Email Routing 이 `in.woong-s.com` 수신 메일을 Email Worker 로 라우팅하고, Worker(postal-mime 파싱)가 첨부를 base64 JSON 으로 백엔드 `POST /ingest/email` webhook 에 전달한다(shared secret 헤더). 백엔드는 to-주소 토큰→user_id 매핑(`email_ingest_addresses`, 마이그 023) → 발신자 화이트리스트(가입 이메일) → Pro 플랜 게이트(W3 `quota.get_effective_plan` 재사용) 검증 후, 첨부마다 `POST /documents` 수신 단계와 동일한 게이트(확장자·50MB·magic bytes·SHA-256 dedup)를 거쳐 `run_full_ingest` BG 파이프라인을 재사용한다. 검증 실패 메일은 **로그만 남기고 조용히 무시** (사용자 확정 2026-07-06 — 발신 메일 인프라 불필요). 유저는 `/settings` 페이지에서 자신의 인제스트 주소를 확인·재발급한다.

**Tech Stack:** Python 3.12 / FastAPI / Supabase / Cloudflare Email Workers + postal-mime / Next.js(App Router)

**선행 조건: W3 플랜(`2026-07-06-w3-plans-quota-metering.md`) 완료** — `app/services/quota.py`(Pro 게이트)와 `app/routers/me.py` 에 의존.

**전제 (기존 코드 사실 — 구현 전 이해 필수):**
- 업로드 수신 단계(`api/app/routers/documents.py:402-575`): 확장자 화이트리스트 `_ALLOWED_EXTENSIONS`(66행) → SHA-256+50MB(`_MAX_SIZE_BYTES`, 79행)+magic bytes(`validate_magic`, `app/routers/_input_gate.py`) → dedup(`user_id`+`sha256`, `deleted_at IS NULL`) → `documents` insert(`SupabaseBlobStorage.build_pending_path` pending 경로, 제목 NFC 정규화) → `create_job` → BG `run_full_ingest(job_id, doc_id, raw, sha256, ext, content_type, page_cap_override, user_id)`.
- `page_cap_override = resolve_page_cap(ingest_mode, settings)` (`app/services/ingest_mode.py`), 기본 mode `"default"`. `_flags_with_ingest_mode(existing, mode)` 로 flags 구성 (documents.py:105).
- 실패 문서 재업로드 분기(`flags.failed` → `_reset_doc_for_reingest`)는 이메일 경로에서 **불필요** — 정상 중복이면 skip, 실패 문서 재시도는 웹 UI 로 유도 (YAGNI).
- `CurrentUser.email` 은 JWT claim 에서 채워짐 (`api/app/auth/dependencies.py:41`) — 주소 발급 시점에 저장해 두면 webhook 에서 admin API 호출 없이 발신자 검증 가능.
- `Settings` 는 default 있는 필드 추가 시 기존 테스트 헬퍼(`_settings()`) 무수정. `_parse_bool` 헬퍼 존재.
- 라우터 등록: `api/app/routers/__init__.py` + `api/app/main.py`. CORS `allow_methods=["GET", "POST"]`.
- 최신 마이그레이션 = 022 (W3). **다음 번호 = 023.**
- 프론트: `web/src/app/<route>/page.tsx` (App Router), API 클라이언트 `web/src/lib/api/client.ts` (`apiGet`/`apiPost`/`apiPostJson` — httpOnly 쿠키 자동 첨부). 설정/프로필 페이지는 현재 **없음** (신규).
- Cloudflare: `woong-s.com` 도메인 기보유 (`jetrag-api.woong-s.com` 등 사용 중). Email Routing 메시지 상한 25MiB — 백엔드 50MB cap 이내.
- 테스트: `cd api && uv run python -m unittest discover tests`.

**설계 결정:**
1. **거절 = 조용히 무시** (사용자 확정) — 알 수 없는 토큰/발신자 불일치/Free 플랜/무첨부/비허용 확장자 모두 webhook 은 200 + warning 로그. Worker 재시도·발신 메일 인프라 불필요. 단 **secret 불일치는 401** (Worker 설정 오류는 시끄럽게).
2. **발신자 화이트리스트 = 주소 발급 시점의 가입 이메일** — `email_ingest_addresses.owner_email` 에 저장(JWT claim), webhook 이 From 과 casefold 비교. 저장값 NULL(구 토큰)이면 거절+로그 → 재발급 유도. Supabase admin API 의존 제거.
3. **Pro 게이트 = fail-closed** — 플랜 조회 실패 시 인제스트 거절+로그 (쓰기 경로라 W3 quota 의 fail-open 과 반대 방향이 안전).
4. **허용 확장자 = 스펙 명시분만** (pdf/hwp/hwpx/docx/jpg/jpeg/png/heic) — 업로드의 txt/md/pptx 는 이메일 첨부 스팸 벡터라 제외. 필요 시 한 줄 추가로 확장.
5. **일일 docs rate limit 카운터도 increment** — 이메일 인제스트도 W2 abuse cap 의 대상 (첨부당 1 카운트). W3 보유 문서 한도(Pro 200)도 적용.
6. Worker 코드는 본 repo `workers/email-ingest/` 에 커밋 — 배포는 wrangler 수동 (Task 7).

---

## 파일 구조

| 파일 | 책임 | 변경 |
|---|---|---|
| `api/migrations/023_email_ingest_addresses.sql` | 토큰↔user 매핑 + owner_email 화이트리스트 | **Create** |
| `api/app/config.py` | webhook secret · 수신 도메인 ENV | Modify |
| `api/app/services/email_ingest.py` | 토큰 발급/조회/회전 · 검증 · 첨부→파이프라인 | **Create** |
| `api/app/routers/email_ingest.py` | `POST /ingest/email` webhook | **Create** |
| `api/app/routers/me.py` | `GET /me/email-ingest` + `POST /me/email-ingest/rotate` | Modify |
| `api/app/routers/__init__.py`, `api/app/main.py` | 라우터 등록 | Modify |
| `workers/email-ingest/{src/index.js, wrangler.toml, package.json}` | CF Email Worker (MIME 파싱→webhook) | **Create** |
| `web/src/app/settings/page.tsx` | 플랜 + 이메일 인제스트 주소 UI | **Create** |
| `api/tests/test_email_ingest.py` | 서비스 단위 테스트 | **Create** |
| `api/tests/test_email_ingest_routes.py` | webhook·/me/email-ingest 통합 테스트 | **Create** |
| `api/tests/test_config.py` | ENV parse 테스트 | Modify |

---

## Task 1: 마이그레이션 023 — email_ingest_addresses

**Files:**
- Create: `api/migrations/023_email_ingest_addresses.sql`

- [ ] **Step 1: 마이그레이션 파일 작성**

`api/migrations/023_email_ingest_addresses.sql`:

```sql
-- ============================================================
-- 023_email_ingest_addresses.sql — 수익화 W4 (이메일 인제스트)
-- ============================================================
-- 배경
--   Pro 유저 전용 이메일 인제스트 — u-{token}@in.woong-s.com 수신 주소를
--   user_id 에 매핑한다. 베타 피드백 1순위(업로드 마찰) 해소.
--
-- 설계
--   - user_id PK — 유저당 주소 1개.
--   - token UNIQUE — 8자리 소문자 영숫자 (URL·이메일 안전). 유출/스팸 시
--     rotate 로 재발급 (rotated_at 갱신, 구 토큰 즉시 무효).
--   - owner_email — 주소 발급 시점의 가입 이메일 (JWT claim). webhook 이
--     발신자(From) 화이트리스트 비교에 사용 — admin API 조회 불필요.
--
-- RLS
--   - service_role only (백엔드 전용 — 프론트는 GET /me/email-ingest 경유).
--     (021 usage_counters 와 동일 패턴.)
--
-- 적용 절차
--   Supabase Studio → SQL Editor → New query 빈 탭 → paste → Run.
--
-- 검증 SQL (적용 후)
--   INSERT INTO email_ingest_addresses (user_id, token, owner_email)
--   VALUES ('00000000-0000-0000-0000-00000000dead', 'abc12345', 'x@y.z');
--   SELECT * FROM email_ingest_addresses WHERE token = 'abc12345';   -- 1행
--   DELETE FROM email_ingest_addresses
--     WHERE user_id = '00000000-0000-0000-0000-00000000dead';        -- cleanup
-- ============================================================

CREATE TABLE IF NOT EXISTS email_ingest_addresses (
    user_id     UUID PRIMARY KEY,
    token       TEXT NOT NULL UNIQUE,
    owner_email TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at  TIMESTAMPTZ
);

ALTER TABLE email_ingest_addresses ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS email_ingest_addresses_service_role_all ON email_ingest_addresses;
CREATE POLICY email_ingest_addresses_service_role_all
    ON email_ingest_addresses
    FOR ALL
    TO service_role
    USING (TRUE) WITH CHECK (TRUE);

-- ============================================================
-- 끝. Python 연동은 app/services/email_ingest.py (Task 3).
-- ============================================================
```

- [ ] **Step 2: 파일만 커밋 (production 적용은 Task 7)**

```bash
git add api/migrations/023_email_ingest_addresses.sql
git commit -m "feat(migration-w4): email_ingest_addresses 토큰 매핑 테이블"
```

---

## Task 2: config — webhook secret + 수신 도메인 ENV

**Files:**
- Modify: `api/app/config.py`
- Test: `api/tests/test_config.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_config.py` 하단에 추가:

```python
class EmailIngestSettingsTest(unittest.TestCase):
    """수익화 W4 — 이메일 인제스트 ENV parse."""

    def _clear(self) -> None:
        for k in ("JETRAG_EMAIL_WEBHOOK_SECRET", "JETRAG_EMAIL_INGEST_DOMAIN"):
            os.environ.pop(k, None)
        config.get_settings.cache_clear()

    def test_defaults(self) -> None:
        self._clear()
        try:
            s = config.get_settings()
            self.assertEqual(s.email_webhook_secret, "")
            self.assertEqual(s.email_ingest_domain, "in.woong-s.com")
        finally:
            self._clear()

    def test_env_override(self) -> None:
        self._clear()
        os.environ["JETRAG_EMAIL_WEBHOOK_SECRET"] = "s3cret"
        os.environ["JETRAG_EMAIL_INGEST_DOMAIN"] = "mail.example.com"
        try:
            s = config.get_settings()
            self.assertEqual(s.email_webhook_secret, "s3cret")
            self.assertEqual(s.email_ingest_domain, "mail.example.com")
        finally:
            self._clear()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_config.EmailIngestSettingsTest -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'email_webhook_secret'`

- [ ] **Step 3: Settings 필드 + 파싱 추가**

`api/app/config.py` — `Settings` 의 `quota_enforcement_enabled` 아래에 추가:

```python
    # 수익화 W4 (2026-07-06) — 이메일 인제스트. secret 빈값 = 기능 비활성 (webhook 503).
    email_webhook_secret: str = ""
    email_ingest_domain: str = "in.woong-s.com"
```

`get_settings()` 의 `quota_enforcement_enabled=...` 뒤에 추가:

```python
        email_webhook_secret=os.environ.get("JETRAG_EMAIL_WEBHOOK_SECRET", ""),
        email_ingest_domain=os.environ.get("JETRAG_EMAIL_INGEST_DOMAIN", "in.woong-s.com"),
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_config.EmailIngestSettingsTest -v`
Expected: PASS (2 tests)

- [ ] **Step 5: 커밋**

```bash
git add api/app/config.py api/tests/test_config.py
git commit -m "feat(config-w4): 이메일 인제스트 webhook secret·도메인 ENV"
```

---

## Task 3: email_ingest 서비스 — 주소 발급·검증·첨부 인제스트

**Files:**
- Create: `api/app/services/email_ingest.py`
- Test: `api/tests/test_email_ingest.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_email_ingest.py`:

```python
"""수익화 W4 — app.services.email_ingest 단위 테스트. MagicMock Supabase, 외부 I/O 0."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")


class TokenTest(unittest.TestCase):
    def test_generate_token_shape(self) -> None:
        from app.services.email_ingest import generate_token

        tok = generate_token()
        self.assertEqual(len(tok), 8)
        self.assertTrue(tok.isalnum())
        self.assertEqual(tok, tok.lower())

    def test_build_address(self) -> None:
        from app.services.email_ingest import build_address

        self.assertEqual(build_address("abc12345", "in.woong-s.com"), "u-abc12345@in.woong-s.com")


class ParseTokenTest(unittest.TestCase):
    def test_parses_token_from_to_address(self) -> None:
        from app.services.email_ingest import parse_token

        self.assertEqual(parse_token("u-abc12345@in.woong-s.com"), "abc12345")

    def test_handles_display_name_and_case(self) -> None:
        from app.services.email_ingest import parse_token

        self.assertEqual(parse_token("Jet-Rag <U-ABC12345@IN.WOONG-S.COM>"), "abc12345")

    def test_invalid_returns_none(self) -> None:
        from app.services.email_ingest import parse_token

        self.assertIsNone(parse_token("someone@example.com"))
        self.assertIsNone(parse_token("not-an-email"))


class LookupAddressTest(unittest.TestCase):
    def _client(self, rows: list[dict]) -> MagicMock:
        client = MagicMock()
        t = MagicMock()
        t.select.return_value = t
        t.eq.return_value = t
        t.limit.return_value = t
        t.execute.return_value.data = rows
        client.table.return_value = t
        return client

    def test_found(self) -> None:
        from app.services import email_ingest

        rows = [{"user_id": "uid-1", "token": "abc12345", "owner_email": "a@b.c"}]
        with patch.object(email_ingest, "get_supabase_client", return_value=self._client(rows)):
            rec = email_ingest.lookup_by_token("abc12345")
        self.assertEqual(rec["user_id"], "uid-1")

    def test_not_found_returns_none(self) -> None:
        from app.services import email_ingest

        with patch.object(email_ingest, "get_supabase_client", return_value=self._client([])):
            self.assertIsNone(email_ingest.lookup_by_token("zzzzzzzz"))


class SenderAllowedTest(unittest.TestCase):
    def test_match_case_insensitive(self) -> None:
        from app.services.email_ingest import sender_allowed

        self.assertTrue(sender_allowed("Kim <USER@Gmail.com>", "user@gmail.com"))

    def test_mismatch(self) -> None:
        from app.services.email_ingest import sender_allowed

        self.assertFalse(sender_allowed("other@gmail.com", "user@gmail.com"))

    def test_missing_owner_email_rejects(self) -> None:
        from app.services.email_ingest import sender_allowed

        self.assertFalse(sender_allowed("user@gmail.com", None))


class IngestAttachmentTest(unittest.TestCase):
    def test_disallowed_extension_skipped(self) -> None:
        from app.services import email_ingest

        result = email_ingest.ingest_email_attachment(
            user_id="uid-1",
            filename="note.txt",
            content_type="text/plain",
            raw=b"hello",
            background_tasks=MagicMock(),
        )
        self.assertEqual(result["status"], "skipped")
        self.assertIn("확장자", result["reason"])

    def test_oversize_skipped(self) -> None:
        from app.services import email_ingest

        with patch.object(email_ingest, "_MAX_SIZE_BYTES", 10):
            result = email_ingest.ingest_email_attachment(
                user_id="uid-1",
                filename="big.pdf",
                content_type="application/pdf",
                raw=b"%PDF-1.4 0123456789",
                background_tasks=MagicMock(),
            )
        self.assertEqual(result["status"], "skipped")

    def test_bad_magic_skipped(self) -> None:
        from app.services import email_ingest

        result = email_ingest.ingest_email_attachment(
            user_id="uid-1",
            filename="fake.pdf",
            content_type="application/pdf",
            raw=b"GIF89a not a pdf",
            background_tasks=MagicMock(),
        )
        self.assertEqual(result["status"], "skipped")

    def test_duplicate_skipped_without_insert(self) -> None:
        from app.services import email_ingest

        client = MagicMock()
        t = MagicMock()
        t.select.return_value = t
        t.eq.return_value = t
        t.is_.return_value = t
        t.limit.return_value = t
        t.execute.return_value.data = [{"id": "doc-1", "flags": {}}]
        client.table.return_value = t
        bg = MagicMock()
        with patch.object(email_ingest, "get_supabase_client", return_value=client):
            result = email_ingest.ingest_email_attachment(
                user_id="uid-1",
                filename="doc.pdf",
                content_type="application/pdf",
                raw=b"%PDF-1.4 test",
                background_tasks=bg,
            )
        self.assertEqual(result["status"], "duplicated")
        bg.add_task.assert_not_called()

    def test_new_attachment_queues_pipeline(self) -> None:
        from app.services import email_ingest

        client = MagicMock()
        dedup_t = MagicMock()
        dedup_t.select.return_value = dedup_t
        dedup_t.eq.return_value = dedup_t
        dedup_t.is_.return_value = dedup_t
        dedup_t.limit.return_value = dedup_t
        dedup_t.execute.return_value.data = []
        insert_t = MagicMock()
        insert_t.insert.return_value.execute.return_value.data = [{"id": "doc-new"}]
        # 1번째 table() 호출 = dedup select, 2번째 = insert
        client.table.side_effect = [dedup_t, insert_t]
        bg = MagicMock()
        fake_job = MagicMock()
        fake_job.id = "job-1"
        with patch.object(email_ingest, "get_supabase_client", return_value=client), \
             patch.object(email_ingest, "create_job", return_value=fake_job) as cj, \
             patch.object(email_ingest, "run_full_ingest") as rfi:
            result = email_ingest.ingest_email_attachment(
                user_id="uid-1",
                filename="보고서.pdf",
                content_type="application/pdf",
                raw=b"%PDF-1.4 test",
                background_tasks=bg,
            )
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["doc_id"], "doc-new")
        cj.assert_called_once_with(doc_id="doc-new")
        bg.add_task.assert_called_once()
        self.assertIs(bg.add_task.call_args.args[0], rfi)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_email_ingest -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.email_ingest'`

- [ ] **Step 3: 서비스 구현**

`api/app/services/email_ingest.py`:

```python
"""수익화 W4 — 이메일 인제스트 (주소 발급·검증·첨부 → 파이프라인).

Pro 유저 전용 u-{token}@<domain> 수신 주소. Cloudflare Email Worker 가
POST /ingest/email 로 첨부를 전달하면, 업로드 수신 단계와 동일한 게이트
(확장자·50MB·magic bytes·SHA-256 dedup)를 거쳐 run_full_ingest 를 재사용한다.

정책
- 거절(알 수 없는 토큰/발신자 불일치/Free/비허용 첨부) = 조용히 skip + warning 로그.
- 발신자 화이트리스트 = 주소 발급 시점의 가입 이메일(owner_email, JWT claim).
- 허용 확장자 = 스펙 명시분(pdf/hwp/hwpx/docx/이미지)만 — 업로드보다 좁음(스팸 벡터 축소).
- documents.source_channel = "email".
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
import string
import unicodedata
from datetime import datetime, timezone
from pathlib import PurePosixPath

from fastapi import BackgroundTasks, HTTPException

from app.adapters.impl.supabase_storage import SupabaseBlobStorage
from app.config import get_settings
from app.db import get_supabase_client
from app.ingest import create_job, run_full_ingest
from app.routers._input_gate import HEAD_BYTES, validate_magic
from app.services.ingest_mode import resolve_page_cap

logger = logging.getLogger(__name__)

_TOKEN_ALPHABET = string.ascii_lowercase + string.digits
_TOKEN_LEN = 8
# 스펙 §2 — 이메일 첨부 허용 포맷. 업로드(_ALLOWED_EXTENSIONS)의 부분집합.
_EMAIL_ALLOWED_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".hwp": "hwp",
    ".hwpx": "hwpx",
    ".docx": "docx",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".heic": "image",
}
_MAX_SIZE_BYTES = 50 * 1024 * 1024  # 업로드와 동일 (documents.py:79)
_EMAIL_RE = re.compile(r"<?([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+)>?\s*$")


def generate_token() -> str:
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_LEN))


def build_address(token: str, domain: str) -> str:
    return f"u-{token}@{domain}"


def _extract_email(raw: str) -> str | None:
    """'Name <a@b.c>' / 'a@b.c' → 'a@b.c' (소문자). 실패 시 None."""
    match = _EMAIL_RE.search(raw.strip())
    return match.group(1).lower() if match else None


def parse_token(to_address: str) -> str | None:
    """수신(To) 주소에서 토큰 추출. u-{token}@... 형식 아니면 None."""
    email = _extract_email(to_address)
    if not email or not email.startswith("u-"):
        return None
    local = email.split("@", 1)[0]
    token = local[2:]
    if len(token) != _TOKEN_LEN or not token.isalnum():
        return None
    return token


def lookup_by_token(token: str) -> dict | None:
    """token → {user_id, token, owner_email} row. 없으면/실패 시 None."""
    try:
        rows = (
            get_supabase_client()
            .table("email_ingest_addresses")
            .select("user_id, token, owner_email")
            .eq("token", token)
            .limit(1)
            .execute()
            .data
        ) or []
        return rows[0] if rows else None
    except Exception as exc:  # noqa: BLE001 — 쓰기 경로: 조회 실패 = 거절 (fail-closed)
        logger.warning("email_ingest 주소 조회 실패 (token=%s...): %s", token[:4], exc)
        return None


def sender_allowed(from_address: str, owner_email: str | None) -> bool:
    """발신자 화이트리스트 — 가입 이메일과 일치해야 통과. owner_email 없으면 거절."""
    if not owner_email:
        return False
    sender = _extract_email(from_address)
    return sender is not None and sender == owner_email.strip().lower()


def get_or_create_address(user_id: str, user_email: str | None) -> dict:
    """유저의 인제스트 주소 row 반환 — 없으면 발급. owner_email 은 최신값으로 갱신."""
    client = get_supabase_client()
    rows = (
        client.table("email_ingest_addresses")
        .select("user_id, token, owner_email")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    ) or []
    if rows:
        row = rows[0]
        if user_email and row.get("owner_email") != user_email:
            client.table("email_ingest_addresses").update(
                {"owner_email": user_email}
            ).eq("user_id", user_id).execute()
            row["owner_email"] = user_email
        return row
    row = {
        "user_id": user_id,
        "token": generate_token(),
        "owner_email": user_email,
    }
    client.table("email_ingest_addresses").insert(row).execute()
    return row


def rotate_address(user_id: str, user_email: str | None) -> dict:
    """토큰 재발급 (스팸/유출 대응). 구 토큰 즉시 무효."""
    client = get_supabase_client()
    row = {
        "user_id": user_id,
        "token": generate_token(),
        "owner_email": user_email,
        "rotated_at": datetime.now(timezone.utc).isoformat(),
    }
    client.table("email_ingest_addresses").upsert(row, on_conflict="user_id").execute()
    return row


def ingest_email_attachment(
    *,
    user_id: str,
    filename: str,
    content_type: str,
    raw: bytes,
    background_tasks: BackgroundTasks,
) -> dict:
    """첨부 1건을 업로드 수신 단계와 동일 게이트로 검증 후 BG 파이프라인 큐잉.

    반환: {"status": "accepted"|"duplicated"|"skipped", ...}. 예외를 던지지 않는다
    (webhook 은 첨부별 결과를 모아 항상 200 — 거절 정책 '조용히 무시').

    documents.py upload_document(402-575) 수신 단계의 의도적 미러 — 공용 추출
    리팩토링은 hot path(업로드) 회귀 리스크로 보류. 게이트 값(_MAX_SIZE_BYTES,
    validate_magic)은 동일 소스를 공유한다.
    """
    ext = PurePosixPath(filename).suffix.lower()
    doc_type = _EMAIL_ALLOWED_EXTENSIONS.get(ext)
    if doc_type is None:
        logger.warning("email_ingest skip — 비허용 확장자 %s (user=%s)", ext, user_id)
        return {"status": "skipped", "filename": filename, "reason": f"비허용 확장자: {ext or '(없음)'}"}

    if len(raw) == 0:
        return {"status": "skipped", "filename": filename, "reason": "빈 첨부"}
    if len(raw) > _MAX_SIZE_BYTES:
        logger.warning("email_ingest skip — 50MB 초과 (user=%s, %d bytes)", user_id, len(raw))
        return {"status": "skipped", "filename": filename, "reason": "50MB 초과"}

    try:
        validate_magic(ext=ext, raw_head=raw[:HEAD_BYTES])
    except HTTPException as exc:
        logger.warning("email_ingest skip — magic bytes 불일치 (user=%s, %s): %s", user_id, filename, exc.detail)
        return {"status": "skipped", "filename": filename, "reason": "파일 형식 불일치"}

    sha256 = hashlib.sha256(raw).hexdigest()
    settings = get_settings()
    supabase = get_supabase_client()

    # Tier 1 dedup (upload_document 와 동일 — 실패 문서 재시도 분기는 웹 UI 전용)
    existing = (
        supabase.table("documents")
        .select("id, flags")
        .eq("user_id", user_id)
        .eq("sha256", sha256)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    if existing.data:
        return {"status": "duplicated", "filename": filename, "doc_id": existing.data[0]["id"]}

    import uuid as _uuid

    pending_path = SupabaseBlobStorage.build_pending_path(
        user_id=user_id, doc_uuid=_uuid.uuid4().hex, ext=ext
    )
    doc_title = unicodedata.normalize("NFC", PurePosixPath(filename).stem)
    page_cap_override = resolve_page_cap("default", settings)
    doc_row = (
        supabase.table("documents")
        .insert(
            {
                "user_id": user_id,
                "title": doc_title,
                "doc_type": doc_type,
                "source_channel": "email",
                "storage_path": pending_path,
                "sha256": sha256,
                "size_bytes": len(raw),
                "content_type": content_type or "application/octet-stream",
                "flags": {"ingest_mode": "default"},
            }
        )
        .execute()
    )
    doc_id = doc_row.data[0]["id"]
    job = create_job(doc_id=doc_id)
    background_tasks.add_task(
        run_full_ingest,
        job_id=job.id,
        doc_id=doc_id,
        raw=raw,
        sha256=sha256,
        ext=ext,
        content_type=content_type or "application/octet-stream",
        page_cap_override=page_cap_override,
        user_id=user_id,
    )
    return {"status": "accepted", "filename": filename, "doc_id": doc_id, "job_id": job.id}
```

> import 주의: `app.routers._input_gate` 를 서비스에서 import — `_input_gate` 는 라우터 의존이 없는 순수 검증 모듈이라 순환 없음. `run_full_ingest`/`create_job` 은 documents.py:44-50 과 동일한 `app.ingest` 소스.
> `received_ms` 는 웹 업로드 SLO 측정용이라 이메일 경로는 미기록 (nullable).

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_email_ingest -v`
Expected: PASS (13 tests)

- [ ] **Step 5: 커밋**

```bash
git add api/app/services/email_ingest.py api/tests/test_email_ingest.py
git commit -m "feat(email-w4): 이메일 인제스트 서비스 — 주소 발급·검증·첨부 파이프라인 재사용"
```

---

## Task 4: `POST /ingest/email` webhook 라우터

**Files:**
- Create: `api/app/routers/email_ingest.py`
- Modify: `api/app/routers/__init__.py`, `api/app/main.py`
- Test: `api/tests/test_email_ingest_routes.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_email_ingest_routes.py`:

```python
"""수익화 W4 — POST /ingest/email webhook 통합 테스트. 외부 I/O 0."""
from __future__ import annotations

import base64
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "dummy-test-token")

from fastapi.testclient import TestClient

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
        email_webhook_secret="s3cret",
        email_ingest_domain="in.woong-s.com",
    )
    base.update(over)
    return Settings(**base)


_PRO = PlanLimits(code="pro", max_documents=200, answers_per_day=50)
_FREE = PlanLimits(code="free", max_documents=10, answers_per_day=5)
_ADDR = {"user_id": "uid-1", "token": "abc12345", "owner_email": "user@gmail.com"}


def _payload(**over) -> dict:
    base = {
        "to": "u-abc12345@in.woong-s.com",
        "from": "user@gmail.com",
        "subject": "보고서",
        "attachments": [
            {
                "filename": "doc.pdf",
                "content_type": "application/pdf",
                "content_base64": base64.b64encode(b"%PDF-1.4 test").decode(),
            }
        ],
    }
    base.update(over)
    return base


class EmailWebhookTest(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[get_settings] = lambda: _settings()
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"X-Jetrag-Webhook-Secret": "s3cret"}

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_missing_secret_returns_401(self) -> None:
        resp = self.client.post("/ingest/email", json=_payload())
        self.assertEqual(resp.status_code, 401)

    def test_disabled_when_secret_unset_returns_503(self) -> None:
        app.dependency_overrides[get_settings] = lambda: _settings(email_webhook_secret="")
        resp = self.client.post("/ingest/email", json=_payload(), headers=self.headers)
        self.assertEqual(resp.status_code, 503)

    def test_unknown_token_ignored_with_200(self) -> None:
        with patch("app.routers.email_ingest.email_ingest.lookup_by_token", return_value=None):
            resp = self.client.post("/ingest/email", json=_payload(), headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")

    def test_sender_mismatch_ignored(self) -> None:
        with patch("app.routers.email_ingest.email_ingest.lookup_by_token", return_value=_ADDR):
            resp = self.client.post(
                "/ingest/email",
                json=_payload(**{"from": "attacker@evil.com"}),
                headers=self.headers,
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")

    def test_free_plan_ignored(self) -> None:
        with patch("app.routers.email_ingest.email_ingest.lookup_by_token", return_value=_ADDR), \
             patch("app.routers.email_ingest.quota.get_effective_plan", return_value=_FREE):
            resp = self.client.post("/ingest/email", json=_payload(), headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")

    def test_plan_lookup_failure_fails_closed(self) -> None:
        with patch("app.routers.email_ingest.email_ingest.lookup_by_token", return_value=_ADDR), \
             patch("app.routers.email_ingest.quota.get_effective_plan", return_value=None):
            resp = self.client.post("/ingest/email", json=_payload(), headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")

    def test_pro_attachment_accepted(self) -> None:
        with patch("app.routers.email_ingest.email_ingest.lookup_by_token", return_value=_ADDR), \
             patch("app.routers.email_ingest.quota.get_effective_plan", return_value=_PRO), \
             patch(
                 "app.routers.email_ingest.email_ingest.ingest_email_attachment",
                 return_value={"status": "accepted", "filename": "doc.pdf", "doc_id": "d1", "job_id": "j1"},
             ) as ing, \
             patch("app.routers.email_ingest._increment_docs_counter") as inc:
            resp = self.client.post("/ingest/email", json=_payload(), headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "processed")
        self.assertEqual(body["results"][0]["status"], "accepted")
        ing.assert_called_once()
        inc.assert_called_once()

    def test_no_attachments_ignored(self) -> None:
        with patch("app.routers.email_ingest.email_ingest.lookup_by_token", return_value=_ADDR), \
             patch("app.routers.email_ingest.quota.get_effective_plan", return_value=_PRO):
            resp = self.client.post(
                "/ingest/email", json=_payload(attachments=[]), headers=self.headers
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_email_ingest_routes -v`
Expected: FAIL — 404 (라우터 없음)

- [ ] **Step 3: webhook 라우터 구현**

`api/app/routers/email_ingest.py`:

```python
"""수익화 W4 — 이메일 인제스트 webhook (Cloudflare Email Worker → 백엔드).

인증 = X-Jetrag-Webhook-Secret 공유 secret (JWT 아님 — 발신자는 Worker).
거절 정책 = 조용히 무시(200 + warning 로그) — Worker 재시도·반송 메일 회피.
단 secret 불일치는 401 (Worker 설정 오류는 시끄럽게 실패해야 발견됨).
"""
from __future__ import annotations

import base64
import binascii
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.db import get_supabase_client
from app.services import email_ingest, quota

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["email-ingest"])


class EmailAttachmentIn(BaseModel):
    filename: str = "attachment"
    content_type: str = "application/octet-stream"
    content_base64: str


class EmailWebhookPayload(BaseModel):
    to: str
    from_: str = Field(alias="from")
    subject: str = ""
    attachments: list[EmailAttachmentIn] = []


class EmailWebhookResponse(BaseModel):
    status: str  # "processed" | "ignored"
    results: list[dict] = []


def _increment_docs_counter(user_id: str) -> None:
    """W2 abuse cap 카운터와 정합 — 이메일 인제스트도 docs 로 센다 (best-effort)."""
    try:
        get_supabase_client().rpc(
            "increment_usage_counter",
            {
                "p_user_key": user_id,
                "p_metric": "docs",
                "p_period_date": datetime.now(timezone.utc).date().isoformat(),
            },
        ).execute()
    except Exception as exc:  # noqa: BLE001 — 카운터 실패가 인제스트를 막지 않음
        logger.warning("email_ingest docs 카운터 실패 (user=%s): %s", user_id, exc)


@router.post("/email", response_model=EmailWebhookResponse)
def email_webhook(
    payload: EmailWebhookPayload,
    background_tasks: BackgroundTasks,
    x_jetrag_webhook_secret: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> EmailWebhookResponse:
    if not settings.email_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="이메일 인제스트가 비활성 상태입니다 (JETRAG_EMAIL_WEBHOOK_SECRET 미설정).",
        )
    if x_jetrag_webhook_secret != settings.email_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="webhook secret 불일치",
        )

    token = email_ingest.parse_token(payload.to)
    if token is None:
        logger.warning("email_ingest ignore — 잘못된 수신 주소: %s", payload.to)
        return EmailWebhookResponse(status="ignored")

    addr = email_ingest.lookup_by_token(token)
    if addr is None:
        logger.warning("email_ingest ignore — 알 수 없는 토큰: %s...", token[:4])
        return EmailWebhookResponse(status="ignored")

    if not email_ingest.sender_allowed(payload.from_, addr.get("owner_email")):
        logger.warning(
            "email_ingest ignore — 발신자 불일치 (user=%s, from=%s)",
            addr["user_id"], payload.from_,
        )
        return EmailWebhookResponse(status="ignored")

    plan = quota.get_effective_plan(str(addr["user_id"]))
    if plan is None or plan.code != "pro":
        # 쓰기 경로 — 플랜 조회 실패도 거절 (fail-closed).
        logger.warning(
            "email_ingest ignore — Pro 아님 (user=%s, plan=%s)",
            addr["user_id"], getattr(plan, "code", None),
        )
        return EmailWebhookResponse(status="ignored")

    if not payload.attachments:
        logger.warning("email_ingest ignore — 첨부 없음 (user=%s)", addr["user_id"])
        return EmailWebhookResponse(status="ignored")

    user_id = str(addr["user_id"])
    results: list[dict] = []
    for att in payload.attachments:
        try:
            raw = base64.b64decode(att.content_base64, validate=True)
        except (binascii.Error, ValueError):
            results.append({"status": "skipped", "filename": att.filename, "reason": "base64 오류"})
            continue
        result = email_ingest.ingest_email_attachment(
            user_id=user_id,
            filename=att.filename,
            content_type=att.content_type,
            raw=raw,
            background_tasks=background_tasks,
        )
        if result["status"] == "accepted":
            _increment_docs_counter(user_id)
        results.append(result)

    logger.info(
        "email_ingest processed — user=%s, 첨부 %d건: %s",
        user_id, len(results), [r["status"] for r in results],
    )
    return EmailWebhookResponse(status="processed", results=results)
```

> W3 보유 문서 한도(Pro 200)는 `ingest_email_attachment` 진입 전 체크를 추가하지 않는다 — dedup·비허용 skip 이 흔해 첨부별 카운트 쿼리가 과함. Pro 200 은 여유가 크고, 웹 업로드 402 게이트가 총량을 이미 막는다. 필요 시 후속에서 `quota.count_active_documents` 체크 1줄 추가.

- [ ] **Step 4: 라우터 등록**

`api/app/routers/__init__.py`:

```python
from .email_ingest import router as email_ingest_router
```

`__all__` 에 `"email_ingest_router",` 추가.

`api/app/main.py` — import 목록에 `email_ingest_router,` 추가 + include 블록에:

```python
# 수익화 W4 — 이메일 인제스트 webhook (Cloudflare Email Worker 전용, shared secret 인증).
app.include_router(email_ingest_router)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_email_ingest_routes -v`
Expected: PASS (8 tests)

- [ ] **Step 6: 커밋**

```bash
git add api/app/routers/email_ingest.py api/app/routers/__init__.py api/app/main.py api/tests/test_email_ingest_routes.py
git commit -m "feat(email-w4): POST /ingest/email webhook — secret 인증 + Pro 게이트 + 조용한 거절"
```

---

## Task 5: `/me/email-ingest` — 주소 확인·재발급

**Files:**
- Modify: `api/app/routers/me.py`
- Modify: `api/tests/test_email_ingest_routes.py` (클래스 추가)

- [ ] **Step 1: 실패하는 테스트 추가**

`api/tests/test_email_ingest_routes.py` 에 추가 (기존 import 재사용 + 상단 import 에 `from app.auth.dependencies import CurrentUser, get_current_user` 추가):

```python
class MeEmailIngestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.authed = CurrentUser(
            user_id="uid-1", email="user@gmail.com", is_authenticated=True
        )
        app.dependency_overrides[get_current_user] = lambda: self.authed
        app.dependency_overrides[get_settings] = lambda: _settings()
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_get_returns_address_and_plan(self) -> None:
        with patch(
            "app.routers.me.email_ingest.get_or_create_address", return_value=_ADDR
        ), patch("app.routers.me.quota.get_effective_plan", return_value=_PRO):
            resp = self.client.get("/me/email-ingest")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["address"], "u-abc12345@in.woong-s.com")
        self.assertTrue(body["pro"])

    def test_get_free_user_sees_pro_false(self) -> None:
        with patch(
            "app.routers.me.email_ingest.get_or_create_address", return_value=_ADDR
        ), patch("app.routers.me.quota.get_effective_plan", return_value=_FREE):
            resp = self.client.get("/me/email-ingest")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["pro"])

    def test_rotate_returns_new_address(self) -> None:
        new_addr = {"user_id": "uid-1", "token": "zzz99999", "owner_email": "user@gmail.com"}
        with patch(
            "app.routers.me.email_ingest.rotate_address", return_value=new_addr
        ), patch("app.routers.me.quota.get_effective_plan", return_value=_PRO):
            resp = self.client.post("/me/email-ingest/rotate")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["address"], "u-zzz99999@in.woong-s.com")

    def test_anonymous_401(self) -> None:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="owner", is_authenticated=False
        )
        resp = self.client.get("/me/email-ingest")
        self.assertEqual(resp.status_code, 401)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run python -m unittest tests.test_email_ingest_routes.MeEmailIngestTest -v`
Expected: FAIL — 404

- [ ] **Step 3: me.py 확장**

`api/app/routers/me.py` — import 에 추가:

```python
from app.config import Settings, get_settings
from app.services import email_ingest
```

파일 하단에 추가:

```python
class EmailIngestAddressResponse(BaseModel):
    address: str
    pro: bool
    plan_code: str


def _address_response(row: dict, user_id: str, settings: Settings) -> EmailIngestAddressResponse:
    plan = quota.get_effective_plan(user_id)
    return EmailIngestAddressResponse(
        address=email_ingest.build_address(row["token"], settings.email_ingest_domain),
        pro=plan is not None and plan.code == "pro",
        plan_code=plan.code if plan is not None else "unknown",
    )


@router.get("/email-ingest", response_model=EmailIngestAddressResponse)
def me_email_ingest(
    current_user: CurrentUserDep = LEGACY_DEFAULT_USER,
    settings: Settings = Depends(get_settings),
) -> EmailIngestAddressResponse:
    """본인 이메일 인제스트 주소 (없으면 발급). 수신 처리 자체는 Pro 전용 —
    Free 유저에게도 주소는 보여주되 pro=false 로 업그레이드 안내를 띄운다."""
    row = email_ingest.get_or_create_address(current_user.user_id, current_user.email)
    return _address_response(row, current_user.user_id, settings)


@router.post("/email-ingest/rotate", response_model=EmailIngestAddressResponse)
def me_email_ingest_rotate(
    current_user: CurrentUserDep = LEGACY_DEFAULT_USER,
    settings: Settings = Depends(get_settings),
) -> EmailIngestAddressResponse:
    """토큰 재발급 — 스팸·유출 대응. 구 주소 즉시 무효."""
    row = email_ingest.rotate_address(current_user.user_id, current_user.email)
    return _address_response(row, current_user.user_id, settings)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run python -m unittest tests.test_email_ingest_routes -v`
Expected: PASS (12 tests)

- [ ] **Step 5: 커밋**

```bash
git add api/app/routers/me.py api/tests/test_email_ingest_routes.py
git commit -m "feat(me-w4): 이메일 인제스트 주소 확인·재발급 endpoint"
```

---

## Task 6: Cloudflare Email Worker

**Files:**
- Create: `workers/email-ingest/src/index.js`
- Create: `workers/email-ingest/wrangler.toml`
- Create: `workers/email-ingest/package.json`

> Worker 는 Python 테스트 대상 아님 — 검증은 Task 8 롤아웃의 실메일 e2e. 코드는 repo 에 커밋해 재배포 재현성 확보.

- [ ] **Step 1: package.json**

`workers/email-ingest/package.json`:

```json
{
  "name": "jetrag-email-ingest",
  "private": true,
  "version": "0.1.0",
  "description": "Jet-Rag W4 — Email Routing 수신 메일을 백엔드 webhook 으로 전달",
  "dependencies": {
    "postal-mime": "^2.3.2"
  }
}
```

- [ ] **Step 2: wrangler.toml**

`workers/email-ingest/wrangler.toml`:

```toml
name = "jetrag-email-ingest"
main = "src/index.js"
compatibility_date = "2026-07-01"

[vars]
JETRAG_API_URL = "https://jetrag-api.woong-s.com"
# JETRAG_EMAIL_WEBHOOK_SECRET 는 var 가 아니라 secret:
#   npx wrangler secret put JETRAG_EMAIL_WEBHOOK_SECRET
```

- [ ] **Step 3: Worker 구현**

`workers/email-ingest/src/index.js`:

```javascript
// Jet-Rag W4 — Cloudflare Email Worker.
// Email Routing(catch-all @in.woong-s.com) → 본 Worker → 백엔드 POST /ingest/email.
// 검증(토큰·발신자·플랜)은 전부 백엔드가 담당 — Worker 는 파싱·전달만.
import PostalMime from 'postal-mime';

function toBase64(arrayBuffer) {
  // 대용량 첨부에서 String.fromCharCode(...spread) 는 스택 초과 — 청크 처리.
  const bytes = new Uint8Array(arrayBuffer);
  let binary = '';
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

export default {
  async email(message, env, ctx) {
    try {
      const email = await new PostalMime().parse(message.raw);
      const attachments = (email.attachments || []).map((a) => ({
        filename: a.filename || 'attachment',
        content_type: a.mimeType || 'application/octet-stream',
        content_base64: toBase64(a.content),
      }));
      const payload = {
        to: message.to,
        from: message.from,
        subject: email.subject || '',
        attachments,
      };
      const resp = await fetch(`${env.JETRAG_API_URL}/ingest/email`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Jetrag-Webhook-Secret': env.JETRAG_EMAIL_WEBHOOK_SECRET,
        },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        console.error(`jetrag webhook 실패: ${resp.status} ${await resp.text()}`);
      }
    } catch (err) {
      // 실패해도 메일 반송(setReject)하지 않음 — 거절 정책 '조용히 무시'와 정합.
      console.error(`jetrag email worker 오류: ${err}`);
    }
  },
};
```

- [ ] **Step 4: 커밋**

```bash
git add workers/email-ingest
git commit -m "feat(worker-w4): Cloudflare Email Worker — MIME 파싱 후 webhook 전달"
```

---

## Task 7: 프론트 `/settings` — 플랜 + 인제스트 주소

**Files:**
- Create: `web/src/app/settings/page.tsx`

- [ ] **Step 1: 기존 페이지 컨벤션 확인**

`web/src/app/docs/page.tsx` 와 전역 레이아웃/네비게이션(`web/src/app/layout.tsx` 및 헤더 컴포넌트 — `Grep -l "href=\"/docs\"" web/src` 로 위치 확정)을 Read — 컨테이너 클래스·에러 처리·로딩 패턴을 아래 baseline 에 맞춰 조정한다.

- [ ] **Step 2: settings 페이지 작성**

`web/src/app/settings/page.tsx` (baseline — Step 1 에서 확인한 프로젝트 스타일 클래스로 조정):

```tsx
'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiGet, apiPost } from '@/lib/api/client';

interface MePlan {
  plan_code: string;
  max_documents: number;
  answers_per_day: number;
  answers_used_today: number;
  documents_count: number;
}

interface EmailIngest {
  address: string;
  pro: boolean;
  plan_code: string;
}

export default function SettingsPage() {
  const [plan, setPlan] = useState<MePlan | null>(null);
  const [email, setEmail] = useState<EmailIngest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rotating, setRotating] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, e] = await Promise.all([
        apiGet<MePlan>('/me/plan'),
        apiGet<EmailIngest>('/me/email-ingest'),
      ]);
      setPlan(p);
      setEmail(e);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '설정을 불러오지 못했습니다. 로그인 상태를 확인해 주세요.');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const rotate = async () => {
    if (!window.confirm('주소를 재발급하면 기존 주소는 즉시 무효화됩니다. 계속할까요?')) return;
    setRotating(true);
    try {
      const e = await apiPost<EmailIngest>('/me/email-ingest/rotate');
      setEmail(e);
    } catch {
      setError('주소 재발급에 실패했습니다. 잠시 후 다시 시도해 주세요.');
    } finally {
      setRotating(false);
    }
  };

  return (
    <main className="mx-auto max-w-2xl px-4 py-8">
      <h1 className="text-2xl font-bold">설정</h1>

      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

      <section className="mt-6 rounded-lg border p-4">
        <h2 className="font-semibold">내 플랜</h2>
        {plan ? (
          <ul className="mt-2 space-y-1 text-sm">
            <li>플랜: <strong>{plan.plan_code === 'pro' ? 'Pro' : 'Free'}</strong></li>
            <li>오늘 답변: {plan.answers_used_today} / {plan.answers_per_day}회</li>
            <li>보유 문서: {plan.documents_count} / {plan.max_documents}개</li>
          </ul>
        ) : (
          <p className="mt-2 text-sm text-gray-500">불러오는 중…</p>
        )}
      </section>

      <section className="mt-6 rounded-lg border p-4">
        <h2 className="font-semibold">이메일로 문서 보내기</h2>
        {email ? (
          <>
            <p className="mt-2 text-sm">
              아래 주소로 첨부파일(PDF·HWP·HWPX·DOCX·이미지)을 보내면 자동으로 수집됩니다.
              <strong> 가입한 이메일에서 보낸 메일만</strong> 처리됩니다.
            </p>
            <code className="mt-2 block select-all rounded bg-gray-100 px-3 py-2 text-sm">
              {email.address}
            </code>
            {!email.pro && (
              <p className="mt-2 text-sm text-amber-600">
                이메일 인제스트는 Pro 전용 기능입니다. 업그레이드 후 이용해 주세요.
              </p>
            )}
            <button
              type="button"
              onClick={() => void rotate()}
              disabled={rotating}
              className="mt-3 rounded border px-3 py-1 text-sm disabled:opacity-50"
            >
              {rotating ? '재발급 중…' : '주소 재발급 (스팸 대응)'}
            </button>
          </>
        ) : (
          <p className="mt-2 text-sm text-gray-500">불러오는 중…</p>
        )}
      </section>
    </main>
  );
}
```

- [ ] **Step 3: 네비게이션 링크 추가**

Step 1 에서 찾은 헤더/네비 컴포넌트에 기존 링크와 동일 스타일로 추가 (로그인 상태에서만 노출하는 조건이 기존에 있으면 동일 조건 적용):

```tsx
<Link href="/settings">설정</Link>
```

- [ ] **Step 4: 수동 검증 (dev 서버)**

```bash
cd web && npm run dev
```

브라우저 `http://localhost:3001/settings` (백엔드 로컬 기동 or `NEXT_PUBLIC_API_BASE_URL` 지정) — 로그인 후 플랜 카드·주소 표시·재발급 버튼 동작 확인. 로그아웃 상태 에러 메시지 확인.

- [ ] **Step 5: 커밋**

```bash
git add web/src/app/settings
git commit -m "feat(web-w4): /settings — 플랜 사용량 + 이메일 인제스트 주소 UI"
# 네비 수정 파일도 함께 add (Step 3 에서 확정된 경로)
```

---

## Task 8: production 롤아웃 (수동 — 사용자 액션 포함)

> 이 태스크는 서브에이전트가 실행하지 않음. 컨트롤러가 사용자와 함께 수행.

- [ ] **Step 1: 마이그레이션 023 적용**

Supabase Studio → SQL Editor → New query 빈 탭 → `023_email_ingest_addresses.sql` paste → Run → 헤더 검증 SQL.

- [ ] **Step 2: secret 생성 + Railway ENV**

```bash
openssl rand -hex 32   # → <SECRET>
```

Railway → jetrag-api → Variables → `JETRAG_EMAIL_WEBHOOK_SECRET=<SECRET>` 저장 → **보라색 Deploy 클릭** (저장만으론 재배포 안 됨 — `railway_deploy_gotcha`).

- [ ] **Step 3: `git push origin main`** (백엔드 + Vercel 프론트 재배포)

- [ ] **Step 4: Cloudflare Email Routing + Worker 배포**

1. Cloudflare dashboard → `woong-s.com` zone → Email → Email Routing → **Enable** → 서브도메인 `in.woong-s.com` 추가 시 안내되는 **MX/TXT(SPF) 레코드 적용** (Email Routing 이 자동 제안).
2. Worker 배포:

```bash
cd workers/email-ingest && npm install
npx wrangler secret put JETRAG_EMAIL_WEBHOOK_SECRET   # Step 2 와 같은 값
npx wrangler deploy
```

3. Email Routing → Routing rules → `in.woong-s.com` **Catch-all** action = Send to Worker `jetrag-email-ingest`.

- [ ] **Step 5: e2e 실메일 검증**

1. 웹 `/settings` 에서 본인 주소 확인 (OWNER 는 W3 에서 pro upsert 됨).
2. 가입 이메일에서 해당 주소로 PDF 1개 첨부 발송.
3. Railway 로그에서 `email_ingest processed` 확인 → 웹 문서 리스트에 `source_channel=email` 문서 등장 → 검색/답변 동작 확인.
4. 거절 경로: 다른 이메일 계정에서 발송 → 문서 미생성 + `발신자 불일치` warning 로그 확인.

- [ ] **Step 6: 문서화 + 베타 안내**

- `.env.example`: `JETRAG_EMAIL_WEBHOOK_SECRET=` / `JETRAG_EMAIL_INGEST_DOMAIN=in.woong-s.com` 주석 추가.
- `README.md` 운영 모드에 이메일 인제스트 bullet 추가.
- 베타 유저(Pro 체험 부여분)에게 이메일 인제스트 사용법 안내 — 베타 피드백 1순위 응답임을 명시.

```bash
git add .env.example README.md
git commit -m "docs(w4): 이메일 인제스트 ENV·운영 모드 문서화"
git push origin main
```

---

## Self-Review (플랜 작성자 체크리스트 결과)

**1. Spec coverage** (스펙 §2 "이메일 인제스트 W3-4"):
- 수신 경로 CF Email Routing → Worker → webhook → Task 6 + 8. ✅
- 주소 체계 `u-{8자리 토큰}@in.woong-s.com` + 매핑 테이블(마이그 023) + 재발급 → Task 1, 3, 5. ✅
- 발신자 검증(가입 이메일 화이트리스트) → Task 3 `sender_allowed` (owner_email 저장 방식 — admin API 불필요로 단순화). ✅
- 첨부 추출 + 기존 파이프라인 재사용(magic bytes·SHA-256 dedup·50MB cap) → Task 3 `ingest_email_attachment` + Task 4. ✅
- Pro 게이트 → Task 4 (fail-closed). Free 발신 시 "안내 메일 회신"은 사용자 확정으로 **로그만 남기고 무시**로 변경. ✅
- Drive 동기화/Android 폴더 감시 → 스펙 명시 후속 sprint — 제외. ✅

**2. Placeholder scan:** 전 스텝 실코드·명령 포함. "확인 후 조정" 은 프론트 스타일 클래스·네비 컴포넌트 위치(Task 7 Step 1 에서 Read 로 확정) 한정. ✅

**3. Type consistency:** `generate_token()` / `build_address(token, domain)` / `parse_token(to)` / `lookup_by_token(token) -> dict|None` / `sender_allowed(from, owner_email)` / `get_or_create_address(user_id, email)` / `rotate_address(user_id, email)` / `ingest_email_attachment(user_id, filename, content_type, raw, background_tasks) -> dict` — 서비스·라우터·테스트 시그니처 일치. webhook 응답 `status: processed|ignored`, 첨부 결과 `accepted|duplicated|skipped` 일관. W3 의 `quota.get_effective_plan`/`PlanLimits` 시그니처와 일치. ✅

**주의 (구현자 확인 필요):**
- `app.ingest` 가 `create_job`/`run_full_ingest` 를 export 하는지 (documents.py:44-50 과 동일 import — 가능).
- `validate_magic` 이 HTTPException 을 던지는 시그니처인지 `app/routers/_input_gate.py` Read 로 확정 (다른 예외면 Task 3 의 except 타입 조정).
- `documents.flags` 에 `ingest_mode: "default"` 리터럴 대신 `_flags_with_ingest_mode` 를 쓰려면 documents.py 의 private 헬퍼 import 이 필요 — 리터럴 dict 로 동일 결과라 의도적으로 미사용.
- Cloudflare Email Routing 메시지 상한 25MiB — 초과 메일은 CF 단에서 반송됨 (백엔드 도달 전). 베타 안내문에 명시.
- `postal-mime` 버전은 배포 시점 latest 확인 (`npm view postal-mime version`).
