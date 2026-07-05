# W1 — API 키 회전 + 멀티유저 쓰기 복원 (데모 병행) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 노출된 고권한 키를 회전하고, Portfolio Mode C+ (read-only 데모)를 "익명 데모 + 로그인 격리 병행" 모드로 전환 — 로그인 유저는 본인 문서 업로드/격리, 익명 방문자는 기존 owner 12-doc 데모 유지.

**Architecture:** 백엔드 `get_current_user` 가 3-way 분기(익명 → owner fallback read-only / JWT → 본인 격리 / 무효 토큰 → 401). 쓰기 7 endpoint 는 신규 `require_authenticated_user` 로 게이트 (기존 `forbid_demo_writes` 대체). 프론트는 보존된 주석 블록 복원하되, proxy 게이트는 `/ingest` 만 보호(데모 병행). RLS(마이그 019)는 이미 적용 상태 — 백엔드 user_id 필터와 이중 방어.

**Tech Stack:** FastAPI + Python 3.12 (uv, unittest) / Next.js 16 + React 19 / Supabase Auth (ES256 JWKS) / Railway ENV

**스펙:** `docs/superpowers/specs/2026-07-05-monetization-sprint-design.md` (W1 트랙 A)

**사용자 확정 결정 (2026-07-05):** 익명 방문자 = 데모 유지 + 로그인 병행.

---

## 사전 확인

- [ ] 작업 시작 전 전체 테스트 baseline 확보:

```bash
cd api && uv run python -m unittest discover tests 2>&1 | tail -3
```

Expected: `OK` (1336+ tests, 기존 flaky 제외). 실패 수를 기록해두고 작업 후 동일한지 비교.

---

### Task 1: [수동/ops] 노출 API 키 회전

> 코드 작업 아님 — 사용자가 dashboard 에서 직접 수행. 근거: `work-log/2026-05-21 TODO — Phase 4 노출 API 키 회전 필수.md` §4 체크리스트. **Task 2 이후 작업과 독립 — 가장 먼저 수행.**

- [ ] **Step 1: Supabase service_role key 재발급**

<https://supabase.com/dashboard/project/mpmtydudhojpukuuadrd/settings/api> → JWT Settings → `Service Role Secret` 행 ⋮ → **Generate new secret**. 기존 키 즉시 무효화되므로 Step 2를 1~2분 내 이어서 수행.

- [ ] **Step 2: Railway ENV 갱신 + Deploy**

<https://railway.com> → Jet-Rag → backend service → Variables → `SUPABASE_SERVICE_ROLE_KEY` 신규 키로 교체 → **좌상단 보라색 Deploy 클릭** (Variables 저장만으론 redeploy 안 됨 — 기존 함정). 로컬 `.env` 의 `SUPABASE_SERVICE_ROLE_KEY` 도 동일 갱신.

- [ ] **Step 3: 검증**

```bash
curl -s -o /dev/null -w "/health: %{http_code}\n" "https://jetrag-api.woong-s.com/health"
curl -s -o /dev/null -w "/search: %{http_code}\n" "https://jetrag-api.woong-s.com/search?q=test"
```

Expected: `/health: 200`, `/search: 200` (현재 데모 모드 기준). 503/500 이면 Railway Deployments → Logs 에서 `PGRST301`/`401` 확인.

- [ ] **Step 4: Railway account token 삭제**

<https://railway.com/account/tokens> → `woongmin projects token` 🗑 삭제.

- [ ] **Step 5: 구 키 무효화 확인 + 문서 archive**

work-log TODO 문서 §3b 의 `OLD_SBKEY` curl 실행 → `401` 기대. 이후:

```bash
mkdir -p work-log/archive && git mv "work-log/2026-05-21 TODO — Phase 4 노출 API 키 회전 필수.md" work-log/archive/ && git commit -m "chore(security): 노출 키 회전 완료 — TODO 문서 archive"
```

---

### Task 2: CurrentUser.is_authenticated + get_current_user 3-way 분기

**Files:**
- Modify: `api/app/auth/dependencies.py:36-114`
- Test: `api/tests/test_auth_demo_fallback.py` (Create)

- [ ] **Step 1: Write the failing test**

`api/tests/test_auth_demo_fallback.py` 생성:

```python
"""수익화 W1 — 데모 병행 모드 get_current_user 3-way 분기 테스트.

- auth_enabled=false: default_user + is_authenticated=True (로컬 dev 쓰기 보존)
- auth_enabled=true + 토큰 없음: owner fallback + is_authenticated=False (익명 데모)
- auth_enabled=true + 유효 JWT: 본인 user_id + is_authenticated=True
- auth_enabled=true + 무효 JWT: 401
실행: `python -m unittest tests.test_auth_demo_fallback`
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("HF_API_TOKEN", "dummy-test-token")

from fastapi import HTTPException

from app.auth.dependencies import CurrentUser, get_current_user
from app.auth.jwt_verify import JWTValidationError, VerifiedToken
from app.config import Settings

_OWNER_ID = "11111111-1111-1111-1111-111111111111"
_DEFAULT_ID = "00000000-0000-0000-0000-000000000001"
_JWT_USER_ID = "22222222-2222-2222-2222-222222222222"


def _settings(auth_enabled: bool, owner: str | None = _OWNER_ID) -> Settings:
    return Settings(
        supabase_url="https://example.supabase.co",
        supabase_key="",
        supabase_service_role_key="",
        supabase_storage_bucket="documents",
        gemini_api_key="",
        hf_api_token="dummy-test-token",
        default_user_id=_DEFAULT_ID,
        doc_budget_usd=0.1,
        daily_budget_usd=0.5,
        sliding_24h_budget_usd=0.5,
        budget_krw_per_usd=1380.0,
        vision_need_score_enabled=True,
        vision_page_cap_per_doc=50,
        auth_enabled=auth_enabled,
        supabase_jwt_secret="test-secret",
        supabase_jwt_algorithm="HS256",
        owner_user_id=owner,
    )


class _FakeRequest:
    def __init__(self, bearer: str | None = None) -> None:
        self.headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
        self.cookies: dict[str, str] = {}


class DemoFallbackTest(unittest.TestCase):
    def test_auth_disabled_returns_default_user_authenticated(self) -> None:
        user = get_current_user(_FakeRequest(), _settings(auth_enabled=False))
        self.assertEqual(user.user_id, _DEFAULT_ID)
        self.assertTrue(user.is_authenticated)

    def test_anonymous_falls_back_to_owner_unauthenticated(self) -> None:
        user = get_current_user(_FakeRequest(), _settings(auth_enabled=True))
        self.assertEqual(user.user_id, _OWNER_ID)
        self.assertFalse(user.is_authenticated)

    def test_anonymous_without_owner_falls_back_to_default(self) -> None:
        user = get_current_user(
            _FakeRequest(), _settings(auth_enabled=True, owner=None)
        )
        self.assertEqual(user.user_id, _DEFAULT_ID)
        self.assertFalse(user.is_authenticated)

    def test_valid_jwt_returns_caller_authenticated(self) -> None:
        with patch(
            "app.auth.dependencies.verify_jwt",
            return_value=VerifiedToken(user_id=_JWT_USER_ID, email="a@b.co"),
        ):
            user = get_current_user(
                _FakeRequest(bearer="valid-token"), _settings(auth_enabled=True)
            )
        self.assertEqual(user.user_id, _JWT_USER_ID)
        self.assertTrue(user.is_authenticated)

    def test_invalid_jwt_raises_401(self) -> None:
        with patch(
            "app.auth.dependencies.verify_jwt",
            side_effect=JWTValidationError("bad token"),
        ):
            with self.assertRaises(HTTPException) as ctx:
                get_current_user(
                    _FakeRequest(bearer="bad-token"), _settings(auth_enabled=True)
                )
        self.assertEqual(ctx.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
```

> `VerifiedToken(user_id: str, email: str | None)` — `api/app/auth/jwt_verify.py:49-57` 에서 검증 완료.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd api && uv run python -m unittest tests.test_auth_demo_fallback -v
```

Expected: FAIL — `is_authenticated` 속성 없음 / 익명 fallback 이 `is_authenticated=True` 등.

- [ ] **Step 3: Implement — dependencies.py 수정**

`api/app/auth/dependencies.py` 의 `CurrentUser` 에 필드 추가:

```python
@dataclass(frozen=True)
class CurrentUser:
    """요청 호출자. user_id 는 격리 키 (RPC user_id_arg / documents.user_id 필터).

    - is_authenticated=False: 익명 데모 방문자 (owner read-only fallback) — 쓰기 불가.
    - is_authenticated=True: JWT 검증 통과 또는 auth_enabled=false 로컬 dev.
    """

    user_id: str
    email: str | None = None
    is_authenticated: bool = True
```

`get_current_user` 전체 교체 (기존 PORTFOLIO MODE early-return + 주석 블록 삭제):

```python
def get_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """호출자 식별 — 데모 병행 3-way 분기 (수익화 W1).

    - auth_enabled=false: 로컬 dev / single-user — default_user 로 쓰기 포함 전체 허용.
    - 토큰 없음: 익명 데모 — owner 문서 read-only (쓰기는 require_authenticated_user 가 차단).
    - 토큰 있음: JWT 검증 → 본인 격리 컨텍스트. 무효 토큰은 401 (조용한 데모 강등 금지).
    """
    if not settings.auth_enabled:
        return CurrentUser(user_id=settings.default_user_id, email=None)

    token = _extract_bearer_token(request) or _extract_cookie_token(request, settings)
    if token is None:
        return CurrentUser(
            user_id=settings.owner_user_id or settings.default_user_id,
            email=None,
            is_authenticated=False,
        )

    try:
        verified = verify_jwt(token, settings)
    except JWTValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return CurrentUser(user_id=verified.user_id, email=verified.email)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd api && uv run python -m unittest tests.test_auth_demo_fallback -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: 기존 스위트 회귀 확인 + Commit**

```bash
cd api && uv run python -m unittest discover tests 2>&1 | tail -3
```

Expected: baseline 과 동일 (신규 5 PASS 추가). 이후:

```bash
git add api/app/auth/dependencies.py api/tests/test_auth_demo_fallback.py
git commit -m "feat(auth-w1): get_current_user 3-way 분기 — 익명 데모 + 로그인 격리 병행"
```

---

### Task 3: require_authenticated_user 신설 + require_admin 익명 차단

**Files:**
- Modify: `api/app/auth/dependencies.py` (`require_admin`, 신규 함수)
- Modify: `api/app/auth/__init__.py` (export)
- Test: `api/tests/test_auth_demo_fallback.py` (추가), `api/tests/test_admin_gate.py` (케이스 추가)

> ⚠️ 보안 핵심: 익명 fallback 의 user_id 가 owner_user_id 와 동일하므로, `require_admin` 이 user_id 비교만 하면 **익명 방문자가 admin 통과**한다. `is_authenticated` 체크 필수.

- [ ] **Step 1: Write the failing tests**

`api/tests/test_auth_demo_fallback.py` 에 클래스 추가:

```python
from app.auth.dependencies import require_admin, require_authenticated_user


class RequireAuthenticatedUserTest(unittest.TestCase):
    def test_anonymous_demo_user_rejected_401(self) -> None:
        anon = CurrentUser(user_id=_OWNER_ID, is_authenticated=False)
        with self.assertRaises(HTTPException) as ctx:
            require_authenticated_user(current_user=anon)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_authenticated_user_passes(self) -> None:
        user = CurrentUser(user_id=_JWT_USER_ID, is_authenticated=True)
        self.assertEqual(require_authenticated_user(current_user=user), user)


class RequireAdminAnonymousTest(unittest.TestCase):
    def test_anonymous_owner_fallback_rejected_403(self) -> None:
        # 익명 fallback 은 user_id 가 owner 와 같아도 admin 불가 (보안 핵심).
        anon = CurrentUser(user_id=_OWNER_ID, is_authenticated=False)
        with self.assertRaises(HTTPException) as ctx:
            require_admin(current_user=anon, settings=_settings(auth_enabled=True))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_authenticated_owner_passes(self) -> None:
        owner = CurrentUser(user_id=_OWNER_ID, is_authenticated=True)
        result = require_admin(
            current_user=owner, settings=_settings(auth_enabled=True)
        )
        self.assertEqual(result.user_id, _OWNER_ID)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && uv run python -m unittest tests.test_auth_demo_fallback -v
```

Expected: FAIL — `require_authenticated_user` import 불가 + 익명 admin 403 케이스 FAIL.

- [ ] **Step 3: Implement**

`api/app/auth/dependencies.py` — `forbid_demo_writes` 함수(131-146행) 를 아래로 **교체**:

```python
def require_authenticated_user(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """쓰기 엔드포인트 게이트 (수익화 W1) — 익명 데모 방문자 401.

    익명 fallback 은 owner_user_id 컨텍스트라 이 게이트 없이는 owner 데이터에
    쓰기가 가능해진다 — 반드시 모든 write 엔드포인트에 걸 것.
    """
    if not current_user.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user
```

`require_admin` 의 auth_enabled=true 분기 수정:

```python
    if (
        not current_user.is_authenticated
        or not settings.owner_user_id
        or current_user.user_id != settings.owner_user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="운영자 권한이 필요합니다.",
        )
    return current_user
```

`api/app/auth/__init__.py` — `forbid_demo_writes` import/export 를 `require_authenticated_user` 로 교체.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd api && uv run python -m unittest tests.test_auth_demo_fallback -v
```

Expected: 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/app/auth/dependencies.py api/app/auth/__init__.py api/tests/test_auth_demo_fallback.py
git commit -m "feat(auth-w1): require_authenticated_user 게이트 + require_admin 익명 fallback 차단"
```

---

### Task 4: 라우터 7곳 게이트 교체 + demo_readonly 제거

**Files:**
- Modify: `api/app/routers/documents.py:40,399,584,785,874`
- Modify: `api/app/routers/answer.py:46,595,752,902`
- Modify: `api/app/config.py:88-90,203-204` (demo_readonly 필드 + parse 제거)
- Test: `api/tests/test_auth_protected_routes.py` (skip 해제 + 재작성), `api/tests/test_admin_gate.py` (skip 해제)

- [ ] **Step 1: 게이트 교체 (기계적 치환)**

두 라우터에서 import 와 dependency 치환:

```python
# documents.py:40 / answer.py:46 — import 교체
from app.auth import require_authenticated_user  # 기존: forbid_demo_writes

# 7개 endpoint decorator — 치환 (documents.py 4곳: 399,584,785,874 / answer.py 3곳: 595,752,902)
dependencies=[Depends(require_authenticated_user)],  # 쓰기 = 로그인 필수 (수익화 W1)
```

검증:

```bash
cd api && grep -rn "forbid_demo_writes\|demo_readonly\|JETRAG_DEMO_READONLY" app/ | grep -v ".pyc"
```

Expected: config.py 수정 전이면 config 만 남음. Step 2 후 출력 0줄.

- [ ] **Step 2: config.py 에서 demo_readonly 제거**

`api/app/config.py:88-90` 의 `demo_readonly: bool = False` 필드(주석 포함)와 `:203-204` 의 `demo_readonly=_parse_bool("JETRAG_DEMO_READONLY", False),` 줄 삭제.

- [ ] **Step 3: 기존 skip 테스트 복원**

`api/tests/test_auth_protected_routes.py` — `@unittest.skip(...)` (65-68행) 제거. 데모 병행 설계에 맞게 기대치 재작성: 익명 GET (`/documents`, `/search?q=`, `/stats`) 은 **401 이 아니라 게이트 통과** (owner fallback — dependency_overrides 로 외부 호출 차단 유지), 익명 POST 쓰기 7 endpoint 는 **401**. 파일 상단 docstring 도 새 정책으로 갱신. 기존 `_auth_enabled_settings()` 헬퍼는 재사용.

`api/tests/test_admin_gate.py` — skip 데코레이터 있으면 제거, `auth_enabled=true` 케이스가 `CurrentUser(is_authenticated=True)` 를 명시 주입하도록 갱신.

- [ ] **Step 4: Run full suite**

```bash
cd api && uv run python -m unittest discover tests 2>&1 | tail -3
```

Expected: `OK` — baseline + 신규/복원 테스트 전부 PASS. 실패 시 실패 테스트를 새 정책 기준으로 수정 (단, dependencies/라우터 구현이 이 plan 과 다르게 동작하면 구현 버그 — 구현을 고칠 것).

- [ ] **Step 5: Commit**

```bash
git add api/app/routers/documents.py api/app/routers/answer.py api/app/config.py api/tests/test_auth_protected_routes.py api/tests/test_admin_gate.py
git commit -m "feat(auth-w1): 쓰기 7 endpoint 로그인 게이트 전환 + Portfolio demo_readonly 제거"
```

---

### Task 5: 프론트 복원 — 로그인 병행 + /ingest 보호

**Files:**
- Modify: `web/src/proxy.ts` (주석 해제 + 보호 범위 축소)
- Modify: `web/src/app/layout.tsx:7-9,50-56,63-67` (AuthProvider 복원)
- Modify: `web/src/components/jet-rag/header.tsx`, `header-mobile-panel.tsx`, `hero-section.tsx` (로그인/업로드 버튼 주석 해제)
- Modify: `web/src/app/ingest/page.tsx` (redirect 제거, IngestPage 복원)
- Modify: `web/src/app/share/route.ts:70` (NOTE 의 cookie forward 적용)

> 각 파일의 `PORTFOLIO MODE ... 복원 시` 주석이 복원 코드를 그대로 보존하고 있다 — 마커를 따라 주석 해제. **단 proxy.ts 는 보존 블록을 그대로 쓰지 말 것** (전 경로 로그인 강제 → 데모 죽음).

- [ ] **Step 1: proxy.ts — 세션 리프레시 복원 + 보호는 /ingest 만**

주석 블록 해제 후 보호 로직을 아래로 교체 (PUBLIC_PATHS 화이트리스트 → PROTECTED_PATHS 블랙리스트 반전):

```typescript
// 데모 병행 (수익화 W1) — 익명은 검색·답변·docs 데모 가능. 업로드 화면만 로그인 강제.
const PROTECTED_PATHS = ['/ingest'];

function isProtectedPath(pathname: string): boolean {
  return PROTECTED_PATHS.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`),
  );
}
```

`proxy()` 본문: 최상단 `return NextResponse.next({ request });` 삭제 → 보존 블록 주석 해제 → 리다이렉트 조건만 교체:

```typescript
  // 보호 경로 미인증 → /login (원래 목적지 returnTo 로 보존). 그 외 경로는 익명 데모 통과.
  if (!user && isProtectedPath(pathname)) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    url.search = '';
    url.searchParams.set('returnTo', pathname);
    return NextResponse.redirect(url);
  }
```

`user && (pathname === '/login' || ...)` → 홈 리다이렉트 블록과 `Cache-Control: private, no-store` 는 보존 블록 그대로 복원.

- [ ] **Step 2: layout.tsx — AuthProvider 복원**

7-9행 import 주석 해제, 50-56행 `getCurrentUser()` 블록 주석 해제, 63행 마커 위치에서 `<ActiveDocsProvider>` 를 `<AuthProvider user={authUser}>` 로 wrap.

- [ ] **Step 3: header / hero / mobile-panel / ingest / share 복원**

각 파일의 `복원 시` 마커 지시 그대로:
- `header.tsx` — Upload/LogIn/LogOut import + `useAuth()` + 업로드·로그인/로그아웃 버튼 블록 주석 해제
- `header-mobile-panel.tsx` — Link/Upload import + 모바일 업로드 버튼 주석 해제
- `hero-section.tsx` — Upload import + 업로드 버튼 블록(95행 마커) 주석 해제. **추천 query 칩은 삭제하지 말 것** (익명 데모 유지)
- `ingest/page.tsx` — redirect 주석 처리, IngestPage 본문 주석 해제
- `share/route.ts` — 70행 NOTE 에 적힌 auth cookie forward 코드 적용 (PWA share_target 이 로그인 유저 업로드로 동작)

- [ ] **Step 4: 타입·린트 검증**

```bash
cd web && pnpm tsc --noEmit && pnpm lint
```

Expected: 오류 0. (`@/lib/auth/*`, `/login` 페이지는 W31 D1 때 구현되어 repo 에 존재 — import 오류가 나면 `web/src/lib/auth/` 와 `web/src/app/login/` 실재 여부 먼저 확인.)

- [ ] **Step 5: 로컬 e2e 수동 검증**

터미널 2개:

```bash
cd api && uv run uvicorn app.main:app --reload   # :8000
cd web && pnpm dev                                # :3000
```

브라우저 확인 (로컬은 `JETRAG_AUTH_ENABLED` 미설정 = auth off — `.env` 에 `JETRAG_AUTH_ENABLED=true` 임시 설정 후):
1. 익명으로 `/` → 검색 → 결과 표시 (owner 데모)
2. 익명으로 `/ingest` → `/login` 리다이렉트
3. 로그인 → `/ingest` → 업로드 UI 표시
4. 익명 상태에서 `curl -X POST localhost:8000/documents` → 401

- [ ] **Step 6: Commit**

```bash
git add web/src/proxy.ts web/src/app/layout.tsx web/src/app/ingest/page.tsx web/src/app/share/route.ts web/src/components/jet-rag/header.tsx web/src/components/jet-rag/header-mobile-panel.tsx web/src/components/jet-rag/hero-section.tsx
git commit -m "feat(web-w1): 로그인 병행 복원 — /ingest 보호 + 익명 데모 유지"
```

---

### Task 6: production 전환 + smoke

**Files:** 없음 (ENV + 검증만)

- [ ] **Step 1: Railway ENV 전환**

Railway → backend → Variables:
- `JETRAG_DEMO_READONLY` **삭제**
- `JETRAG_AUTH_ENABLED=true`
- `SUPABASE_JWT_ALGORITHM=ES256` + `SUPABASE_JWKS_URL=https://mpmtydudhojpukuuadrd.supabase.co/auth/v1/.well-known/jwks.json` (신규 프로젝트 ECC 서명 — HS256 불가)
- `OWNER_USER_ID` 기존 값 유지 확인
- **Deploy 클릭** (저장만으론 반영 안 됨)

프론트는 main push 로 Vercel 자동 배포 (Task 5 커밋 push 후).

- [ ] **Step 2: production smoke**

```bash
curl -s -o /dev/null -w "익명 검색: %{http_code}\n" "https://jetrag-api.woong-s.com/search?q=test"
curl -s -o /dev/null -w "익명 업로드: %{http_code}\n" -X POST "https://jetrag-api.woong-s.com/documents"
curl -s -o /dev/null -w "익명 admin: %{http_code}\n" "https://jetrag-api.woong-s.com/admin/queries/stats"
```

Expected: `익명 검색: 200` / `익명 업로드: 401` / `익명 admin: 403`.

- [ ] **Step 3: 브라우저 검증**

1. 시크릿 창 `jetrag.woong-s.com` → 데모 검색 동작 (12 docs)
2. 본인 로그인 → `/ingest` 업로드 → 인제스트 완료 → 본인 문서 검색됨
3. 로그아웃 → 방금 올린 문서가 데모 검색에 **안 나옴** (owner 컨텍스트 = owner 12 docs. 단, owner 계정으로 업로드했다면 나오는 게 정상 — 격리 검증은 별도 테스트 계정으로)

- [ ] **Step 4: README 갱신 + Commit**

`README.md` 의 "현재 운영 모드" 섹션(28-33행) 을 새 정책으로 교체: 익명 = read-only 데모 / 로그인 = 본인 업로드·격리 / 복원 절차 문단 삭제.

```bash
git add README.md
git commit -m "docs(readme): W1 데모 병행 모드 반영 — 익명 데모 + 로그인 격리"
git push origin main
```

> push 는 사용자 확인 후 실행 (production Vercel 자동 배포 트리거).

---

## 후속 (이 플랜 범위 밖 — 별도 플랜)

- W2: Gemini 유료 키 전환 + per-user rate limit
- W3–4: plans/subscriptions/usage_counters + 이메일 인제스트
- W5–6: 카카오페이 정기결제
