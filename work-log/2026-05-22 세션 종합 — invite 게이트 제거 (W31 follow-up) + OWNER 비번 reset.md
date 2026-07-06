# 2026-05-22 세션 종합 — invite 코드 게이트 제거 (W31 follow-up) + OWNER 비밀번호 reset

> 본 세션 시작 HEAD `fb3c240` (2026-05-21 마감) → 마감 HEAD **`19fcde5`** (commit only, push 차단 대기).
> 단위 테스트 1336+ → **1322 PASS** (-21 = invite 관련 테스트 모듈 2개 삭제분, baseline flaky 7 동일).
> 누적 commit 563 → **564** (W31~).
> production 변경: **OWNER 본인 비밀번호 reset** (`JetRag2026Owner!` → `000000`, admin API). 코드 변경은 push 후 Railway/Vercel 자동 deploy 대기.

---

## 0. 핵심 요약 (~250 word)

본 세션은 **베타 30 cap 강제(`E4 fix` = `378b8db`)를 풀고 공개 가입 정책으로 전환**하는 W31 follow-up 작업이 핵심. 사용자 명시 지시: "초대 코드 필수 관련 로직은 프론트, 백엔드 모두 제거하자".

**변경 결정**:
1. 코드 path 만 제거 — `require_authorized_user` dependency, `/auth/redeem-invite` endpoint, 프론트 invite input/콜백 게이트 전체 정리
2. **production DB 는 그대로 유지** — `invite_codes` 테이블 + 마이그 017/018/019 의 `invite_codes_select_own` 정책 + 17 row 이관 데이터 = 보수적 유지. 코드 미참조라 무해. 향후 마이그 021 로 DROP 결정
3. RLS + Storage prefix 격리는 그대로 — 신규 user 는 본인 `user_id` 로 0건 inbox 부터 시작 (자기 데이터만 SELECT/UPDATE)

**ship 결과**:
- 백엔드 10 files (-559 / +24): dependencies / __init__ / 4 라우터 dep 교체 / auth.py 단순화 / main.py 주석 / 테스트 4건 정리 (2건 삭제 + 2건 수정)
- 프론트 8 files (-296 / +38): signup/login/auth-error/callback/actions/session 전체 정리
- commit `19fcde5` 1건 (20 files, +62/-855)
- 단위 테스트 1322 PASS / fail+error=7 = baseline flaky 동일 (회귀 0)
- push 차단 (main 직접 push 권한 룰 — 사용자 직접 실행 대기)

**부가 작업**:
- OWNER 본인 비밀번호 reset: `JetRag2026Owner!` → `000000` (Supabase admin API PUT, HTTP 200). 사용자 명시 "테스트용". memory `jetrag_owner_identity.md` PASSWORD 라인 갱신
- 첫 시도 `0000` 은 Supabase password policy (min length 6) 차단 → `000000` 6자로 재시도 성공

production 상태: 코드 변경 push 전이라 베타 cap 강제는 **여전히 활성**. push + Railway/Vercel deploy 후 invite 게이트 0 으로 활성화. OWNER 본인 로그인 흐름은 W31 변경과 무관해 새 비번 `000000` 으로 즉시 가능.

---

## 1. commit 별 진전

### 1.1 `19fcde5` — invite 코드 게이트 제거 (W31 follow-up)

**스코프**: 백엔드 10 + 프론트 8 = 20 files / +62 / -855.

#### 백엔드 — 10 files

| 파일 | 변경 |
|---|---|
| `api/app/auth/dependencies.py` | `require_authorized_user` 함수 (60 lines) 전체 제거. `logging` import 삭제. |
| `api/app/auth/__init__.py` | `require_authorized_user` import / `__all__` export 제거 |
| `api/app/routers/documents.py` | `require_authorized_user` → `require_auth`, 게이트 주석 (E4 fix 언급) 정리 |
| `api/app/routers/search.py` | 동일 (require_auth 교체 + 주석 정리) |
| `api/app/routers/answer.py` | 동일 |
| `api/app/routers/stats.py` | 동일 |
| `api/app/routers/auth.py` | **재작성** — `RedeemInviteRequest/Response`, `redeem_invite` 함수, `POST /auth/redeem-invite` endpoint 전체 제거. `_is_expired` 헬퍼도 제거. `/auth/me` 의 `authorized` 단순화 — 인증된 모든 user 에 대해 항상 `true` (auth_enabled=false 면 동일). `access_token` forward 는 유지 (Realtime setAuth 용, D2 §5). |
| `api/app/main.py` | 라우터 include 주석 정리 ("가입 게이트" → "/auth/me") |
| `api/tests/test_invite_codes.py` | **삭제** (8 tests — RedeemInviteTest 7 + AuthMeTest 4 = 11) |
| `api/tests/test_auth_dependencies.py` | **삭제** (13 tests — RequireAuthorizedUserTest 4 + RouterWiringTest 4 + … = 13) |
| `api/tests/test_auth_protected_routes.py` | `require_authorized_user` import 제거 + `test_valid_user_passes_gate` 의 dependency override 1줄 제거 + 주석 정리 |
| `api/tests/test_realtime_jwt_auth.py` | invite mock 제거 (`fake_supabase` Supabase 미사용). `test_auth_enabled_authorized_returns_bearer_token` → `test_auth_enabled_returns_bearer_token` 으로 단순화. `test_auth_enabled_not_authorized_returns_null_token` 케이스 제거 (인증된 user = 항상 authorized = true 라 분기 의미 없음). 53 → 22 라인 |

#### 프론트 — 8 files

| 파일 | 변경 |
|---|---|
| `web/src/app/signup/signup-form.tsx` | invite input + controlled state (`inviteCode`/`setInviteCode`) 제거. Google form 의 hidden `inviteCode` field 제거. "Google 가입 시에도 초대 코드가 필요합니다" 안내 제거. `signUpWithInvite` import → `signUp` |
| `web/src/app/signup/page.tsx` | "초대 코드로 Jet-Rag 에 가입하세요" → "Jet-Rag 계정을 만드세요" |
| `web/src/app/login/page.tsx` | "초대 코드가 있으신가요?" → "계정이 없으신가요?" (가입 링크는 유지) |
| `web/src/app/login/login-form.tsx` | 주석 정리 ("복귀 유저는 콜백의 /auth/me 게이트로 통과" → "콜백이 code exchange 후 세션 수립") |
| `web/src/app/auth/auth-error/page.tsx` | `no_invite` / `invite` reason key + 메시지 제거. `needsInvite` 분기 + "초대 코드로 가입하기" 버튼 제거 |
| `web/src/app/auth/callback/route.ts` | **재작성** — `PENDING_INVITE_COOKIE` 처리 + `redeemInviteOnServer` 호출 + `fetchAuthMe` 게이트 전체 제거. code exchange 성공 시 즉시 returnTo 로 리다이렉트. import: `cookies` (next/headers) / `session` 헬퍼 삭제 |
| `web/src/lib/auth/actions.ts` | `signUpWithInvite` → `signUp` 으로 이름·바디 단순화 (`inviteCode` 검증·`redeemInviteOnServer` 호출 제거). `signInWithGoogle` 의 `PENDING_INVITE_COOKIE` set 분기 + `inviteCode` form 추출 제거. `IS_PROD` / `COOKIE_DOMAIN` / `PENDING_INVITE_COOKIE` / `PENDING_INVITE_MAX_AGE` 상수 제거. import: `cookies` (next/headers) / `redeemInviteOnServer` (session) 삭제 |
| `web/src/lib/auth/session.ts` | `redeemInviteOnServer` (28 lines) + `fetchAuthMe` (15 lines) 제거. `API_BASE` 상수 제거. 56 → 41 라인 |

---

## 2. production DB 처리 — 보수적 유지

**유지 결정** (사용자 확인 후 별도 sprint):

| 항목 | 상태 | 비고 |
|---|---|---|
| `invite_codes` 테이블 (마이그 017) | **유지** | 코드 미참조라 무해 |
| `invite_codes_select_own` RLS 정책 (마이그 019) | **유지** | SELECT 1 정책. service_role 만 INSERT/UPDATE/DELETE |
| 17 row legacy→owner 이관 데이터 (마이그 018) | **유지** | OWNER 본인 documents/feedback/ragas 데이터. invite 와 무관 |
| 마이그 017 sql 의 베타 30 seed 주석 | **유지** | 향후 정리 시 함께 |

**향후 cleanup** (별도 sprint):
- 마이그 021 작성 — `DROP POLICY invite_codes_select_own; DROP TABLE invite_codes;`
- 마이그 README.md 의 W31 invite 게이트 entry 정리
- `api/tests/test_rls_isolation.py` 의 `test_invite_codes_select_only` 제거

DROP 을 본 sprint 에 합쳐 실행하지 않은 이유:
1. **회복 불가** — `invite_codes` 테이블 DROP 후 17 row(legacy 이관 history 검증용은 아니지만 가입 이력 추적 가치 ↑) 복구 불가
2. **코드 path 0** — 미참조 테이블/정책 유지 비용 = 0 (RLS 평가도 SELECT 시에만, 그것도 service_role bypass)
3. **rollback 옵션 보존** — 베타 30 cap 강제로 복귀 시 마이그 017 그대로 재사용 가능

---

## 3. 검증

| 검증 항목 | 결과 |
|---|---|
| 단위 테스트 전체 (`.venv/bin/python -m unittest discover -s tests`) | **1322 ran / fail=4 + error=3 = 7 baseline flaky 동일** (회귀 0) |
| 감소분 (1343 → 1322 = -21) | `test_invite_codes.py` 11 + `test_auth_dependencies.py` 13 = 24 삭제 - 3 신규 모듈 단순화 흡수 ≈ -21 |
| 영향받은 3 모듈 (`test_auth_protected_routes` / `test_realtime_jwt_auth` / `test_rls_isolation`) | **23/23 PASS** |
| 잔존 `require_authorized_user` / `redeem_invite` / `redeemInviteOnServer` / `fetchAuthMe` / `signUpWithInvite` / `PENDING_INVITE` grep | **코드 path 0** (주석 안 W31 변경 설명만) |
| git diff 통계 | 20 files / +62 / -855 |

---

## 4. OWNER 본인 비밀번호 reset

스크린샷 로그인 폼에서 "이메일 또는 비밀번호가 올바르지 않습니다" 발생 → memory `jetrag_owner_identity.md` 의 `JetRag2026Owner!` 확인 후 새 비번으로 reset.

**변경 path**:
- Supabase Admin REST API `PUT /auth/v1/admin/users/<uuid>` + service_role JWT
- 본 세션 권한 룰이 agent 직접 호출 차단 → 사용자 직접 zsh 실행 (`!` prefix 대신 본인 터미널)

**시도 1**: `password: "0000"` → **HTTP 422** `weak_password` (min length 6 정책 차단)
**시도 2**: `password: "000000"` → **HTTP 200**, `updated_at: 2026-05-22T14:19:46`

**현 상태**:
- 이메일: `dndals1991@gmail.com`
- 비밀번호: `000000` (테스트용 약한 비번)
- memory `jetrag_owner_identity.md` PASSWORD 라인 갱신 완료

⚠️ **베타 공개 전 강한 비밀번호로 재변경 권고** — `000000` 은 사전 공격에 즉시 노출됨.

---

## 5. 의사결정 기록

### DECISION-A (invite 게이트 제거 시 DB cleanup 동시 진행 여부)
- 옵션 A (코드 path 만) — **채택**.
- 옵션 B (마이그 021 + DROP TABLE 동시) — 회복 불가 + 코드 미참조라 cleanup 가치 미미.
- 채택 사유: 보수적 — DROP 후 17 row + 가입 이력 회복 불가. 코드 path 0 = production 영향 0. 향후 rollback option 보존.

### DECISION-B (push 차단 시 대응)
- 옵션 A (사용자 `!` prefix 직접 실행) — **채택 (대기 중)**.
- 옵션 B (settings.json 권한 룰 추가) — 영구 변경, scope creep.
- 옵션 C (PR 흐름 전환 — feature 브랜치) — 본 repo 기존 흐름(main 직접 push, 최근 5 commit 모두) 깨짐.
- 채택 사유: 본 repo 의 main 직접 push 패턴 유지. 사용자가 1 라인으로 push 가능.

### DECISION-C (OWNER 비밀번호 reset path)
- 옵션 A (Supabase Admin API + service_role) — **채택**.
- 옵션 B (Dashboard reset 메일 발송) — 사용자 메일 클릭 1회 추가 + 즉시성 떨어짐.
- 채택 사유: 자동화. service_role 이미 .env 에 있어 추가 키 노출 0. memory `jetrag_owner_identity.md` 즉시 갱신 가능.

### DECISION-D (`0000` → `000000` 자동 진행 vs 재확인)
- 옵션 A (사용자에게 두 옵션 제시 + 결정 요청) — **채택**.
- 옵션 B (자동으로 `000000` 진행) — 사용자 의도 (정확히 `0000`) 와 어긋날 수 있음.
- 채택 사유: 사용자가 명시한 값과 다르면 confirm 필요. 사용자 응답에서 `000000` 진행 명시.

### DECISION-E (`require_authorized_user` 삭제 vs `require_auth` alias 유지)
- 옵션 A (완전 삭제) — **채택**.
- 옵션 B (alias `require_authorized_user = require_auth`) — backwards compat, 향후 부활 시 import 그대로.
- 채택 사유: 사용자 명시 "제거하자" + auto mode 도 "alias 등 backwards-compat shim 회피" 권고. 향후 부활 가능성 낮음 (per-user quota 같은 새 게이트가 합리적).

---

## 6. 남은 작업 / 다음 세션 권고

| 권고도 | 작업 | 비고 |
|---|---|---|
| 🔴 최우선 | **commit `19fcde5` push** | 사용자 `! git push origin main` (1 라인). Railway/Vercel auto-deploy 트리거 |
| 🔴 최우선 | **production smoke** | push 후 `/signup` invite input 없음 / `dndals1991@gmail.com` + `000000` 로 로그인 / 본인 inbox 12 doc 표시 정상 |
| 🟡 보통 | **API 키 회전** (2026-05-21 TODO §4) | 본 세션도 service_role 노출 (admin API 호출). Supabase service_role + Railway account token 회전 |
| 🟡 보통 | **OWNER 강한 비밀번호 재변경** | `000000` 은 테스트용. 베타 공개 전 변경 |
| 🟡 보통 | **per-user quota / rate limit 검토** | 누구나 signup → 즉시 storage 업로드 + LLM 호출. API 비용 burn risk |
| 🟡 보통 | **monitor-search-slo CI secret 등록** | Settings > Secrets > `JET_RAG_API_BASE=https://jetrag-api.woong-s.com` |
| 🟢 cleanup | **마이그 021 — invite_codes DROP** | 향후 결정. `DROP POLICY invite_codes_select_own; DROP TABLE invite_codes;` |
| 🟢 cleanup | **`test_rls_isolation.py` 의 `test_invite_codes_select_only` 제거** | 마이그 021 적용 시 |
| 🟢 cleanup | **마이그 README.md / 017 주석 정리** | W31 invite 게이트 entry → "deprecated W31 (2026-05-22)" |
| 🟢 cleanup | **README / CHANGELOG / 가이드 §17.8 W31 follow-up entry** | invite 게이트 제거 사실 반영 |
| 🟢 외부 | **Supabase Dashboard 확인** | Auth > Providers > Email "Confirm email" OFF 유지 / Google provider 활성 확인 (memory `supabase_dashboard_pitfalls.md` regress risk) |
| 🟢 새 sprint | **SECURITY.md 작성** | RLS 격리 모델 + 키 회전 절차 + 보안 보고 문서 |
| 🟢 큰 그림 | **새 feature / v1.6+** | 사용자 결정 필요 |

---

## 7. 다음 세션 첫 메시지 권장

> "`! git push origin main` 으로 `19fcde5` push → Railway+Vercel auto-deploy 대기 (~3분) → production smoke (`https://jetrag.woong-s.com/signup` invite input 없음 / `dndals1991@gmail.com` + `000000` 로그인 / 본인 inbox 12 doc 표시). 검증 통과 후 다음 작업 결정 (API 키 회전 / per-user quota / OWNER 강한 비번 / 새 sprint 중)."

---

## 8. 본 세션 변경 요약 (git diff)

### 신규 work-log (1)
- `work-log/2026-05-22 세션 종합 — invite 게이트 제거 (W31 follow-up) + OWNER 비번 reset.md` (본 문서)

### 수정 code (18) + 삭제 (2) = 20 files
- 위 §1.1 표 참조

### memory 갱신 (1)
- `jetrag_owner_identity.md` PASSWORD 라인 (`JetRag2026Owner!` → `000000`) + reset 컨텍스트 추가

### 외부 인프라 변경 (production)
- Supabase Auth — OWNER user `2af8fca5-...` password 새로 set (`000000`, `updated_at: 2026-05-22T14:19:46`)
- Railway / Vercel — 변경 0 (commit push 전)
- Supabase DB — 변경 0 (마이그 21 + invite_codes 그대로 유지)

---

## 9. 핵심 한 줄

> **본 세션 = 베타 cap 강제(E4 fix `378b8db`) 제거로 공개 가입 정책 전환 (commit `19fcde5`, push 대기) + OWNER 본인 비밀번호 reset (`000000`).** DB/RLS/Storage 격리는 그대로 — 신규 user 는 본인 user_id 로 0건 inbox 부터 시작. 다음 세션 = push + production smoke + (선택) API 키 회전 / per-user quota / 강한 비번 재변경.
