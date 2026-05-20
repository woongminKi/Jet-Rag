# 2026-05-20 D2 멀티유저 RLS 정책 + Storage per-user prefix + stats 누출 차단 — 구현 계획서 v1.0 (사용자 승인 — planner 추천값 일괄 채택)

> senior-planner 작성(v0.1) → 사용자 "권장 방법으로 진행" 지시로 11건 미결 전부 planner 추천값 채택 → **v1.0 승인본**.
> 기준 코드: HEAD `da5c640` (D1 ship), 마이그 최신 018 → **신규 = 019, 020**, 단위 테스트 1298 OK.
> production live: jetrag.woong-s.com / jetrag-api.woong-s.com. `JETRAG_AUTH_ENABLED=false` default.
> 다음: senior-developer 구현(코드 작업, **migration apply 보류**) → senior-qa 검증 → 사용자 D1 ship 완료 후 → D2 deploy.

---

## 0. D2 한 줄 정의

Supabase RLS 정책으로 **DB layer 격리(방어 심도)** + Storage 객체 **`user/<uid>/<sha256>{ext}`** prefix 이관 + **stats `chunks_total` 전역 count 누출(P1#2) 차단** + Realtime client JWT 주입. 백엔드는 service_role 유지(RLS bypass + app-layer 격리). 본 sprint 가 닫히면 senior-qa "D2 RLS 미완 = `auth_enabled=true` 다중유저 베타 공개 금지" 게이트 해소.

---

## 1. 확정된 결정 11건 (planner 추천값 일괄 채택)

| # | 항목 | 결정 |
|---|---|---|
| Q1 | `vision_page_cache` / `embed_query_cache` 글로벌 공유 | **유지** (sha256 키, PII 없음, 비용 절감) |
| Q2 | 버킷 분리 vs 단일 + prefix | **단일 버킷 `documents` + prefix** |
| Q3 | Storage 정책 위치 | **SQL 마이그 020** (repo 단일 진실 원천) |
| Q4 | D1 ship 완료 여부 | **deploy 게이트** — 코드 작업은 진행. 마이그 020 apply 는 D1 ship + 018 + ENV `true` 후 |
| Q5 | Supabase SDK `storage.move()` 지원 | **move() 시도 + 폴백 download→upload→delete** |
| Q6 | `SupabaseBlobStorage.put` 시그니처 | **`user_id` 명시 추가** (호출부 4곳 수정) |
| Q7 | chunks stats 옵션 | **B (RPC `get_chunks_stats_for_user`)** |
| Q8 | Realtime JWT 주입 | **D2 포함** (+0.5h, 베타 게이트 해소) |
| Q9 | web/src anon supabase 사용처 | senior-developer/qa 가 grep 으로 확인. Realtime 만이면 영향 0 |
| Q10 | `ingest_jobs.doc_id` NULL 행 RLS | **현 시점 미보강** — 베타 모니터링 후 결정 |
| Q11 | answer_feedback/ragas user_id 인덱스 | **D2 보류** — row 적음, D3+ 데이터 누적 시 |

---

## 2. 영향받는 테이블 / RLS 정책 7건

(planner §3.1 표 그대로)

| 테이블 | 정책 핵심 | 인덱스 | 비고 |
|---|---|---|---|
| `documents` | `user_id = auth.uid()` + `deleted_at IS NULL` (SELECT) / WITH CHECK (I/U/D) | `idx_documents_user_created` 활용 | service_role bypass |
| `chunks` | `EXISTS (SELECT 1 FROM documents d WHERE d.id = chunks.doc_id AND d.user_id = auth.uid() AND d.deleted_at IS NULL)` | `idx_chunks_doc` + `idx_documents_user_created` | doc_id 서브쿼리(D1-Q8) |
| `ingest_jobs` | `EXISTS (... documents.user_id = auth.uid())` | `idx_ingest_jobs_doc` | Realtime publication 자동 격리 |
| `ingest_logs` | `EXISTS (... ingest_jobs JOIN documents ...)` 2-hop | `idx_ingest_logs_job` | **2-hop JOIN 성능 측정 필요** |
| `answer_feedback` | `user_id = auth.uid()` (I/U/D 동일) | (인덱스 D2 보류) | service_role bypass |
| `answer_ragas_evals` | `user_id = auth.uid()` | (인덱스 D2 보류) | service_role bypass |
| `invite_codes` | SELECT: `used_by = auth.uid()` / I/U/D 차단(service_role 만) | `idx_invite_codes_unused` | redeem 은 service_role 라우터가 처리 |

**정책 불요(글로벌 운영)**: vision_usage_log, search_metrics_log, vision_page_cache, embed_query_cache — 002 패턴(정책 없음 + service_role only) 유지.

---

## 3. Storage per-user prefix 이관 (3-step 무중단)

### 3.1 새 경로 패턴
- pending(router): `user/<uid>/pending/{uuid}{ext}`
- final(BG): `user/<uid>/{sha256}{ext}`
- 버킷: `documents` (단일, Private)

### 3.2 3-step 적용 순서
1. **코드 deploy** (무중단) — 신규 업로드만 user prefix. `get/delete/signed_url` 은 구식 root-level path 도 수용(blob_id=path 직접).
2. **1회성 마이그** — 마이그 020 SQL `UPDATE documents.storage_path = 'user/' || user_id::text || '/' || storage_path WHERE storage_path NOT LIKE 'user/%'` + Storage move 스크립트 `api/scripts/migrate_storage_to_per_user.py` (move() 시도 → 실패 시 download/upload/delete 폴백, idempotent).
3. **Storage RLS 활성화** — 모든 객체 user prefix 통일 후 020 의 RLS 정책 4개 SELECT/INSERT/UPDATE/DELETE 적용.

### 3.3 Storage RLS 정책 (마이그 020)
```sql
CREATE POLICY "documents_select_own" ON storage.objects
  FOR SELECT TO authenticated
  USING (
    bucket_id = 'documents'
    AND (storage.foldername(name))[1] = 'user'
    AND (storage.foldername(name))[2] = auth.uid()::text
  );
-- INSERT/UPDATE/DELETE 동일 패턴 (WITH CHECK + USING)
```

### 3.4 어댑터/라우터 변경
- `api/app/adapters/impl/supabase_storage.py`: `_build_path(user_id, sha256, file_name)` + `put(user_id=...)`. 헤더 주석 "sha 공유" → "유저별 분리" 갱신.
- `api/app/routers/documents.py:74-77`: `_PENDING_PATH_NAMESPACE` 제거.
- `api/app/routers/documents.py:521,718`: pending_path = `user/<uid>/pending/...`
- `api/app/routers/documents.py:939`: put 호출 시 user_id 전달
- `api/app/ingest/upload.py:31-81`: `run_full_ingest(..., user_id=...)`, final_path = `user/<uid>/{sha256}{ext}`
- `api/app/ingest/stages/extract.py:177`, `incremental.py:411`: put 호출 user_id 전달

---

## 4. P1#2 stats chunks_total 누출 차단

마이그 019 안에 RPC `get_chunks_stats_for_user(user_id_arg UUID)` 포함:
```sql
CREATE OR REPLACE FUNCTION get_chunks_stats_for_user(user_id_arg UUID)
RETURNS TABLE(total BIGINT, filtered BIGINT, breakdown JSONB)
LANGUAGE SQL STABLE AS $$ ... $$;
```
- `api/app/routers/stats.py:214,288` 를 RPC 호출로 치환. `ChunksStats` 응답 schema 동일 유지(프론트 무영향).

---

## 5. Realtime JWT 주입 (Q8 = D2 포함)

- 옵션 (a) `supabase.realtime.setAuth(jwt)` 호출 후 구독
- `/auth/me` 가 access_token 도 반환하도록 D1 endpoint 확장(작은 변경)
- 프론트 Realtime client 가 server action 으로 토큰 fetch → setAuth → subscribe
- 미주입 환경에서도 polling fallback(`documents/active`) 유지 — 회귀 위험 0

---

## 6. 코드 영향 4분류

### 재사용
service_role 클라이언트, RPC `user_id_arg`, user_id 컬럼, `idx_documents_user_created`/`idx_chunks_doc`, D1 `get_current_user`/`CurrentUserDep`, `SupabaseBlobStorage.put_at`.

### 신규
- `api/migrations/019_rls_policies.sql` (7 테이블 정책 + RPC `get_chunks_stats_for_user`)
- `api/migrations/020_storage_per_user_prefix.sql` (storage_path UPDATE + Storage RLS 정책)
- `api/scripts/migrate_storage_to_per_user.py` (Storage move 1회성, idempotent)
- 테스트: `test_rls_isolation.py`, `test_storage_per_user_prefix.py`, `test_stats_chunks_per_user.py`, `test_realtime_jwt_auth.py`

### 수정
- `api/app/adapters/impl/supabase_storage.py` (시그니처)
- `api/app/routers/documents.py:74,521,718,939` (pending path + put)
- `api/app/ingest/upload.py:31-81` (run_full_ingest user_id + final_path)
- `api/app/ingest/stages/extract.py:177`, `incremental.py:411` (put user_id)
- `api/app/routers/stats.py:214,281-313` (RPC 적용)
- `api/app/routers/auth.py` (/auth/me 응답에 access_token 포함)
- `web/src/lib/supabase/client.ts` (또는 별도 hook): Realtime setAuth 주입
- `api/migrations/README.md` (019·020 행)

### 개발 금지 (D2 범위 밖)
D3 per-user cap, D4 법적, vision_usage_log per-user 분리, 결제, 팀 공유, invite_codes 직접 GET endpoint.

---

## 7. 무중단·사이드이펙트 대응

| 위험 | 대응 |
|---|---|
| anon supabase 직접 쿼리 회귀 | senior-qa grep audit. Realtime 외 사용 발견 시 plan 보강 |
| Storage 마이그 도중 reingest 발생 | 마이그 시점 대시보드 안내 1줄 |
| RLS 정책 활성화 후 검색 P95 회귀 | golden_v2 + production 측정. 회귀 < 100ms 게이트 |
| Realtime 토큰 미주입 환경 | polling fallback 유지 |
| 020 SQL 이 default_user_id 잔여 row 에 작동 | D1 018 + ENV `true` 선행 전제 (Q4 게이트) |
| service_role 키 유출 | RLS 가 방어 못 함 — D3+ key rotation |
| `ingest_logs` 2-hop JOIN 성능 | 측정 후 LIMIT 1 또는 RPC 우회 |

---

## 8. 단위 테스트 / 검증

### 신규
- `test_rls_isolation.py`: anon client → 0 rows / 2 user 시뮬레이션
- `test_storage_per_user_prefix.py`: `_build_path` user prefix / put 시그니처
- `test_stats_chunks_per_user.py`: RPC 본인 chunks 만 / 격리
- `test_realtime_jwt_auth.py`: setAuth 호출 + 권한 검증

### 회귀
- 백엔드 1298 → 1298 + 신규 (service_role bypass 회귀 0 기대)
- golden_v2 R@10·top-1 불변
- `test_search_user_isolation.py` 재실행
- `test_e2e_pipeline.py` 재실행 (Storage prefix 도입 후)

### Manual smoke (D2 deploy 시)
- anon key 로 다른 user storage 객체 → 403
- 본인 JWT → 200
- user A·B 격리 Realtime

---

## 9. 단계별 체크리스트 (dev ~3.5~5h)

| 단계 | 작업 | 담당 | 추정 |
|---|---|---|---|
| 0 | **prerequisite**: D1 ship 완료 (Q4 게이트 — D2 deploy 시점) | 사용자 | (코드 작업과 병행) |
| 1 | 마이그 019 (RLS 정책 7 + RPC) | dev | 1~1.5h |
| 2 | `SupabaseBlobStorage` 시그니처 + 호출부 4곳 + pending path | dev | 0.5~1h |
| 3 | `stats.py` chunks RPC 적용 | dev | 0.5h |
| 4 | 마이그 020 (storage_path UPDATE + Storage RLS) + `migrate_storage_to_per_user.py` | dev | 0.5~1h |
| 5 | 단위 테스트 4종 + 회귀 fix | dev | 0.5~1h |
| 6 | Realtime JWT 주입(`/auth/me` 확장 + client setAuth) | dev | 0.5h |
| 7 | senior-qa 검증 | qa | 별도 |
| 8 | **deploy 적용** (Q4 ship 게이트 통과 후): 코드 push → Storage move 스크립트 → 마이그 020 RLS 활성화 → smoke | 사용자 | 0.5h |

---

## 10. 다음 sprint 예고

- **D3** per-user 비용 cap + rate limit (`vision_usage_log` user_id 추가, 월 업로드 상한, Vision RPD cap per user)
- **D4** 법적 (Privacy/ToS, 데이터 삭제 요청, GDPR-ish)

---

## 11. KPI/SLO

- KPI #10 P95 2.5s — RLS JOIN/EXISTS 인덱스 활용으로 +0~50ms 추정. **게이트 = 회귀 < 100ms**.
- 검색 정확도 — service_role bypass 라 회귀 0.
- 인제스트 SLO — Storage path 변경은 BG 단계, 5 버킷 회귀 0 기대.
- P1#2 차단 — RPC < 200ms.
