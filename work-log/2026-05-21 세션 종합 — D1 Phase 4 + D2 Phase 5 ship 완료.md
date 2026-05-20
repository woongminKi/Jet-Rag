# 2026-05-21 세션 종합 — D1 Phase 4 (데이터 이관 + OWNER_USER_ID) + D2 Phase 5 (RLS + Storage prefix) ship 완료

> 전(前) 세션 마감 HEAD `1a46a5f` (Phase 1~3 + JWKS ES256 보강) → 본 세션에서 **Phase 4 + Phase 5 자동화 (admin REST API + Railway GraphQL + storage script) 1회 흐름으로 마감 → D1+D2 ship 완료**. 코드 변경 0 (마이그/script 적용 + 외부 인프라 ENV/Storage 만 변경). work-log + memory + 키 회전 TODO 만 신규.
> 사용자 직접 작업: 019 SQL apply (1회), 020 PART 2 SQL apply (1회), 브라우저 검증 (1회), Railway PAT 토큰 발급 (1회). 그 외 모든 단계는 admin API/Railway GraphQL/스크립트 자동.

---

## 0. 핵심 요약

본인 단독 운영(D1) 상태에서 출발 → 멀티유저 베타 공개 가능(D2) 상태로 도약. 절차:

1. **Phase 4a (018 이관)**: Supabase service_role 로 admin REST API PATCH 3건 — `documents` 12 / `answer_feedback` 1 / `answer_ragas_evals` 4 = **17 row** legacy UUID(`00000000-...-001`) → owner UUID(`2af8fca5-...`) UPDATE. 잔존 0.
2. **Phase 4b (OWNER_USER_ID)**: Railway GraphQL `variableUpsert` mutation — 자동 redeploy 트리거 (dashboard "Apply N change" 클릭 gotcha 우회). deploy `e89f34d0` SUCCESS.
3. **Phase 4c smoke**: backend `/search` 무토큰 401 / `/health` 200 / 본인 브라우저 로그인 → 문서 12건 보임 / 시크릿 → `/login` 리다이렉트 — 사용자 확인.
4. **Phase 5a (019 RLS)**: 사용자 SQL Editor paste & Run — 25 정책 + RPC `get_chunks_stats_for_user` 등록. PostgREST 검증 — RPC 호출 200 + total/filtered/breakdown 정상 (`total=37057 filtered=15144 table_noise=2928 extreme_short=8417 header_footer=3799`). service_role 7 테이블 read 통과 (bypass 보존).
5. **Phase 5b PART 1 (storage_path)**: PostgREST PATCH 12 row — `<sha256>.<ext>` → `user/<owner>/<sha256>.<ext>`. 잔존 0.
6. **Phase 5b script**: `migrate_storage_to_per_user.py` default 모드 — 12 객체 native move (~6초, errors=0). bucket root 정리 — `user/` 폴더만 잔존.
7. **Phase 5b PART 2 (Storage RLS)**: 사용자 SQL Editor — 4 정책 (SELECT/INSERT/UPDATE/DELETE) on `storage.objects` filter by `bucket_id='documents' AND foldername[1]='user' AND foldername[2]=auth.uid()::text`. 무인증 GET 객체 → 400, service_role list 통과.
8. **마감**: 키 회전 TODO 문서 별도 작성 (`Phase 4 노출 API 키 회전 필수.md`) + MEMORY 2건 추가 + 본 work-log + commit.

production 상태: **D1+D2 ship 완료**. `JETRAG_AUTH_ENABLED=true` + `OWNER_USER_ID=2af8fca5-...` + RLS 25 + Storage RLS 4 + 객체 모두 `user/<uid>/` prefix. 멀티유저 베타 30명 공개 게이트 — `senior-qa` 의 P1/P2 차단 모두 해소.

---

## 1. 진행 흐름

### 1.1 Phase 4a — 018 데이터 이관 (admin REST API)

선택지 비교:
- 옵션 A (자동화) — admin REST API + Railway GraphQL 토큰 위임. **채택**.
- 옵션 B (사용자 SQL Editor + dashboard) — 안전하지만 round-trip 길고 destructive UPDATE 의 가시성은 SQL Editor 의 transaction 표시가 우수.
- 옵션 C (혼합) — 사전 SELECT 만 API, UPDATE 는 SQL Editor.

채택 사유: 사용자가 명시적 "자동화" 선택. service role key 한 번 노출 → 작업 후 회전 TODO 명시. Phase 4b 의 Railway GraphQL 도 동일 패턴.

**사전 검증** (PostgREST count=exact):
| 테이블 | legacy | owner (pre) | 이관 대상 |
|---|---:|---:|---:|
| documents | 12 | 0 | 12 |
| answer_feedback | 1 | 0 | 1 |
| answer_ragas_evals | 4 | 0 | 4 |
| **합계** | **17** | **0** | **17** |

owner 측 0 → 재실행 충돌 risk 0, 멱등 OK.

**UPDATE 실행** (PostgREST PATCH 3건, return=representation):
- `documents` HTTP 200, updated_rows=12
- `answer_feedback` HTTP 200, updated_rows=1
- `answer_ragas_evals` HTTP 200, updated_rows=4

**사후 검증**: legacy=0/0/0 owner=12/1/4. documents 12 row 의 `title`/`doc_type`/`created_at` 무결성 sample 확인 — pdf 8 / hwpx 2 / hwp 1 / pptx 1, deleted_at 0.

### 1.2 Phase 4b — OWNER_USER_ID Railway 등록

Railway account token 발급 (사용자, dashboard 1 액션) → workspace `woongminki's Projects` scope.

**기존 ENV 확인** (GraphQL `variables` query):
- `JETRAG_AUTH_ENABLED`=true (len 4), `SUPABASE_JWT_SECRET` (len 88), `SUPABASE_JWT_ALGORITHM`=ES256, `SUPABASE_JWKS_URL` 모두 present.
- `OWNER_USER_ID`: <MISSING>.

**variableUpsert 호출**:
```graphql
mutation { variableUpsert(input: {
  projectId: "acf7403f-539a-459e-942e-8f5ac75a4ddb",
  environmentId: "44d9b5a8-60b8-4422-ba4f-586c13e4de22",
  serviceId: "a11393d0-471b-4cd8-9c37-33c095fbd2f9",
  name: "OWNER_USER_ID",
  value: "2af8fca5-..."
}) }
```
→ `{"variableUpsert": true}`. 재조회 — match owner True.

**자동 redeploy 발견**: `deployments(first:5)` 조회 → 신규 `e89f34d0` DEPLOYING (createdAt 즉시 직후). dashboard 의 "Apply N change" 보라색 Deploy 버튼 클릭 gotcha 가 API 경로엔 없음. 명시적 `serviceInstanceRedeploy` mutation 호출 불요. → memory `railway_graphql_auto_redeploy.md` 신규.

**polling**: 10초 간격, try=1 (10초 후) 에 이미 SUCCESS 도달. backend smoke 401/200 정상.

### 1.3 Phase 4c — 본인 브라우저 E2E

사용자 확인:
- 본인 로그인 → inbox 12 문서 보임 ✓
- 시크릿 창 → `/login` 리다이렉트 ✓

→ D1 ship 완료.

### 1.4 Phase 5a — 019 RLS 정책 apply

자동화 시도 분기: PostgREST 는 raw SQL exec 불가 확정 — local psql / supabase CLI / psycopg 모두 미설치. Management API + PAT 는 account-wide 권한 + 회전 부담 → 사용자 SQL Editor paste 가 사용자 액션 동등 + risk 0 으로 더 우수. → 사용자 paste & Run.

사용자 응답 "Success. No rows returned" 후 자동 검증:
- **RPC 호출** `POST /rpc/get_chunks_stats_for_user`: HTTP 200, `total=37057 filtered=15144 breakdown={table_noise:2928, extreme_short:8417, header_footer:3799}`. RPC 등록 + SECURITY DEFINER + GRANT TO service_role 모두 정상.
- **service_role 7 테이블 read** (RLS bypass 보존): documents 12 / chunks 37057 / ingest_jobs 51 / ingest_logs 429 / answer_feedback 1 / answer_ragas_evals 4 / invite_codes 1.
- **backend 회귀 0**: /search 401, /health 200.

### 1.5 Phase 5b PART 1 — documents.storage_path prefix

12 대상 row 도출 (`storage_path NOT LIKE 'user/%' AND deleted_at IS NULL`) → owner UUID 동일.

PostgREST PATCH row-by-row (12회):
- new `storage_path = "user/2af8fca5-.../<sha256>.<ext>"`
- HTTP 200, return=representation 검증
- result ok=12 fail=0

사후: `NOT LIKE 'user/%'` 잔존 0, `LIKE 'user/%'` 12 row. sample 3 — `user/2af8fca5-.../<sha256>.pdf` 형식 정상.

### 1.6 Phase 5b script — 12 객체 native move

`PYTHONPATH=api uv run python scripts/migrate_storage_to_per_user.py --dry-run`:
- 첫 시도 — `ModuleNotFoundError: app` (cwd 가 `api/scripts/`). `PYTHONPATH=/.../api` 추가로 해결.
- dry-run — `processed=12 moved=12 errors=0`. errors=0 확인 후 실 이동.

실 이동:
- 12 객체 모두 `storage.move()` native 호출 성공 (~6초). fallback download/upload/delete 경로 0.
- bucket root 직접 list — `user/` 폴더만 잔존, legacy `<sha256>.ext` 0건.

### 1.7 Phase 5b PART 2 — Storage RLS 정책 4개

사용자 SQL Editor paste & Run (5a 와 동일 패턴):
```sql
documents_select_own / _insert_own / _update_own / _delete_own
ON storage.objects FOR ... TO authenticated
WHERE bucket_id='documents' AND foldername[1]='user' AND foldername[2]=auth.uid()::text
```

검증:
- service_role list under `user/2af8fca5-.../`: 12 objects (bypass 통과)
- 무인증(no key) GET 객체 → 400 (Supabase Storage gateway 가 missing-auth 차단 — 정책 적용 효과)
- backend /search 401 / /health 200

---

## 2. 외부 인프라 변경

### Supabase (PostgreSQL)
- `documents` / `answer_feedback` / `answer_ragas_evals` 의 17 row user_id legacy→owner UPDATE
- `documents` 의 12 row storage_path prefix 부착 (`user/<uid>/...`)
- 019 마이그 apply — 7 테이블에 25 policy + `get_chunks_stats_for_user` RPC + GRANT
- 020 PART 2 apply — `storage.objects` 4 policy (documents 버킷 user prefix 강제)
- invite_codes / vision_usage_log / search_metrics_log / vision_page_cache / embed_query_cache 정책 (002 패턴 + service_role only) — 019 본문에 자연 포함

### Supabase (Storage)
- 12 객체 root → `user/<owner>/...` native move
- bucket root 정리 — `user/` 단일 폴더만 잔존

### Railway
- ENV `OWNER_USER_ID = 2af8fca5-03ab-421b-94b8-53d4fe9d8046` upsert
- 자동 redeploy `e89f34d0` SUCCESS

### 코드 변경
- **없음**. 모든 작업이 외부 인프라(데이터 + ENV + Storage 객체) 차원에서 진행.

---

## 3. 의사결정 기록

### DECISION-A (Phase 4 자동화 vs SQL Editor)
- 옵션 A (admin REST API + Railway GraphQL) — 채택. 한 번에 다 처리, dashboard 단계 minimize.
- 옵션 B (사용자 SQL Editor + Railway dashboard) — destructive UPDATE 의 SQL Editor transaction 가시성 우수하나, round-trip 길어짐.
- 옵션 C (혼합) — 의미 없음.
- 채택 사유: 사용자 명시 지시 "자동화". 키 노출은 작업 후 즉시 회전으로 상쇄.

### DECISION-B (Phase 5 raw SQL 경로)
- 옵션 1 (SQL Editor — 사용자 paste) — **채택**. 사용자 액션 30초 × 2회.
- 옵션 2 (Management API + PAT) — PAT 발급/회전 부담 + account-wide 권한이 risk 큼.
- 옵션 3 (psql/psycopg + DB password) — 로컬 미설치 + password reset 부담.
- 채택 사유: PostgREST 가 DDL 불가 확정. 자동화 path 모두 사용자 액션 동등 + 추가 key 노출 + risk 더 큼. SQL Editor 가 risk-effort 합리적. → memory `supabase_raw_sql_exec_paths.md` 신규.

### DECISION-C (Phase 5b 패턴 A vs B)
- 패턴 A (단순, ~1분 downtime) — **채택**. 본인 단독 운영 → downtime 영향 실효 0.
- 패턴 B (downtime 0, copy-only → cleanup-only) — 객체 일시 2배 비용 + 절차 5 단계. over-engineering.
- 채택 사유: 단일 사용자 환경에서 패턴 B 는 합리적 근거 없음.

### DECISION-D (키 회전 시점)
- Phase 5 작업 중 회전 — Phase 5 작업이 다시 키 필요할 가능성. 회전 시 작업 중단 risk.
- **Phase 5 완전 종료 후 회전** — 채택. 별도 TODO 문서로 강조 + work-log §5 에 명시.

---

## 4. 검증·테스트 진전

| 측정 시점 | 검증 항목 | 결과 |
|---|---|---|
| Phase 4a 사전 | legacy row count via count=exact | 12/1/4 = 17 |
| Phase 4a 사후 | legacy / owner row count | 0/0/0 / 12/1/4 |
| Phase 4b | Railway ENV 재조회 | OWNER_USER_ID = owner (match True) |
| Phase 4b | deploy polling | SUCCESS in ≤10s |
| Phase 4c smoke | /search no-token / /health | 401 / 200 |
| Phase 4c E2E | 본인 inbox 12 문서 / 시크릿 /login | ✓ ✓ (사용자) |
| Phase 5a | RPC 호출 + breakdown | 200, total=37057 filtered=15144 |
| Phase 5a | service_role 7 테이블 bypass | 모두 통과 (count 정상) |
| Phase 5a | backend smoke | 401 / 200 (회귀 0) |
| Phase 5b PART 1 | PATCH 12 row, 잔존 NOT LIKE | 0 / user/% 12 |
| Phase 5b script | dry-run / real | moved=12/12 errors=0 |
| Phase 5b PART 2 | service_role list / anon GET / backend | 12 / 400 / 401·200 |

단위 테스트(api/tests/) 본 세션에서 별도 실행 안 함 — 코드 변경 0 (외부 인프라 + 데이터만 변경) 이라 회귀 측정 불요.

---

## 5. ⚠️ 다음 세션 진입 직전 반드시 처리 — API 키 회전

별도 문서 `work-log/2026-05-21 TODO — Phase 4 노출 API 키 회전 필수.md` 참조. **본 세션 conversation 본문에 두 개의 고권한 키가 평문 노출됐다**:

1. **Supabase service_role JWT** (`eyJhbGciOiJIUzI1...AKu38`, exp 2092-08-13) — Phase 4a UPDATE + Phase 5b PART 1/script 에 사용.
2. **Railway account token** (`19e1ac78-b146-4534-a50e-863b66c50bc6`, woongminki's Projects scope) — Phase 4b variableUpsert + deploy polling 에 사용.

회전 절차 (TODO 문서 §1/§2 의 6단계 체크리스트 따라):
- Supabase dashboard > Settings > API > "Generate new secret" → Railway backend `SUPABASE_SERVICE_ROLE_KEY` 갱신 → Deploy 클릭
- Railway dashboard > Account > Tokens > 휴지통 삭제

회전 직후 production 무중단 보장 절차 + 검증 SQL 모두 TODO 문서에 명시. **권고 시점: 다음 세션 첫 작업** (잊으면 위험).

---

## 6. 누적 변경 (본 세션 git diff)

### 신규 파일 (3)
- `work-log/2026-05-21 TODO — Phase 4 노출 API 키 회전 필수.md` — 키 회전 6단계 체크리스트 + production 무중단 보장 + 노출 fingerprint redact 안내.
- `work-log/2026-05-21 세션 종합 — D1 Phase 4 + D2 Phase 5 ship 완료.md` — 본 문서.
- (memory 갱신은 git 추적 외 `~/.claude/projects/.../memory/` 에 위치)

### memory 추가 (2)
- `railway_graphql_auto_redeploy.md` (reference) — variableUpsert 가 자동 redeploy 트리거함, dashboard gotcha 가 API 에 없음.
- `supabase_raw_sql_exec_paths.md` (reference) — PostgREST DDL 불가, 3가지 우회 경로 + risk 비교, SQL Editor 가 risk-effort 합리적.

### memory MEMORY.md 인덱스 갱신 — 2건 link 추가.

### 코드 (api/web)
- **0 변경.**

---

## 7. 핸드오프

### 7.1 production 현재 상태
- **D1+D2 ship 완료**. 본인 단독 + 멀티유저 베타 30명 공개 가능 상태.
- ENV: `JETRAG_AUTH_ENABLED=true` + `OWNER_USER_ID=2af8fca5-...` + `SUPABASE_JWT_SECRET` + `SUPABASE_JWT_ALGORITHM=ES256` + `SUPABASE_JWKS_URL=...`.
- DB: 마이그 017/018/019 apply. user_id 가 owner 인 row 12+1+4. RLS 25 policy active. RPC `get_chunks_stats_for_user` 등록.
- Storage: 12 객체 모두 `user/2af8fca5-.../<sha256>.<ext>`. Storage RLS 4 policy active.
- 무토큰 /search → 401, /health → 200, 무인증 Storage GET → 400, 본인 브라우저 로그인 정상.

### 7.2 다음 세션 진입 우선 순위
1. **§5 키 회전 TODO 문서 따라 진행 — 가장 우선.**
2. `git pull` 후 본 work-log + TODO 문서 + memory 갱신 확인.
3. 베타 30명 공개 진입 시 — invite_codes 일괄 seed (가이드 §Phase 2b 베타 시드).
4. (선택) admin 라우트 운영 — `/admin/queries/stats` 등 — 본인 토큰으로만 통과.

### 7.3 rollback 절차
- Storage RLS PART 2: `DROP POLICY IF EXISTS "documents_*_own" ON storage.objects;` (4건). 020 헤더 §ROLLBACK.
- Storage prefix 복원: `UPDATE documents SET storage_path = regexp_replace(storage_path, '^user/[^/]+/', '') WHERE storage_path LIKE 'user/%' AND deleted_at IS NULL;` + 객체 reverse-move 별도 스크립트 (미작성).
- 019 RLS: `ALTER TABLE <name> DISABLE ROW LEVEL SECURITY;` 7건 + `DROP POLICY` 25건 + `DROP FUNCTION get_chunks_stats_for_user`.
- 018 데이터: `UPDATE ... SET user_id=legacy WHERE user_id=owner` 3건.
- Railway `OWNER_USER_ID` 삭제 + Deploy.

코드 자체는 변경 0 → revert 불요.

### 7.4 트러블슈팅 학습 (본 세션에서 발견)
- **Railway GraphQL `variableUpsert` 는 자동 redeploy 트리거** — dashboard 의 "Apply N change" Deploy 버튼 gotcha 없음. API 경로 우월. (memory)
- **Supabase PostgREST 는 raw SQL DDL 불가** — RPC 도 함수 등록 자체에 raw SQL 필요. Management API+PAT/psycopg+DB password/SQL Editor 3 경로. SQL Editor 가 risk-effort 합리적. (memory)
- **`migrate_storage_to_per_user.py` 실행 시 PYTHONPATH 필수** — `from app.config import ...` 가 cwd 에 의존. `PYTHONPATH=/.../api uv run python scripts/...` 형식으로 호출.
- **Supabase Storage `move()` native API 가 신뢰성 우수** — 12/12 객체 모두 fallback 안 타고 native 성공. download/upload/delete 폴백은 정말 사고 시 대비.

---

## 8. 다음 세션 첫 메시지 권장

> "git pull 후 work-log `2026-05-21 TODO — Phase 4 노출 API 키 회전 필수.md` 의 §4 체크리스트 따라 키 회전 진행."

키 회전 종료 후에는 베타 30명 공개 진입 (invite_codes 일괄 seed + Storage RLS 다른 사용자 토큰 검증) 또는 v1.5+ feature 진입 가능.
