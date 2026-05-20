# 2026-05-20 세션 종합 — D2 RLS + Storage per-user prefix + stats 누출 차단 + QA + P1 패치

> 본 세션 후반부 = D2(방어 심도 + Storage 격리 + P1#2 차단) sprint 완수.
> D1 push(`c754204→da5c640`) 직후 사용자 "권장 방법으로 진행해" 지시 → D2 plan → 구현 → QA → P1 3건+P2#3 패치 → push.
> 시작 HEAD `da5c640` (D1) → 마감 HEAD **`31f1e9a`** (D2 single commit, 18 files +2213/-118).
> 단위 테스트 1298 → **1330 OK** (+32, failures 7 환경 flaky 동일, skipped 1 RLS E2E 가드).
> **remote Supabase 마이그 019/020 미적용** — D1 ship(018 + ENV `true`) 완료 후 사용자가 9단계 deploy 진행.

---

## 0. 핵심 요약

D2 = D1 senior-qa 가 명문화한 "D2 RLS 미완 상태로 `JETRAG_AUTH_ENABLED=true` 다중유저 베타 공개 금지" 게이트를 닫는 sprint. plan v1.0 (11 결정 전부 planner 추천값 채택) → senior-developer 구현(설계 검토 1회 + 본 구현 1회) → senior-qa 조건부 PASS(P0=0, P1 3건+P2 6건) → P1 3건+P2#3 패치 → 게이트 6/6 통과.

**무중단 절대 준수** — `auth_enabled=false` default + service_role bypass + RPC graceful 로 production live(jetrag.woong-s.com) 무영향. 마이그 019/020 은 D1 ship 후 사용자가 dashboard 에서 apply.

총 5 sprint(plan / 설계검토 / 구현 / QA / P1 패치), 단위 테스트 +32, 신규 파일 백엔드 7(마이그 2 + script 1 + tests 4) + 프론트 1(/api/auth-token route handler) + work-log 2.

---

## 1. 진행 흐름 (시간 순)

### 1.1 plan v1.0 작성 (senior-planner)
- 입력: D1 senior-qa 인계(P1#2 stats 누출), D1 마스터 plan §6 D1/D2 경계, 멀티유저 D2~D7 sprint plan.
- 핵심 발견 6건(F1~F10): user_id 컬럼 매트릭스, F6/F8 Storage path 단순한 root-level/pending/default 구조, F9 마이그 다음 = 019/020.
- 11 미결 결정(Q1~Q11) — 사용자 "권장 방법으로 진행해" 지시로 **전부 planner 추천값 채택**:
  - Q1 vision/embed cache 글로벌 공유 유지(콘텐츠 sha256, PII 없음)
  - Q2 단일 버킷 + prefix / Q3 SQL 마이그 / Q4 D1 ship 게이트
  - Q5 move + 폴백 / Q6 옵션 B helper / Q7 RPC / Q8 Realtime JWT D2 포함
  - Q9 anon audit / Q10 NULL doc_id 모니터링 / Q11 인덱스 D2 보류

### 1.2 senior-developer 설계 검토 (1차)
- plan §3.4 표 정정: extract.py/incremental.py 는 storage.get 만 사용 → put 시그니처 변경 불필요.
- 옵션 B 채택 — `build_user_path`/`build_pending_path` static helper 추가, Protocol 무변경, 호출부에서 `put_at(path=...)`.
- anon supabase audit: Realtime 1곳 외 직접 쿼리 0건 확정(Q9 해소).
- 추가 4 결정 사용자 승인(`/auth/me` route handler + same-origin / PART 분리 유지 / E2E 환경 가드).

### 1.3 senior-developer 본 구현 (2차, 승인된 설계)
- 마이그 019(7 테이블 25 정책 + RPC), 020(PART 1·2 분리), Storage 어댑터, 라우터·인제스트, stats RPC, /auth/me access_token, /api/auth-token route, Realtime setAuth, migrate_storage_to_per_user.py(move + 폴백).
- 테스트 4종 신규(rls_isolation 12 + storage_prefix 9 + stats_chunks 6 + realtime_jwt 4).
- 검증: 1298 → 1328 OK(+30), 프론트 lint/build clean.

### 1.4 senior-qa 검증 (조건부 PASS)
- ship 게이트 6/9 PASS(회귀·무중단·RLS·Storage·P1#2·Realtime), 3건 deploy 후 측정.
- **P1#2 발견** — `get_chunks_stats_for_user` SECURITY DEFINER + GRANT TO authenticated = cross-user 카운트 누출 위험. anon/authenticated 키 노출 시 임의 UUID 호출 가능.
- **P1#3 발견** — PART 1 SQL 적용 후 script 완료 전 사이 storage.get(새 path) 404 가능. 분 단위 downtime 위험.
- P1#1(문서 명시) + P2 6건(트래킹).

### 1.5 senior-developer P1 패치 (보안 + 운영 정책)
- **P1#2 차단**: GRANT 라인 `TO authenticated, service_role` → `TO service_role` 좁힘 + 함수 본문 진입부에 `auth.uid() <> user_id_arg` 가드(이중 방어, service_role 호출 시 NULL 통과). SQL 검증 테스트 2건 추가.
- **P1#3 차단**: 020 SQL 헤더 + README 에 **패턴 A(단순, ~1분 downtime) / 패턴 B(downtime 0, copy → PART 1 → cleanup)** 두 deploy 흐름 명시. script 에 `--copy-only` / `--cleanup-only` 모드 추가(보수적 skip 정책).
- **P1#1**: README 에 "auth_enabled 무관 user prefix 적용" 단락 명시.
- **P2#3 차단**: Realtime hook 의 setAuth IIFE → fetch→setAuth→subscribe sequential await chain + cancel flag.
- 검증: 1328 → 1330 OK(+2 P1#2 SQL 검증), 프론트 lint/build clean.

### 1.6 commit + push
- single commit `31f1e9a` (18 files +2213/-118). main push 완료(`da5c640..31f1e9a`).

---

## 2. 누적 변경 (커밋 `31f1e9a`)

### 백엔드 신규
- `api/migrations/019_rls_policies.sql` — RLS 7 테이블 25 정책 + RPC `get_chunks_stats_for_user`(P1#2 가드 포함)
- `api/migrations/020_storage_per_user_prefix.sql` — PART 1(storage_path UPDATE) + PART 2(Storage RLS 4 정책)
- `api/scripts/migrate_storage_to_per_user.py` — Storage 객체 이동(default move / `--copy-only` / `--cleanup-only` / `--dry-run`)
- `api/tests/test_rls_isolation.py`, `test_storage_per_user_prefix.py`, `test_stats_chunks_per_user.py`, `test_realtime_jwt_auth.py`

### 백엔드 수정
- `api/app/adapters/impl/supabase_storage.py` — `build_user_path`/`build_pending_path` static helper, `_legacy_path` private, 헤더 갱신
- `api/app/routers/documents.py` — `_PENDING_PATH_NAMESPACE` 제거 + pending_path + BG 호출 4곳 user_id 전달
- `api/app/ingest/upload.py` — `run_full_ingest(user_id=None)` 시그니처, final_path 분기(회귀 0)
- `api/app/routers/stats.py` — `_compute_chunks_stats_via_rpc` RPC 적용 + graceful
- `api/app/routers/auth.py` — `AuthMeResponse.access_token` + `_extract_request_token`
- `api/migrations/README.md` — 019·020 + D2 deploy 패턴 A/B 분기
- `api/tests/test_invite_codes.py`, `test_e2e_pipeline.py` — D2 시그니처 호환

### 프론트 신규
- `web/src/app/api/auth-token/route.ts` — same-origin GET, no-store cache

### 프론트 수정
- `web/src/lib/hooks/use-active-docs-realtime.ts` — fetch→setAuth→subscribe sequential await chain, cancel flag

### 문서
- `work-log/2026-05-20 D2 RLS+Storage prefix+stats 누출 차단 plan.md` v1.0 (마스터)
- 본 문서

---

## 3. 테스트·검증 진전

| 시점 | 단위 테스트 | failures(환경 flaky) | skipped | 비고 |
|---|---:|---:|---:|---|
| D1 push 후 시작 | 1298 | 7 | 0 | HEAD `da5c640` |
| senior-developer 본 구현 후 | 1328 | 7 | 1 | +30 D2 신규 (RLS E2E ENV 가드 skip) |
| P1 패치 후 (commit `31f1e9a`) | **1330** | 7 | 1 | +2 P1#2 SQL 검증 |
| 환경 flaky 셋 | `test_embed_cache`×4 + `test_synonym_inject`×1 + `test_vision_caption_propagation`×2 | — | — | 본 세션 unchanged, D2 무관 |

프론트: lint 0 error / build PASS / 15 route + `/api/auth-token` 신규 등록.

---

## 4. D2 ship 게이트 통과 여부

| 게이트 | 결과 |
|---|---|
| 1. 코드·테스트·문서 완료 | ✅ |
| 2. 단위 테스트 회귀 0 | ✅ (1298→1330, failures 7 동일) |
| 3. 무중단 deploy 보장(auth_enabled=false + 마이그 미적용 안전) | ✅ |
| 4. RLS 정책 7 테이블 × 4 + invite SELECT + RPC 등록 | ✅ |
| 5. P1#2 차단 + Realtime JWT | ✅ (P1#2 GRANT 좁힘 + 본문 가드, P2#3 race 해소) |
| 6. 보안 audit(service_role 노출 0 / anon 직접 쿼리 = Realtime 1건) | ✅ |

ship 진입 가능. **D1 ship 완료(018 + ENV `true`) 후 D2 deploy 9단계** 진행.

---

## 5. D2 deploy 절차 (사용자 작업, D1 ship 완료 후)

### 공통 선두 4단계
1. 본 commit `31f1e9a` Railway redeploy(`auth_enabled=false` default 무중단)
2. `018_migrate_default_user.sql` apply(D1 ship 의 일환 — 미실행 시 본 단계가 D1 ship 의 마지막)
3. `019_rls_policies.sql` apply (Supabase SQL Editor 에서 전체 paste → Run). RLS 정책 25 + RPC 등록
4. Railway `JETRAG_AUTH_ENABLED=true` 등록 + redeploy

### 패턴 A — 단순 (단일유저 베타 권장, ~1분 downtime)
5A. `020_storage_per_user_prefix.sql` PART 1 만 apply
6A. `cd api && uv run python scripts/migrate_storage_to_per_user.py --dry-run` → 영향 row 수 확인
7A. `cd api && uv run python scripts/migrate_storage_to_per_user.py` (default move)
8A. PART 2 apply
9A. smoke (본인 JWT 다운로드 200, anon 403, Realtime 본인 row only)

### 패턴 B — downtime 0 (멀티유저 운영)
5B. `migrate_storage_to_per_user.py --copy-only --dry-run`
6B. `--copy-only` 실행 (old/new 양쪽 존재)
7B. PART 1 apply
8B. `--cleanup-only` (new 존재 확인 후 old 제거, 보수적 skip)
9B. PART 2 apply
10B. smoke

---

## 6. senior-qa 트래킹 (P2 미해소 6건, D3+ 검토)

| # | 항목 | 우선 |
|---|---|---|
| P2#1 | `ingest_jobs.doc_id` NULL row 모니터링 SQL 주 1회 | low |
| P2#2 | stats RPC fail 시 `error_code='migrations_pending'` hint | low |
| P2#4 | `/api/auth-token` 에 `Vary: Cookie` 추가(edge cache 보강) | low |
| P2#5 | README 9단계 ↔ 020 헤더 Step 번호 master 명시 | trivial |
| P2#6 | migrate script `--dry-run` 의 `_object_exists` 호출 허용(skip 예측) | low |

---

## 7. 다음 sprint 후보

### 사용자 작업 (D1 ship + D2 deploy)
- 가장 가치 큼: D1 dashboard·ENV·가입·018 → D2 9단계 deploy → smoke. ~3~4h.
- 막힘 시 Claude 보조: dashboard 가이드, smoke 스크립트, SQL 미세조정.

### Claude 병행 후보 (이전 세션 §5 변동 없음)
| 순위 | 항목 | 작업량 | 가치 |
|---|---|---|---|
| 1 | woong-s.com root 포트폴리오 landing | 4~8h | ★★★★ 이직 어필 |
| 2 | 데모 GIF / 검색 결과 스크린샷 | 1~2h | README 10/10 |
| 3 | M3 sprint (BIAS-1/UX-1) | 2~4h | 검색 품질 정합 |
| 4 | D3 plan (per-user cap + rate limit) | 1~2h | D2 deploy 후 진입 |
| 5 | D3 전체 구현 | 4~6h | D2 deploy + 베타 진입 후 |

### 멀티유저 베타 게이트
**D1 ship + D2 deploy 9단계 + smoke 통과 시점 = `JETRAG_AUTH_ENABLED=true` 다중유저 베타 30명 공개 가능**(senior-qa D1 ship 게이트 해소).

---

## 8. 미응답 의사결정 (D1 세션 인계)

| Q | 항목 | 상태 |
|---|---|---|
| Q1 도메인 / Q9 공개범위·Auth | ✅ 해소 |
| Q8 결제 모델 | ⏸ deferred(베타 후) |
| Q10 공개 시점 | ⏸ Q8 의존 |
| Q11 Vercel 라이선스 | ⏸ Q8 의존 |

---

## 9. 회고

### 9.1 ROI 가장 높은 결정
- **planner 11 결정 일괄 채택** — "권장 방법으로 진행해" 한 마디로 sprint 진입. plan v0.1 → v1.0 변환 즉시.
- **옵션 B helper 채택** (senior-developer 설계 검토 단계) — Protocol 변경 0, 호출부 변경 최소. plan §3.4 의 "put 시그니처 변경" 안보다 깔끔.

### 9.2 senior-qa 가 잡은 핵심 결함 (예방)
- **P1#2 SECURITY DEFINER GRANT** = 코드 deploy 후에야 노출됐을 보안 누출. plan 단계에서 잡힌 게 아니라 검증 단계에서 잡힘 — QA 게이트의 가치 실증.
- **P1#3 downtime window** = 운영 시점에야 느꼈을 503/500. plan §3.2 의 3-step 무중단 표현이 다운로드/검색 영향까지 명시 안 한 약점을 검증이 보완.

### 9.3 D1→D2 한 세션 처리 학습
- D1 push 직후 D2 진입이 자연스러웠던 이유 = "여러 유저가 각자 데이터를 독립적으로?" 사용자 질문이 트리거. **사용자 의도(베타 공개 게이트 해소)** 가 sprint 우선순위를 명확히 함.
- D2 코드 작업 + D1 ship 사용자 작업 = 진정한 병렬. 의존성 0(service_role bypass + 마이그 미적용 안전).

---

## 10. 참조

- 마스터: `work-log/2026-05-20 D2 RLS+Storage prefix+stats 누출 차단 plan.md` v1.0
- D1 마스터·종합: `work-log/2026-05-20 D1 멀티유저 Auth plan.md` + `work-log/2026-05-20 세션 종합 — D1 멀티유저 Auth 구현 + QA 통과 + P1#1 차단.md`
- commit `31f1e9a` — `feat(rls-d2): RLS 정책 7테이블 + Storage per-user prefix + stats 누출 차단 + Realtime JWT`

## 11. production URLs (live, D2 코드 deploy 후 / 마이그 미적용)

- https://jetrag.woong-s.com — frontend (auth_enabled=false 무중단)
- https://jetrag-api.woong-s.com — backend (D2 코드 + service_role bypass)
- Realtime: anon key, RLS publication 정책 미적용 = 전체 row 자연 deny(D1 P0 보전)
