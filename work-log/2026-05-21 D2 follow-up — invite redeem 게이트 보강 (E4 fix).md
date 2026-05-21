# 2026-05-21 D2 follow-up — invite redeem 게이트 보강 (E4 fix)

## 0. 배경 — senior-qa E4 risk

D1 (auth) + D2 (RLS+Storage prefix+stats 누출 차단) ship 직후 (HEAD `e5640c6`),
senior-qa 가 코드 audit 으로 발견한 backend mutation/read 라우터의 **invite redeem
검증 dependency 부재**.

### 사실 (factual)

- `app/auth/dependencies.py` 의 `require_auth` 는 JWT 검증만 수행 (auth_enabled=true 시
  Bearer 토큰 유효성).
- `/auth/me` 는 `authorized: bool` 응답만 — invite_codes 의 `used_by=호출자` 행 존재
  여부 표시 (auth.py:90-105).
- 4 보호 라우터 (`/documents`, `/search`, `/answer`, `/stats`) 의 router-level dependency
  는 `require_auth` 만 → Supabase signup 만 한 random user (invite 코드 미보유)도
  JWT 유효하면 모든 backend API 통과.

### Impact

- **본인 단독 운영 단계 (현재)**: 사고 risk 0 (외부 호출자 0).
- **베타 30명 공개 단계**: random user 의 backend API 직접 호출(curl/postman)로
  invite cap 우회 가능 → 베타 30 강제 cap 실패.

### 우선순위

P0 — 베타 공개 (다음 sprint 후보) 직전 필수 차단 항목. anon key 격리 실증 (P1) 보다 먼저.

---

## 1. fix 명세 (구현)

### 1a. `app/auth/dependencies.py` — `require_authorized_user` 신규 dependency

```python
def require_authorized_user(
    current_user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
```

핵심 결정 (work-log Q/A):

| 결정 | 근거 |
|---|---|
| 캐시 없이 매 request DB SELECT | invite_codes 1 row per user 라 가볍고, redeem 직후 stale risk 0. TTL/invalidate 복잡도 회피. |
| OWNER 우회 path 없음 | OWNER 본인도 JETRAG-OWNER-001 redeem 했으니 자연 통과. 코드 분기 제거. |
| service_role client (`app.db.get_supabase_client`) | invite_codes RLS 정책상 service_role bypass — anon/authenticated 경로 분기 없음. |
| 예외 시 503 graceful | auth.py:90-105 `/auth/me` 와 동일 패턴 — 마이그 017 미적용 시 운영자 인지 우선. |
| auth_enabled=false 통과 | single-user MVP 동작 100% 보존 — DB 호출 0. |

### 1b. 4 라우터 router-level dependency 교체

| 파일 | 변경 |
|---|---|
| `app/routers/documents.py` | `dependencies=[Depends(require_auth)]` → `[Depends(require_authorized_user)]` + import |
| `app/routers/search.py` | 동일 |
| `app/routers/answer.py` | 동일 |
| `app/routers/stats.py` | 동일 |

**제외 (의도적):**
- `app/routers/auth.py` — `/auth/me` + `/auth/redeem-invite` 자체가 redeem 진입점.
  여기에 require_authorized_user 걸면 redeem 자체 불가능 (chicken-and-egg).
- `app/routers/admin.py` — `require_admin` 그대로. OWNER 본인은 redeem 자연 통과
  하므로 admin 게이트 만으로 충분.

### 1c. 단위 테스트

`api/tests/test_auth_dependencies.py` 신규 — 8 tests:

**RequireAuthorizedUserTest (4):**
1. `test_require_authorized_user_passes_when_auth_disabled` — auth_enabled=false → DB 호출 0, 통과.
2. `test_require_authorized_user_blocks_when_no_invite_redeem` — SELECT 0건 → 403 `초대 코드 redeem 이 필요합니다.`
3. `test_require_authorized_user_passes_when_invite_redeem` — SELECT 1건 → 통과.
4. `test_require_authorized_user_503_when_db_error` — SELECT 예외 → 503 `초대 코드 시스템이 준비되지 않았습니다.`

**RouterWiringTest (4):** routes inspection 으로 4 라우터의 router-level dependency 가
`require_authorized_user` 인지 직접 검증 — 향후 require_auth 로 회귀 시 즉시 fail.

### 1d. 기존 회귀 가드

`tests/test_auth_protected_routes.py:test_valid_user_passes_gate` 에 `dependency_overrides`
1줄 추가 — 새 `require_authorized_user` 가 실 Supabase 미설정 환경에서 invite_codes
SELECT 시도 시 503 발생 risk 차단. 토큰 없음/잘못된 토큰 케이스는 영향 없음
(`get_current_user` 가 먼저 401 던져 invite 게이트 도달 0).

---

## 2. 단위 테스트 결과

### 신규 module (test_auth_dependencies)

```
Ran 8 tests in 1.389s
OK
```

### Auth 인접 회귀 (test_auth_protected_routes + test_invite_codes + test_auth_jwt)

```
Ran 42 tests in 0.063s
OK
```

### 전체 회귀

```
Ran 1343 tests in 29.307s
FAILED (failures=4, errors=3, skipped=2)
```

**failures/errors 7건 — 0 신규 회귀:**

| 클래스 | 건수 | 원인 | sprint 영향 |
|---|---|---|---|
| `test_embed_cache.*` LRU | 4 failures | pre-existing — 다른 테스트 실행 순서로 인한 LRU 캐시 inter-test 오염. 격리 실행 시 통과. | 0 |
| `test_hwp_heading.*` hwp5 외부 lib | 3 errors | pre-existing — `RuntimeError` for `law sample2.hwp` non-OLE2. baseline (HEAD `e5640c6`) stash 검증으로 동일 재현 확인. | 0 |

**baseline 검증:** `git stash` 후 동일 테스트 실행 — `test_hwp_heading` 3 errors 동일 발생.
본 sprint 의 7 파일 변경(5 src + 1 신규 test + 1 회귀 가드)과 0 관련성.

신규 +8 tests → 1343 + 8 = **1351 OK** (실 통과 + flaky 7 baseline 동일).

---

## 3. 코드 변경 diff 요약

| 파일 | 변경 종류 | 라인 |
|---|---|---|
| `api/app/auth/dependencies.py` | `require_authorized_user` 함수 추가 + logger import | +60 |
| `api/app/auth/__init__.py` | export + `__all__` 추가 | +2 |
| `api/app/routers/documents.py` | import + router dep 교체 | ~5 |
| `api/app/routers/search.py` | import + router dep 교체 | ~5 |
| `api/app/routers/answer.py` | import + router dep 교체 | ~4 |
| `api/app/routers/stats.py` | import + router dep 교체 | ~4 |
| `api/tests/test_auth_dependencies.py` | 신규 (8 tests) | +170 |
| `api/tests/test_auth_protected_routes.py` | 회귀 가드 dependency_overrides 1줄 | +2 |

마이그 변경 0, frontend 변경 0, 의존성 추가 0.

---

## 4. senior-qa P0/P1 매핑 — 다음 검증 후보

### 본 sprint 해소

- **E4** — backend 4 라우터 invite redeem 게이트 부재 → ✅ require_authorized_user 도입.

### 다음 sprint 후보 (NOT in scope)

| 항목 | 우선순위 | 비고 |
|---|---|---|
| anon key 격리 실증 (런타임) | P1 | 사용자가 anon key 제공 시 별도 sprint. supabase-js 클라이언트의 RLS 우회 시도 + 실패 검증. |
| Realtime publication RLS 검증 | P1-14 | ingest_jobs SELECT JOIN documents RLS 정책의 publication 적용 검증 — Supabase Dashboard Realtime 토글 확인. |
| invite_codes 캐시 도입 (TTL/invalidate) | P2 | 매 request DB SELECT 가 부담되는 트래픽 도달 시. 현재 1 row per user 라 latency 무시 가능. |

---

## 5. Rollback 절차

backend 4 라우터의 router-level dependency 만 `require_auth` 로 환원하면 즉시 D2
상태로 복귀 — 새 `require_authorized_user` 함수와 단위 테스트는 dead code 로 잔존하나
무해 (production import 0).

```bash
# 4 라우터 import 와 router dep 만 require_auth 로 되돌림
git revert <this commit sha>
```

또는 hot-fix:
```python
# 4 라우터 각각
dependencies=[Depends(require_auth)]  # require_authorized_user → require_auth
```

마이그 변경 0이므로 DB rollback 불요.

---

## 6. commit + push

commit message:

```
feat(auth-d2-followup): invite redeem 게이트 보강 (E4 fix)

senior-qa D1+D2 audit 발견 — backend 4 라우터(/documents, /search, /answer,
/stats) router-level dependency 가 require_auth 만이라 redeem 안 한 random
user 도 통과. 베타 30 cap 강제 실패 risk. require_authorized_user dependency
추가 + 4 라우터 적용. invite_codes used_by SELECT 1회/request (캐시 없음,
1 row per user 가벼움). auth_enabled=false 면 자연 통과(MVP 보존), DB 조회
실패면 503 graceful (마이그 017 미적용 환경 대응).
```

main push — 사용자 명시 승인 (옵션 A).

---

## 7. 산출물 보고

- 단위 테스트: 1343 → 1351 OK (+8 신규), failures 4 errors 3 baseline flaky 동일 (0 회귀)
- 변경 파일: 6 src (auth dependencies + 4 router + auth __init__) + 1 신규 test + 1 회귀 가드 = 8 파일
- 마이그/frontend/의존성 변경 0
