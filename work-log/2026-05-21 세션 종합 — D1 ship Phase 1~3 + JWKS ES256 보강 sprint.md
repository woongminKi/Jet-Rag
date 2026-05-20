# 2026-05-21 세션 종합 — D1 ship Phase 1~3 (Supabase Auth+ENV+가입) + JWKS/ES256 보강 sprint

> D1+D2 ship 가이드(`work-log/2026-05-20 D1+D2 ship 단계별 가이드 (Phase 1-5).md`) 실제 runbook 진입. Phase 1(Supabase Auth + Vercel/Railway ENV) → Phase 2(017 마이그 + invite seed) → Phase 3(JETRAG_AUTH_ENABLED=true + 본인 가입) 까지 완수. Phase 1a-4 에서 발견된 **Supabase ECC(P-256) signing key migration** 대응으로 백엔드 JWKS/ES256 분기 보강 sprint(S1) 가 중간에 끼어 진행.
> 시작 HEAD `fb1006e` → 마감 HEAD **`2822ca5`** (단일 commit `feat(auth-d1-jwks)` 5 files +212/-26).
> 단위 테스트(auth 관련) 35 → **40 PASS** (+5 비대칭 경로 신규). 기존 HS256 회귀 0.
> production 무중단 — `JETRAG_AUTH_ENABLED=true` 활성화 후 본인 단독 로그인 가능. Phase 4(018 데이터 이관 + `OWNER_USER_ID` Railway 등록 + smoke) **미진행** — 다음 세션.

---

## 0. 핵심 요약

D1 ship 가이드의 Phase 1~3 = "본인 단독 활성화" 단계. Supabase Auth dashboard / Vercel / Railway ENV 설정 → 017 마이그 + invite 시드 → JETRAG_AUTH_ENABLED=true → 본인 가입의 4 단계인데, 다음 두 사건으로 expected runbook 보다 길어졌다:

1. **JWKS/ES256 sprint S1** — Phase 1a-4 에서 Supabase JWT signing key 가 이미 **CURRENT=ECC(P-256) / PREVIOUS=Legacy HS256(verify-only, "a month ago")** 으로 비대칭 체계 migration 됨을 발견. 코드 기본 HS256 검증으로는 신규 토큰 검증 불가 → D1 ship 영구 차단. senior-developer 위임 → JWKS 분기 + ES256 검증 보강(`2822ca5`) → ENV 2개(`SUPABASE_JWT_ALGORITHM=ES256` + `SUPABASE_JWKS_URL=...`) 추가로 우회.

2. **본인 가입 4회 진단 루프** — (a) 약한 비밀번호 거부(generic catch "이미 가입된 이메일" 표시) → (b) Supabase **Email confirm OFF 토글 미적용** → signup 후 session 미생성("세션을 확인할 수 없습니다") → (c) **Railway `JETRAG_AUTH_ENABLED=true` staging 미배포** ("Apply 1 change" 보라색 deploy 버튼 클릭 누락) → backend fallback `default_user_id` 로 invite redeem 처리 → (d) 가입 직전 임시 비밀번호 망실 → admin API 로 reset. 각 단계마다 admin REST API 직접 진단 + 수정.

production 상태: **`JETRAG_AUTH_ENABLED=true` 활성. 본인(`dndals1991@gmail.com`, UUID `2af8fca5-03ab-421b-94b8-53d4fe9d8046`) 단독 로그인 가능. invite_codes.used_by = 본인 UUID 박힘. 그러나 `documents`/`answer_feedback`/`answer_ragas_evals` 의 기존 row 들은 여전히 `00000000-0000-0000-0000-000000000001` (legacy default) 소속 — Phase 4 SQL UPDATE 전까지 본인 inbox 가 비어 있음**.

---

## 1. 진행 흐름 (시간 순)

### 1.1 Phase 1a Supabase Authentication (~15분)
| Step | 작업 | 결과 |
|---|---|---|
| 1a-1 | Email provider 활성화, Confirm email **OFF** (D1-Q6) | **표면적 OK — 실제로는 토글 저장 미적용. 1.4 에서 재확인 후 OFF 확정** |
| 1a-2 | Google OAuth 발급 + 등록 | **skip** (D1 hard requirement 아님, 사용자 선택) |
| 1a-3 | Site URL `https://jetrag.woong-s.com` + Redirect URLs 4개 | OK |
| 1a-4 | JWT Settings 확인 | **CURRENT=ECC(P-256), PREVIOUS=Legacy HS256(verify-only) 발견** → 1.2 sprint S1 트리거 |

### 1.2 JWKS/ES256 sprint S1 (senior-developer 위임, ~20분)
Phase 1 일시 정지. 다음 두 옵션 비교:
- **옵션 A** — Supabase Legacy JWT secret 으로 다운그레이드. dashboard "Legacy JWT Secret" 탭 = `only verify` 안내, revert 옵션 없음. **불가 확정**.
- **옵션 B** — 백엔드 `jwt_verify` 에 ES256/JWKS 경로 보강. 채택.

senior-developer 위임 (자세한 작업 명세 + 비범위 명시):
- `api/app/auth/jwt_verify.py` — `_SYMMETRIC_ALGORITHMS` 옆 `_ASYMMETRIC_ALGORITHMS = {ES256, ES384, ES512, RS256, RS384, RS512}` 추가. `_resolve_signing_key(token, settings)` 가 알고리즘 화이트리스트 기반 분기, 비대칭은 `PyJWKClient` 로 token kid 매칭 공개키 fetch.
- `api/app/config.py` — `supabase_jwks_url: str | None = None` 필드 + ENV 로딩 추가.
- `api/pyproject.toml` — `pyjwt` → `pyjwt[crypto]` extra 명시 (ECC 검증용 cryptography).
- `api/tests/test_auth_jwt.py` — `VerifyJwtAsymmetricTest` 5 신규(ES256 정상 / JWKS URL 미설정 / `PyJWKClientError` fetch 실패 / `OSError` 네트워크 / 만료). `cryptography` 로 ephemeral keypair + `PyJWKClient.get_signing_key_from_jwt` mock → 외부 IO 0.

검증 → **40/40 PASS** (test_auth_jwt + test_realtime_jwt_auth + test_admin_gate + test_auth_protected_routes). 기존 HS256 경로 회귀 0.

commit `2822ca5` (5 files +212/-26) → main push.

### 1.3 Phase 1b/1c Vercel + Railway ENV
- **Vercel**: 4개 ENV(`NEXT_PUBLIC_SITE_URL` + `NEXT_PUBLIC_COOKIE_DOMAIN=.woong-s.com` + `NEXT_PUBLIC_SUPABASE_URL` + `NEXT_PUBLIC_SUPABASE_ANON_KEY`) 추가 + Production redeploy.
- **Railway**: `SUPABASE_JWT_SECRET` + `SUPABASE_JWT_ALGORITHM=ES256` + `SUPABASE_JWKS_URL=https://mpmtydudhojpukuuadrd.supabase.co/auth/v1/.well-known/jwks.json` 3개 추가 + 자동 redeploy.

검증: `curl -I https://jetrag.woong-s.com` → 307 → `/login` (Vercel ENV 적용 증거) / `GET /health` → 200.

### 1.4 Phase 2 — 017 마이그 + JETRAG-OWNER-001 invite seed
- Supabase SQL Editor 에서 017 본문(BEGIN…COMMIT) 실행 → `invite_codes` 테이블 + 부분 인덱스 + RLS ENABLE.
- `INSERT INTO invite_codes (code, note) VALUES ('JETRAG-OWNER-001', '본인 첫 가입용') ON CONFLICT DO NOTHING;` → 1 row, `used_by=NULL`.

### 1.5 Phase 3a — Railway `JETRAG_AUTH_ENABLED=true` 추가
사용자가 Variables 에서 신규 변수 추가 → **그러나 좌상단 "Apply 1 change" + 보라색 "Deploy ⇧+Enter" 버튼 미클릭** → staging 상태로 머무름. 1.6/1.7 진단 과정에서 발견 → deploy 클릭으로 해결.

### 1.6 Phase 3c — 본인 가입 진단 루프 (4회 시도)

| 시도 | 입력 | 결과 / 진단 |
|---|---|---|
| 1차 | invite `JETRAG-OWNER-001` + `dndals1991@gmail.com` + 약한 비밀번호(`111111` 추정, 스크린샷 dots=5) | "가입에 실패했습니다. 이미 가입된 이메일일 수 있습니다." (web/src/lib/auth/actions.ts:72 generic catch). admin API `GET /admin/users?email=...` 검증 → 0 matches → **비밀번호 정책 위반** 확정. |
| 2차 | 강한 비밀번호(`JetragOwner!2026`) | "세션을 확인할 수 없습니다. 다시 로그인해 주세요." (web/src/lib/auth/session.ts:52). admin API → 1 match, `email_confirmed_at=None`, `confirmation_sent_at`=있음 → **Email confirm 이 ON 상태** (Phase 1a-1 미적용 확정). |
| dashboard 재설정 | 사용자가 Confirm email 토글 OFF 재저장 | OK. 다만 이전 user 잔존. |
| 3차 | 가입 재시도 (Confirm OFF 상태) | 가입 + 자동 로그인 성공. `last_sign_in_at` 박힘. **그러나** `invite_codes.used_by=00000000-...-001` (= `default_user_id`) 로 박힘. backend 가 JWT 검증 못 하고 fallback 으로 redeem → **`JETRAG_AUTH_ENABLED=true` 가 staging 만이고 미배포 확정**. `GET /search?q=test` 무토큰 → HTTP 200 + 결과 데이터 = backend 가 여전히 auth_enabled=false 동작 증거. |
| Railway redeploy | 사용자가 보라색 Deploy 버튼 클릭 | redeploy active 후 검증 → `GET /search?q=test` → **401 `인증이 필요합니다.`** + `/health` 200 = auth 활성. |
| invite UPDATE | `PATCH /rest/v1/invite_codes?code=eq.JETRAG-OWNER-001` `{"used_by": "2af8fca5-..."}` | 본인 UUID 로 정정 + `used_at` 유지. |
| 4차 로그인 | `dndals1991@gmail.com` + `JetragOwner!2026` | "이메일 또는 비밀번호가 올바르지 않습니다." → 실제 가입 비밀번호와 다름(예시값 그대로 사용 안 했음). admin API `PUT /admin/users/{id}` `{"password": "JetRag2026Owner!"}` 로 강제 reset → 로그인 성공. |

### 1.7 최종 상태 검증
- `email_confirmed_at` 박힘, `last_sign_in_at` 최신, `aud=authenticated`.
- `invite_codes.used_by = 2af8fca5-03ab-421b-94b8-53d4fe9d8046`, `used_at` 유지.
- `GET /search?q=test` 무토큰 → 401 (auth 활성 증거), `/health` → 200.
- 본인 ES256 토큰으로 frontend 로그인 정상.

---

## 2. 누적 변경 (커밋 `2822ca5`)

### 코드 (5 files +212/-26)
- `api/app/auth/jwt_verify.py` — `_ASYMMETRIC_ALGORITHMS` 추가, `_resolve_signing_key(token, settings)` 알고리즘 화이트리스트 분기, `_resolve_symmetric_key` / `_resolve_asymmetric_key` 분리, `@lru_cache(maxsize=4)` `PyJWKClient` URL별 싱글톤, `PyJWKClientError` + `Exception` 모두 `JWTValidationError("JWKS 공개키 조회에 실패했습니다.")` 흡수.
- `api/app/config.py` — `supabase_jwks_url: str | None = None` 필드 + `get_settings()` 의 `SUPABASE_JWKS_URL` env 로딩(빈 문자열 → None).
- `api/pyproject.toml` — `pyjwt[crypto]>=2.10.0` (extra 명시, ECC 검증용 cryptography).
- `api/tests/test_auth_jwt.py` — `VerifyJwtAsymmetricTest` 5 신규 + 기존 `test_unsupported_algorithm_raises` 가 `RS256` → `PS256` 으로 (RS256 은 이제 지원).
- `api/uv.lock` — pyjwt[crypto] extra 반영(transitive 변동 0, cryptography 가 이미 존재).

### 외부 인프라 (코드 외)
- **Supabase Auth dashboard** — Confirm email 토글 OFF 재설정.
- **Supabase invite_codes** — 017 마이그 apply + `JETRAG-OWNER-001` seed + 본인 UUID 로 used_by PATCH.
- **Supabase Auth user** — `dndals1991@gmail.com` 생성 + 강제 confirm + 비밀번호 reset (admin API).
- **Vercel Production ENV** — `NEXT_PUBLIC_*` 4개.
- **Railway backend ENV** — `SUPABASE_JWT_SECRET` + `SUPABASE_JWT_ALGORITHM=ES256` + `SUPABASE_JWKS_URL=...` + `JETRAG_AUTH_ENABLED=true`.

---

## 3. 테스트·검증 진전

| 시점 | auth 테스트 PASS | 신규 | 비고 |
|---|---:|---:|---|
| sprint S1 시작 | 35 | — | HEAD `fb1006e`, 기존 HS256 only |
| sprint S1 종료 (commit `2822ca5`) | **40** | +5 | `VerifyJwtAsymmetricTest` 5 (ES256 정상 / JWKS URL 미설정 / PyJWKClientError / OSError / 만료) |
| production smoke | — | — | `/search` 무토큰 401 / `/health` 200 / 본인 로그인 + ES256 토큰 정상 |

전체 단위 테스트는 본 세션에서 별도 측정 안 함(전체 회귀는 senior-developer 가 본 영역 4 모듈만 한정 측정). D2 검증된 1330 PASS 에서 +5 라 추정. flaky 동일.

---

## 4. 의사결정 기록

### DECISION-A (sprint S1) — JWKS 우회 채택, Supabase 다운그레이드 비채택
- 옵션 A(Supabase Legacy HS256 으로 다운그레이드) = dashboard "only verify" 안내 = 불가 확정.
- 옵션 B(백엔드 ES256/JWKS 보강) = ~20분 sprint, HS256 default 100% 보존, ENV 2개로 토글.
- 채택 사유: ECC 가 Supabase 의 새 default. 모든 신규 프로젝트가 동일 경로 갈 것 → 옵션 B 가 장기적으로 올바름. 옵션 A 가 가능했어도 ECC 가 보안 우수(P-256 ≈ RSA 3072) 라 옵션 B 선호.

### DECISION-B (Phase 3 진단) — admin REST API 로 직접 진단 + 수정
- generic catch 메시지(`이미 가입된 이메일`, `세션을 확인할 수 없습니다`) 가 진짜 원인을 가려서 dashboard 시각 진단보다 admin REST API 가 빠름.
- Service role key 로 `GET /admin/users?email=...` + `PUT /admin/users/{id}` + `PATCH /rest/v1/invite_codes?code=eq...` 직접 호출 — 권한 시스템이 차단한 destructive 액션(`PUT password`)은 사용자 명시 승인 후 실행.
- 향후 재현 가능 — 동일 dashboard 토글 regressing 시 admin REST API 가 가장 빠른 진단.

### DECISION-C (가입 비밀번호) — `JetRag2026Owner!` 채택
- 가이드 안내문의 예시 `JetragOwner!2026` 사용자 실제 입력값과 다름 확인 → admin reset.
- 차후 회복 가능 — 로그인 페이지에 "비밀번호 찾기" 메일 flow 가 있다면 메일 reset 도 가능(Confirm OFF 상태에서 reset 메일 동작 여부 미검증).

---

## 5. 남은 단계 — Phase 4 (다음 세션)

가이드 `2026-05-20 D1+D2 ship 단계별 가이드 (Phase 1-5).md` 의 Phase 4 그대로 진행:

### 4a. 018 SQL 데이터 이관
`api/migrations/018_migrate_default_user.sql` 본문을 SQL Editor 에서 실행. 기존 `documents` / `answer_feedback` / `answer_ragas_evals` 의 `user_id = 00000000-...-001` row 들을 본인 UUID 로 UPDATE. 본 ID 는 **`2af8fca5-03ab-421b-94b8-53d4fe9d8046`** 직접 SQL 에 박아 넣기.

### 4b. Railway `OWNER_USER_ID` 등록
`OWNER_USER_ID = 2af8fca5-03ab-421b-94b8-53d4fe9d8046` 추가 + Deploy 클릭(staging 잊지 말 것).

### 4c. smoke
- 본인 로그인 → 기존 문서 1건 검색 → 정상 응답
- inbox 에 기존 row 들이 본인 소속으로 보이는지

→ 여기까지 = **D1 ship 완료. 본인 단독 활성화 상태**.

### Phase 5 (선택, 베타 30명 공개 시)
019 RLS + 020 Storage per-user prefix(패턴 A 단순/B downtime 0). 본인 단독 운영 단계에선 보류.

---

## 6. 트러블슈팅 학습 — 가이드 §트러블슈팅 보강 권장

본 세션에서 발견된 신규 케이스를 가이드 §트러블슈팅에 추가하는 게 좋다:

### TS-1' — Supabase ECC migration 프로젝트 (가이드 §트러블슈팅 1 보강)
기존 가이드는 "JWT 가 비대칭인 경우 옵션 A/B" 만 명시. 실제로는 **옵션 A 불가 확정**(Legacy JWT secret 탭 = "only verify", revert 옵션 없음). 옵션 B 가 유일 경로 — 본 sprint S1 로 해소(`2822ca5`). 후속 프로젝트는 처음부터 옵션 B 로 진행.

### TS-7 — Phase 1a-1 Confirm email 토글 regress
dashboard 에서 OFF 저장 후에도 실제 적용 안 됨 사례. 가입 시 `email_confirmed_at=None` + `confirmation_sent_at`=있음 → 토글 미적용 확정 패턴.
**진단**: admin API `GET /admin/users?email=...` 로 `email_confirmed_at` 확인이 가장 빠름.
**대응**: dashboard 에서 토글 OFF → ON → OFF 순서로 왔다갔다 후 명확히 Save.

### TS-8 — Railway "Apply 1 change" pending 미배포
Variables 에 ENV 추가만 하고 좌상단 보라색 Deploy 버튼 미클릭 → staging 만 머무름 → backend 가 이전 ENV 로 동작.
**진단**: `curl /search?q=test` 무토큰이 200 (auth 활성이면 401 기대) → ENV 미배포 확정.
**대응**: Railway 좌상단 보라색 Deploy 클릭 또는 Deployments 탭 → 최신 build 의 `⋯` → Redeploy.

### TS-9 — generic catch 가 진짜 원인 가림
- `web/src/lib/auth/actions.ts:72` "가입에 실패했습니다. 이미 가입된 이메일일 수 있습니다." → 실제로는 비밀번호 정책 위반 케이스가 흔함.
- `web/src/lib/auth/session.ts:52` "세션을 확인할 수 없습니다. 다시 로그인해 주세요." → 실제로는 Confirm email 토글 ON 으로 session 미생성 케이스.

향후 개선 후보: signUp 의 `error.message` 를 사용자에게 직접 노출하거나 (보안상 일부 마스킹 후), 또는 백엔드 로그에 raw error 를 남기고 사용자 메시지엔 "확인 후 다시 시도" 안내.

---

## 7. 핸드오프

### 7.1 다음 세션 진입
- `git pull` → HEAD `2822ca5` 확인.
- `MEMORY.md` 갱신 권장(다음 §8).
- 가이드 `work-log/2026-05-20 D1+D2 ship 단계별 가이드 (Phase 1-5).md` 의 Phase 4 부터 진행.

### 7.2 본인 UUID·자격 증명
```
USER UUID: 2af8fca5-03ab-421b-94b8-53d4fe9d8046
EMAIL:     dndals1991@gmail.com
PASSWORD:  JetRag2026Owner!  (admin reset, 추후 본인이 비밀번호 변경 권장)
```

### 7.3 production 상태
- `JETRAG_AUTH_ENABLED=true` 활성. 본인 단독 로그인 가능.
- 마이그 017 apply 완료. **018/019/020 미적용**.
- documents / answer_feedback / answer_ragas_evals 가 여전히 legacy default user 소속 → 본인 inbox 비어 있음.

### 7.4 Rollback 절차
즉시 무중단 회복:
- Railway `JETRAG_AUTH_ENABLED=false` 로 변경 + Deploy → backend 가 즉시 default_user_id fallback.
- `2822ca5` 자체는 HS256 default 보존이라 revert 불요 (ENV `SUPABASE_JWT_ALGORITHM=HS256` 으로만 되돌리면 끝).

---

## 8. MEMORY.md 갱신 권장 (다음 세션 첫 작업)

본 세션에서 비-자명한 학습 3건 — 다음 세션이 같은 함정에 안 빠지도록 memory 에 기록:

1. **Supabase 신규 프로젝트는 ECC(P-256) signing key 가 default** — Legacy HS256 secret 은 "only verify" 라 다운그레이드 불가. 백엔드는 ES256/JWKS 분기 필수. (project memory)
2. **Supabase Confirm email 토글이 regress 가능** — dashboard 저장 후에도 적용 안 되는 케이스. signup 후 `email_confirmed_at=None` 발견 시 토글 재확인. (project memory)
3. **Railway Variables 추가는 좌상단 "Apply N change" Deploy 버튼 클릭 전엔 staging 만** — 단순 ENV 저장 ≠ 배포. (project memory)
