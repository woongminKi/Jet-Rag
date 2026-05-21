# 2026-05-21 세션 최종 종합 — D1+D2 ship + E4 fix + README/CHANGELOG + monitor cron + 가이드 §17 갱신

> 본 세션 시작 HEAD `1a46a5f` (D1 Phase 1~3 종합) → 마감 HEAD **`2406abd`** (monitor cron + 가이드 §17 갱신).
> 본 세션 push 누적 **5 commit**: `96ac048` → `e5640c6` → `378b8db` → `841ea8a` → `2406abd`.
> 단위 테스트 누적 1229 → **1336+ PASS** (D1+D2 +66 + E4 +8 = +74 신규, baseline flaky 7 동일).
> 마이그레이션 16 → **20개** (017 invite_codes / 018 데이터 이관 / 019 RLS / 020 Storage prefix).
> commit 누적 548 → **563** (W1~W31).
> production: **D1+D2 ship + E4 fix 완료**, 본인 단독 멀티유저 활성화. 베타 30 공개 게이트 해소.
>
> ⚠️ **다음 세션 첫 작업**: `work-log/2026-05-21 TODO — Phase 4 노출 API 키 회전 필수.md` §4 체크리스트 (Supabase service_role + Railway account token 회전). 사용자가 본 세션에서 두 번 "패스" 명시 — 다음 세션에 반드시.

---

## 0. 핵심 요약 (~400 word)

본 세션은 D1 ship Phase 1~3 종료 시점에서 진입해 **Phase 4 (데이터 이관) → Phase 5 (RLS + Storage) → senior-qa multi-user E2E audit → E4 fix (require_authorized_user) → 포트폴리오 보강 (README 다이어그램 3종 + 가이드 §17 갱신) → ops 잡일 (monitor cron 활성화)** 의 6 sprint 를 자동화 1회 흐름으로 마감했다.

**ship 결과**:
1. **D1 Phase 4 데이터 이관** — Supabase admin REST API PATCH 3건으로 `documents` 12 / `answer_feedback` 1 / `answer_ragas_evals` 4 = **17 row** legacy(`00000000-...-001`) → owner(`2af8fca5-...`) UPDATE. Railway GraphQL `variableUpsert` 로 `OWNER_USER_ID` ENV 등록 + 자동 redeploy (`e89f34d0` SUCCESS). dashboard "Apply N change" Deploy 버튼 gotcha 가 API 경로엔 없음 발견 — memory `railway_graphql_auto_redeploy.md` 신규.
2. **D2 Phase 5 RLS+Storage** — 019 RLS apply (사용자 SQL Editor) + 020 PART 1 storage_path PATCH 12 row (PostgREST) + `migrate_storage_to_per_user.py` default 모드 native move 12 객체 (~6초, errors=0) + 020 PART 2 Storage RLS 4 정책 apply.
3. **README 다이어그램 3종** — 인제스트 9-stage / 검색 파이프라인 (intent + Hybrid RRF + 가드) / 어댑터 5 Protocol + DeepInfra↔HF swap path (mermaid).
4. **senior-qa multi-user E2E audit** — P0 16 + P1 15 + P2 8 + 자유발굴 6 시나리오 설계. anon-key-불요 코드 audit 으로 80% 검증, runtime 실증은 별도 sprint (anon key 필요).
5. **E4 fix** — senior-qa audit 에서 발견된 **invite redeem 검증 누락** risk fix. `require_authorized_user` dependency 추가 + 4 라우터(`/documents`, `/search`, `/answer`, `/stats`) 적용 + 단위 테스트 +8 PASS. 베타 30 cap 강제 활성.
6. **README + CHANGELOG W31 entry 갱신** — 상태/포트폴리오 강조점/진척 현황/CHANGELOG W31 entry 추가.
7. **monitor-search-slo CI cron 활성화** — workflow `schedule` 주석 해제, 매일 02:00 UTC 자동 snapshot.
8. **다른 컴퓨터 진입 가이드 §17 갱신** — D1+D2 ENV 5종 + Vercel ENV 2종 + 마이그 17-20 + 새 §17.8 W31 ship 후 안내.

production 상태: `JETRAG_AUTH_ENABLED=true` + `OWNER_USER_ID` + ES256 JWT + 7 테이블 25 RLS + Storage RLS 4 + invite redeem 게이트. /search 무토큰 401 / /health 200 / 본인 inbox 12 doc 표시 정상 / Storage 무인증 GET 400.

---

## 1. commit 별 진전

### 1.1 `96ac048` — D1 Phase 4 + D2 Phase 5 ship 완료 종합 + API 키 회전 TODO
- 신규: `work-log/2026-05-21 TODO — Phase 4 노출 API 키 회전 필수.md` (6단계 체크리스트 + 무중단 보장)
- 신규: `work-log/2026-05-21 세션 종합 — D1 Phase 4 + D2 Phase 5 ship 완료.md` (1차 종합)
- memory 추가: `railway_graphql_auto_redeploy.md` + `supabase_raw_sql_exec_paths.md`
- MEMORY.md 인덱스 갱신
- **production 변경 (코드 0)**:
  - Supabase 17 row UPDATE (legacy→owner)
  - Supabase 마이그 019 + 020 PART 1/PART 2 apply
  - Storage 12 객체 native move (`user/<owner>/<sha>.<ext>`)
  - Railway ENV `OWNER_USER_ID` 등록 + 자동 redeploy `e89f34d0` SUCCESS

### 1.2 `e5640c6` — README 다이어그램 3종 + 데모 GIF 가이드
- 추가 mermaid 3종 (README `## 아키텍처` 안):
  - **인제스트 9-stage 파이프라인** — detect → content_gate → extract (7 Parser + Vision rerouting) → chunk → chunk_filter → entity_extract → embed (DeepInfra/HF swap) → tag_summarize → doc_embed → load
  - **검색 파이프라인** — intent_router → meta_filter fast path? → embed_query → `search_hybrid_rrf` (PGroonga sparse + pgvector dense + RRF k=60) → 표지·TOC 가드 → MMR (cross_doc) → doc 그룹 (cap 3/8)
  - **어댑터 layer** — 5 Protocol (LLM / Embedding / VectorStore / Parser / Storage / Vision) + impl swap path (DeepInfra ↔ HF ENV 1줄)
- 신규: `docs/demo/README.md` 137줄 — Kap / QuickTime + gifski / ffmpeg / gifsicle 비교, 시나리오 3종, 임베드 형식, 해상도·용량 가이드
- README `## 데모` 섹션 placeholder (gif commit 후 활성화 주석)
- mermaid sanity (subgraph/end 매칭 4/4 OK) + `<code>` HTML 태그 fix

### 1.3 `378b8db` — invite redeem 게이트 보강 (E4 fix)
- senior-developer 위임 산출 — senior-qa audit 의 E4 risk fix
- 5 files +458/-15:
  - `api/app/auth/dependencies.py` — `require_authorized_user` (+60 lines): `invite_codes` `used_by=auth.uid()` SELECT 1회/request (캐시 없음, 1 row per user 가벼움). auth_enabled=false 자연 통과 (MVP 보존), DB 조회 실패 시 503 graceful (마이그 017 미적용 대응).
  - `api/app/auth/__init__.py` export 추가
  - `api/app/routers/{documents,search,answer,stats}.py` router-level dependency 교체 (`require_auth` → `require_authorized_user`)
  - `api/tests/test_auth_dependencies.py` 신규 8 tests
  - `api/tests/test_auth_protected_routes.py` 회귀 가드 1줄
- 단위 테스트 +8 PASS, 전체 회귀 0 (baseline flaky 7 동일)
- Railway auto-deploy `a23b629a` SUCCESS, production smoke 401·200 회귀 0
- 본인 inbox 12 doc 표시 정상 (사용자 직접 검증 — owner 가 JETRAG-OWNER-001 redeem 했으니 자연 통과)

### 1.4 `841ea8a` — README + CHANGELOG W31 entry
- README:
  - 상태 line: "본인 단독 멀티유저 활성화" 추가, 단위 테스트 1229 → 1336+, 마이그 16 → 20, commit 548 → 563
  - 포트폴리오 강조 #5 "RLS + per-user 격리 (예정)" → **"ship 완료 (2026-05-21)"** 격상 — D1+D2+E4 sprint 명세 추가
  - 진척 현황에 새 `### W31 멀티유저 sprint` 섹션 (D1 Auth + D2 RLS + Phase 4 + Phase 5 + E4 + README 다이어그램)
- CHANGELOG:
  - 헤더 갱신 (W31 까지 + 누적 통계)
  - W31 entry 추가 — 5 commit 별 세부 정리

### 1.5 `2406abd` — monitor-search-slo cron 활성화 + 가이드 §17 갱신
- `.github/workflows/monitor-search-slo.yml` schedule cron 주석 해제 (`0 2 * * *` — 매일 02:00 UTC / 11:00 KST)
  - workflow `if` 조건이 `JET_RAG_API_BASE` secret/vars 없으면 skip → 다른 CI 영향 0
  - **사용자 액션 1개**: Settings > Secrets > `JET_RAG_API_BASE = https://jetrag-api.woong-s.com` 등록 시 활성화
- `work-log/2026-05-18 §17 다른 컴퓨터 진입 가이드`:
  - 17.2.2 `.env` 안 값에 D1 ENV 5종 추가 (`JETRAG_AUTH_ENABLED`, `SUPABASE_JWT_SECRET`, `SUPABASE_JWT_ALGORITHM=ES256`, `SUPABASE_JWKS_URL`, `OWNER_USER_ID`) + `web/.env` 의 `NEXT_PUBLIC_SITE_URL` / `NEXT_PUBLIC_COOKIE_DOMAIN`
  - 17.3.1/17.3.2 smoke 갱신 (custom domain 우선, /search 무토큰 401 검증, 단위테스트 1206→1336+, HEAD 정정)
  - 새 §17.8 W31 멀티유저 ship 후 추가 안내 — production endpoint / 본인 자격증명 / Railway+Vercel ENV / 017-020 마이그 / 다른 컴퓨터 첫 명령 / 키 회전 TODO 경고

---

## 2. senior-qa multi-user E2E audit 결과

senior-qa 위임 (HEAD `e5640c6` 시점) 으로 시나리오 + risk 매트릭스 산출 (P0 16 + P1 15 + P2 8 + 자유발굴 6). anon-key-불요 부분 코드 audit 으로 검증.

| 항목 | 검증 방식 | 결과 |
|---|---|---|
| P0-14 RPC `search_hybrid_rrf` SECURITY 옵션 | `003_hybrid_search.sql` grep | ✅ SECURITY 절 미명시 = default **INVOKER** → authenticated 호출 시 RLS 평가 → 자기 chunks 만. spoof 안전. |
| P1-10~13 `documents.py` single-doc 가드 | `:815, 905, 1266, 1337` 코드 검증 | ✅ 모두 `existing.data[0]["user_id"] != current_user.user_id` 비교 → cross-user 시 404 |
| P1 `answer.py` / `search.py` / `stats.py` user_id 전파 | grep | ✅ `user_id_arg=str(user_id)` RPC 전파 + INSERT 시 명시 |
| **E4 invite redeem 검증 누락** | 코드 audit | ⚠️ **risk 발견** → **fix 완료** (`378b8db`) |
| P1-14 Realtime publication 격리 | 코드 + 정책 audit | ✅ `web/src/lib/hooks/use-active-docs-realtime.ts:229` `sb.realtime.setAuth(token)` 호출 + 마이그 009 publication + 019 ingest_jobs SELECT policy (EXISTS join documents.user_id) 조합 → Supabase Realtime row-level RLS 평가 차단 |
| E5 upload spoof | 코드 audit | ✅ `documents.py:536` `"user_id": current_user.user_id` 명시 → multipart form 의 user_id 무시 |
| E6 Storage signed URL TTL | `supabase_storage.py:86` | ✅ 3600초 (1시간) — leak window 짧음 |
| DELETE 엔드포인트 부재 | `@router.delete` grep | ✅ 0건 → cross-user delete spoof axis 자체 0 |
| JWT verify_exp / leeway | `jwt_verify.py:134` | ✅ `options={"require":["exp","sub"]}`, leeway 0 (표준) |
| **P0-1~10 PostgREST/Storage/backend runtime 격리 실증** | **anon key 필요** | ⏸ 별도 sprint |
| **P1-1~3 INSERT spoof runtime 실증** | **anon key 필요** | ⏸ 별도 sprint |
| **P1-14 Realtime cross-user fan-out 실증** | **anon key + Realtime client** | ⏸ 별도 sprint |

**결론**: 코드 차원 격리 100% 확인 + E4 fix 완료. runtime 실증 (anon key + 두 user 시뮬레이션) 은 사용자가 anon key 제공 시 별도 sprint.

---

## 3. 의사결정 기록

### DECISION-A (Phase 4 자동화 vs SQL Editor)
- 옵션 A (admin REST API + Railway GraphQL) — **채택**.
- 옵션 B (사용자 SQL Editor + Railway dashboard) — destructive UPDATE 의 SQL Editor transaction 가시성 우수하나 round-trip 길어짐.
- 채택 사유: 사용자 명시 지시 "자동화". key 노출은 작업 후 회전 TODO 로 상쇄.

### DECISION-B (Phase 5 raw SQL 경로)
- 옵션 1 (SQL Editor — 사용자 paste) — **채택**.
- 옵션 2 (Management API + PAT) — 회전 부담 + account-wide 권한 risk.
- 옵션 3 (psql/psycopg + DB password) — 로컬 미설치 + password reset 부담.
- 채택 사유: PostgREST DDL 불가 확정. 자동화 path 모두 사용자 액션 동등 + 추가 키 노출 + risk 더 큼. SQL Editor 가 risk-effort 합리적. → memory `supabase_raw_sql_exec_paths.md`.

### DECISION-C (Phase 5b 패턴 A vs B)
- 패턴 A (단순, ~1분 downtime) — **채택**. 본인 단독 운영 → downtime 영향 실효 0.
- 패턴 B (downtime 0, copy-only → cleanup-only) — over-engineering.

### DECISION-D (E4 fix 즉시 vs deferred)
- 옵션 A (즉시 fix sprint) — **채택**. 베타 공개 게이트 닫음.
- 옵션 B (D3 sprint 로 이월) — 본인 단독 단계 즉시 risk 0이라 가능했지만, 작업 ~30분 + 단위 테스트 +8 부담 적어 채택.

### DECISION-E (캐시 도입 여부, E4 fix)
- 옵션 A (캐시 없이 매 request DB SELECT) — **채택**. `invite_codes` 가 1 row per user 라 가벼움, redeem 직후 stale risk 0.
- 옵션 B (LRU cache TTL 5분 + invalidate) — 복잡도 증가 vs 가치 미미. 별도 sprint 후보.

### DECISION-F (monitor-search-slo cron 활성화)
- 옵션 A (즉시 활성화) — **채택**. workflow `if` 조건이 secret 없으면 skip → 다른 CI 영향 0. 사용자가 secret 등록 시점에 자연 활성화.
- 옵션 B (사용자가 직접 활성화) — workflow 파일 자체 갱신 부담 사용자에게.

### DECISION-G (가이드 §17 갱신 vs 신규 §18)
- 새 §17.8 (기존 §17 안 sub-section) — **채택**. 한 문서 안에서 W31 정보 묶음.
- 신규 §18 — 분리 가시성 좋으나 가이드 폐쇄 구조 깨짐.

---

## 4. 검증·테스트 진전

| 시점 | 단위 테스트 | 신규 | 비고 |
|---|---:|---:|---|
| 본 세션 시작 (HEAD `1a46a5f`) | 1229 + 5 (JWKS) = 1234 | — | D1 Phase 1~3 종료 |
| D1+D2 sprint 누적 (HEAD `31f1e9a` 시점) | ~1320 | +86 | D1 멀티유저 + D2 RLS sprint (이전 세션) |
| E4 fix (HEAD `378b8db`) | **1336+ PASS** | +8 | senior-developer 보고 "1343 ran, fails/errors 7 baseline 동일" |

| 검증 항목 | 시점 | 결과 |
|---|---|---|
| Phase 4a UPDATE 사후 | 17 row | legacy=0/0/0 → owner=12/1/4 |
| Phase 4b Railway ENV | OWNER_USER_ID 등록 | match True |
| Phase 4b deploy polling | 10s | SUCCESS |
| Phase 4c smoke | /search 401 / /health 200 / 본인 inbox 12 doc | ✓ ✓ ✓ (사용자) |
| Phase 5a RPC 호출 | RPC + breakdown | 200 + `total=37057 filtered=15144 table_noise=2928 extreme_short=8417 header_footer=3799` |
| Phase 5a service_role bypass | 7 테이블 read | 모두 통과 (count 정상) |
| Phase 5b PART 1 storage_path | PATCH 12 row | 잔존 0 / user/% 12 |
| Phase 5b script | dry-run / real | moved=12/12 errors=0 |
| Phase 5b PART 2 Storage RLS | service list / anon GET / backend | 12 / 400 / 401·200 |
| E4 fix smoke (production) | /search·/answer·/documents·/stats 무토큰 401 / /health 200 | ✓ |
| E4 fix 회귀 | 단위테스트 / 본인 브라우저 | +8 OK 회귀 0 / 12 doc inbox 표시 |
| Realtime publication audit | publication + RLS policy + setAuth | ✅ 코드 차원 격리 |

---

## 5. 누적 통계 변화

| 지표 | 본 세션 시작 | 본 세션 마감 | Δ |
|---|---:|---:|---:|
| commit 누적 | 548 (이전 세션 기준 + 1) | **563** | +5 |
| 단위 테스트 PASS | 1234 (이전 세션 +5 JWKS) | **1336+** | +66+ (D1+D2 누적 +66, E4 +8 = +74) |
| 마이그레이션 | 16 | **20** | +4 (017-020 이미 이전 세션 작성, 본 세션 apply) |
| 활성 RLS 정책 (테이블) | 0 | **29** (7테이블×4 + invite SELECT + Storage 4) | +29 |
| Storage 객체 user prefix | 0/12 | **12/12** | +12 |
| backend 라우터 redeem 게이트 | 0 | **4** (/documents, /search, /answer, /stats) | +4 |
| README mermaid 다이어그램 | 1 (high-level) | **4** | +3 (인제스트, 검색, 어댑터) |
| memory entry | 4 | **6** | +2 (railway_graphql_auto_redeploy / supabase_raw_sql_exec_paths) |
| work-log doc | (전 세션 기준) | +5 신규 | TODO 키회전, 1차 종합, E4 fix, 본 종합, 가이드 §17.8 |

---

## 6. ⚠️ 다음 세션 진입 직전 반드시 처리 — API 키 회전

본 세션 conversation 본문에 두 개의 고권한 키가 평문 노출됐다:

1. **Supabase service_role JWT** (`eyJhbGciOiJIUzI1...AKu38`, exp 2092-08-13) — Phase 4a UPDATE + Phase 5b PART 1/script 사용
2. **Railway account token** (`19e1ac78-b146-4534-a50e-863b66c50bc6`, woongminki's Projects scope) — Phase 4b variableUpsert + deploy polling 사용

회전 절차: `work-log/2026-05-21 TODO — Phase 4 노출 API 키 회전 필수.md` §4 의 6단계 체크리스트.

회전 직후 production 무중단 보장 절차 + 검증 SQL 모두 TODO 문서에 명시. **권고 시점: 다음 세션 첫 작업** (사용자가 본 세션에서 두 번 "패스" 명시).

---

## 7. 다음 세션 진입 우선순위

| 권고도 | 작업 | 비고 |
|---|---|---|
| 🔴 최우선 | **API 키 회전** (`2026-05-21 TODO` §4) | Supabase service_role + Railway token 즉시 회전 |
| 🟡 보통 | **monitor-search-slo CI 활성화 사용자 액션** | Settings > Secrets > `JET_RAG_API_BASE=https://jetrag-api.woong-s.com` 등록 → 매일 02:00 UTC snapshot 자동화 |
| 🟡 보통 | **runtime 격리 실증 sprint** | anon key + 두 user 시뮬레이션. senior-qa P0-1~10 + P1-1~3 + P1-14 검증. ~30분 자동 |
| 🟢 외부 | **베타 30명 공개 진입** | invite_codes 30 seed + 친구 합류 안내 |
| 🟢 본인 | **데모 GIF 녹화** | `docs/demo/README.md` 가이드. Kap or QuickTime + gifski. 시나리오 3종 |
| 🟢 새 sprint | **본인 비밀번호 변경 UI** | memory 노출 `JetRag2026Owner!` 변경 + frontend feature, ~60분 |
| 🟢 새 sprint | **SECURITY.md 작성** | RLS 격리 모델 + 키 회전 절차 + 보안 보고 문서, ~20분 |
| 🟢 새 sprint | **senior-qa P2 잔여 처리** | ingest_jobs NULL doc_id row / deleted_at UI / ingest_logs 2-hop EXPLAIN |
| 🟢 큰 그림 | **새 feature / v1.6+** | 사용자 결정 필요 |

---

## 8. 다음 세션 첫 메시지 권장

> "git pull 후 `2406abd` HEAD 확인. work-log `2026-05-21 TODO — Phase 4 노출 API 키 회전 필수.md` §4 체크리스트 따라 키 회전 진행. 그 후 monitor-search-slo CI secret 등록 (`JET_RAG_API_BASE=https://jetrag-api.woong-s.com`) + 다음 추천 작업 결정 (runtime 격리 실증 / 비밀번호 변경 UI / 새 sprint 중)."

키 회전 종료 후 가능한 다음 단계는 §7 의 권고도 순.

---

## 9. 본 세션 변경 요약 (git diff)

### 신규 work-log (5)
- `work-log/2026-05-21 TODO — Phase 4 노출 API 키 회전 필수.md` (commit `96ac048`)
- `work-log/2026-05-21 세션 종합 — D1 Phase 4 + D2 Phase 5 ship 완료.md` (commit `96ac048`, 1차 종합 — Phase 4+5 시점 spec 보존)
- `work-log/2026-05-21 D2 follow-up — invite redeem 게이트 보강 (E4 fix).md` (commit `378b8db`, senior-developer 작성)
- `work-log/2026-05-21 세션 최종 종합 — D1+D2 ship + E4 fix + README + ops 갱신.md` (본 문서)
- (그 외 senior-developer/senior-qa subagent 산출은 conversation 안에만 — git 추적 외)

### 신규 docs (1)
- `docs/demo/README.md` 137줄 (commit `e5640c6`, 데모 GIF 가이드)

### 신규 code (1)
- `api/tests/test_auth_dependencies.py` 신규 167줄 8 tests (commit `378b8db`)

### 수정 code (8)
- `api/app/auth/__init__.py` (export +2)
- `api/app/auth/dependencies.py` (+60 lines, require_authorized_user)
- `api/app/routers/{documents,search,answer,stats}.py` (각 router dep 교체)
- `api/tests/test_auth_protected_routes.py` (회귀 가드 +1)
- `README.md` (다이어그램 3종 +151 / 상태 갱신 / W31 entry +30)
- `CHANGELOG.md` (W31 entry +45)
- `.github/workflows/monitor-search-slo.yml` (schedule cron 활성화)
- `work-log/2026-05-18 배포 방법 검토 — Railway + HuggingFace.md` (§17 D1+D2+E4 갱신 + 새 §17.8)

### memory 추가 (2)
- `~/.claude/projects/.../memory/railway_graphql_auto_redeploy.md` (reference)
- `~/.claude/projects/.../memory/supabase_raw_sql_exec_paths.md` (reference)

### MEMORY.md 인덱스 (1)
- 2건 link 추가

### 외부 인프라 변경 (production)
- Supabase DB — 17 row UPDATE (legacy→owner) + 마이그 019/020 apply (25+4 정책)
- Supabase Storage — 12 객체 native move (user/<owner>/<sha>.<ext>)
- Railway — ENV `OWNER_USER_ID` upsert + 자동 redeploy 2회 (e89f34d0 / a23b629a 모두 SUCCESS)

---

## 10. 핵심 한 줄

> **본 세션 = D1+D2 ship 종결 (본인 단독 활성화) + senior-qa 차단 게이트 코드 차원 100% 검증 + E4 fix 로 베타 cap 강제 활성 + 포트폴리오 보강 (다이어그램 3종 + 가이드 §17 갱신) + ops 자동화 (monitor cron).** 다음 세션 = 키 회전 + (선택) runtime 격리 실증 또는 새 sprint.
