# 2026-07-06 세션 종합 — 수익화 W1 쓰기 복원 (데모 병행) 구현 완료 + Railway ENV 전환 대기

> 세션 시작 HEAD `83b4bfb` → 마감 HEAD **`9faf5a3`** (**push 완료** — Vercel 프론트 자동 배포 트리거됨).
> 단위 테스트 baseline **1332** (failures=4, errors=3, skipped=12 — 기존 flaky embed_cache/hwp, 회귀 0).
> production 백엔드는 **아직 구 코드** — Railway ENV 전환 + 재배포가 남은 유일한 블로커.

---

## 0. 핵심 요약

수익화 sprint 의 **W1 = "read-only 포트폴리오 데모 → 데모 병행 멀티유저 쓰기 모드"** 전환 구현을 완료했다.

**목표 모드**:
- **익명 방문자**: OWNER 의 12개 문서로 read-only 데모 유지 (검색/답변/통계 조회 가능)
- **로그인 사용자**: 본인 `user_id` 격리 공간에서 업로드/삭제/재인제스트 등 쓰기 가능
- **쓰기 7 endpoint**: 익명이면 401 `"로그인이 필요합니다."`
- **admin endpoint**: 익명 fallback 이 OWNER user_id 를 갖더라도 `is_authenticated=False` 라 403 차단

**실행 방식**: superpowers:subagent-driven-development — Task 별 fresh 서브에이전트 + 2단계 리뷰(spec → quality). 최종 홀리스틱 리뷰 verdict: **READY for production flip**.

**Task 1 (API 키 회전)** 은 사용자 지시로 **스킵** ("키 회전은 무시하고 넘어가").

---

## 1. 완료된 작업 (Task 2~5 + 정리 + push)

### 1.1 커밋 목록 (9건, push 완료)

| commit | 내용 |
|---|---|
| `1eef1d5` | docs(spec): 수익화 sprint 디자인 — 카카오페이 B2C 구독 + 이메일 인제스트 |
| `b50a73e` | docs(plan): W1 구현 플랜 — 키 회전 + 쓰기 복원 (익명 데모 병행) |
| `10925f0` | feat(auth-w1): get_current_user 3-way 분기 — 익명 데모 + 로그인 격리 병행 |
| `80abde3` | feat(auth-w1): require_authenticated_user 게이트 + require_admin 익명 fallback 차단 |
| `be850db` | feat(auth-w1): 쓰기 7 endpoint 로그인 게이트 전환 + Portfolio demo_readonly 제거 |
| `4f7b8fd` | fix(auth-w1): 익명 GET 테스트 외부 호출 제거 + 리뷰 지적 정리 |
| `5772bce` | feat(web-w1): 로그인 병행 복원 — /ingest 보호 + 익명 데모 유지 |
| `240d8a0` | fix(web-w1): proxy 리다이렉트 시 리프레시 세션 쿠키 유실 방지 + 캐시 금지 헤더 |
| `cacea48` | docs(readme): W1 데모 병행 모드 반영 — 익명 데모 + 로그인 격리 |
| `9faf5a3` | chore(auth-w1): 최종 리뷰 정리 — 낡은 PORTFOLIO MODE 주석·dead import 제거 + share 401 wrap |

### 1.2 백엔드 (Task 2~4)

**`api/app/auth/dependencies.py`** — 핵심 파일:
- `CurrentUser` frozen dataclass 에 `is_authenticated: bool = True` 추가
- `get_current_user` 3-way 분기:
  - auth_enabled=false → default_user + `is_authenticated=True`
  - 토큰 없음 → **익명 fallback**: `owner_user_id` + `is_authenticated=False` (데모 read)
  - 유효 JWT → 호출자 본인 + `True` / 무효 JWT → 401
- `require_authenticated_user` 신규 — 익명이면 401 `"로그인이 필요합니다."` + `WWW-Authenticate: Bearer`
- `require_admin` 보강 — `not is_authenticated or not owner_user_id or user_id != owner_user_id` → 403 `"운영자 권한이 필요합니다."` (**보안 핵심**: 익명 fallback 이 OWNER uid 를 가지므로 is_authenticated 체크 필수)
- `forbid_demo_writes` 삭제, `api/app/config.py` 의 `demo_readonly` / `JETRAG_DEMO_READONLY` 파싱 제거

**쓰기 게이트 7곳** — `Depends(require_authenticated_user)`:
- `documents.py` 4곳 (업로드/삭제/재인제스트 등, line 399/584/785/874)
- `answer.py` 3곳 (line 595/752/902)

**테스트**:
- `test_auth_demo_fallback.py` 신규 9건 (3-way 분기 5 + 401 게이트 2 + admin 익명 차단 2)
- `test_auth_protected_routes.py` 재작성 — 익명 GET 은 게이트 통과 증명(q 누락 422 트릭), DB 라우트는 `get_supabase_client` MagicMock patch, 쓰기는 401 detail 검증. **외부 I/O 0**

### 1.3 프론트 (Task 5 — 서브에이전트 usage limit 중단 → 직접 인수 완료)

- `web/src/proxy.ts` — 세션 refresh 복원 + `/ingest` 만 보호 경로 (`PROTECTED_PATHS`), 나머지는 익명 통과. **`redirectWithSession()` 헬퍼**로 redirect 시 리프레시 쿠키 유실 방지 + `Cache-Control: private, no-store`
- `web/src/app/layout.tsx` — AuthProvider/getCurrentUser 복원
- `header.tsx` / `header-mobile-panel.tsx` / `hero-section.tsx` — 업로드/로그인/로그아웃 UI 복원
- `web/src/app/ingest/page.tsx` — read-only redirect 제거, IngestUI 복원
- `web/src/app/share/route.ts` — **버그 수정**: 존재하지 않는 `sb-access-token` 쿠키 읽기 → 기존 `getServerForwardToken()` 헬퍼 재사용 (청크 분할 `sb-<ref>-auth-token` 대응). 503 분기 → 401 한국어 wrap
- returnTo open-redirect 방어는 기존 3곳에 이미 존재 확인 (`startsWith('/') && !startsWith('//')`)
- 검증: `pnpm tsc --noEmit && pnpm lint && pnpm build` 전부 clean

### 1.4 리뷰 루프에서 잡은 이슈

1. **Task 4 테스트가 live HF HTTP 호출** — get_settings 만 override 해 router.huggingface.co 실호출. 422 트릭 + MagicMock patch 로 수정 (`4f7b8fd`)
2. **proxy redirect 쿠키 유실** — `NextResponse.redirect()` 는 setAll 이 쓴 쿠키를 안 가짐. `redirectWithSession()` 으로 수정 (`240d8a0`)
3. **share/route.ts 잘못된 쿠키명** — 파일 내 낡은 NOTE 를 따라 구현됨. 직접 diff 리뷰에서 발견·수정

---

## 2. 남은 작업 (W1-T6 — 유일한 블로커: Railway ENV 전환)

### 2.1 Railway ENV 전환 (사용자 액션 필요)

로컬에 Railway CLI/토큰 없음 → 둘 중 하나:
- **옵션 A**: Railway API 토큰 제공 → GraphQL `variableUpsert` 로 적용 (자동 redeploy)
- **옵션 B**: 대시보드 (Railway → jetrag-api → Variables):
  1. `JETRAG_DEMO_READONLY` **삭제**
  2. `JETRAG_AUTH_ENABLED` = `true`
  3. `SUPABASE_JWT_ALGORITHM` = `ES256`
  4. `SUPABASE_JWKS_URL` = `https://mpmtydudhojpukuuadrd.supabase.co/auth/v1/.well-known/jwks.json`
  5. `OWNER_USER_ID` 유지 확인
  6. **좌상단 보라색 "Apply N changes" Deploy 버튼 클릭 필수**

> production Supabase 는 ECC P-256 signing key → ES256/JWKS 필수. `jwt_verify.py` 는 이미 지원 (코드 변경 0).

### 2.2 재배포 후 smoke (기준선 → 기대값)

| 체크 | 전환 전 (2026-07-06 측정) | 전환 후 기대 |
|---|---|---|
| `GET /search?q=test` 익명 | 200 | **200** (데모 유지) |
| `POST /documents` 익명 | 503 | **401** |
| `GET /admin/queries/stats` 익명 | 200 | **403** |

```bash
curl -s -o /dev/null -w "%{http_code}" "https://jetrag-api.woong-s.com/search?q=test"
curl -s -o /dev/null -w "%{http_code}" -X POST "https://jetrag-api.woong-s.com/documents"
curl -s -o /dev/null -w "%{http_code}" "https://jetrag-api.woong-s.com/admin/queries/stats"
```

### 2.3 브라우저 검증

1. 익명으로 jetrag.woong-s.com 접속 → 데모 검색 정상 (OWNER 12 docs)
2. 로그인 → `/ingest` 진입 → 업로드 성공 (본인 격리 공간)
3. 익명으로 `/ingest` 접근 → `/login?returnTo=/ingest` 리다이렉트
4. 로그인 사용자와 익명 데모 데이터가 섞이지 않는지 확인

### 2.4 스킵/이연 항목

- **Task 1 (API 키 회전)**: 사용자 지시로 스킵. Gemini/Supabase 키가 과거 노출된 이력이 있다면 별도 회전 필요 (수익화 W2 의 Gemini 유료 키 전환 때 자연 해소 가능)
- **낮은 리스크 pre-flip 체크** (최종 리뷰어 IMPORTANT): 데모 12 docs 의 `user_id == OWNER_USER_ID` — 현 production 이 이미 owner filter 로 12건을 보여주고 있어 low risk. smoke 의 "익명 검색 200 + 결과 존재" 로 커버

---

## 3. 이후 로드맵 (수익화 sprint, spec `2026-07-05-monetization-sprint-design.md`)

| 주차 | 내용 | 상태 |
|---|---|---|
| **W1** | 키 회전(스킵) + 멀티유저 쓰기 복원 | 구현 완료, **ENV 전환 대기** |
| W2 | Gemini 유료 키 + rate limit | 별도 플랜 작성 예정 |
| W3-4 | 미터링 + 이메일 인제스트 (베타 피드백 1순위: 업로드 마찰 제거) | - |
| W5-6 | 카카오페이 B2C 구독 | - |

---

## 4. 다른 컴퓨터/새 세션 진입 가이드

1. `git pull` — HEAD `9faf5a3` 까지 push 됨
2. Railway ENV 전환 여부 확인: §2.2 smoke 3종 실행 — upload 가 503 이면 아직 전환 전
3. 전환 완료면 §2.3 브라우저 검증 → W1 마감 → W2 플랜 (superpowers:writing-plans)
4. 테스트: `cd api && uv run python -m unittest discover tests` (baseline 1332, flaky 7) / `cd web && pnpm tsc --noEmit && pnpm lint`
