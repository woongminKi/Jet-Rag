# Phase 1 S0 D2 — `vision_page_cache` lookup/upsert 통합

날짜 2026-05-07
sprint master plan §6 S0 D2 — sprint plan §11.7 의 E1-A3 흡수
선행 마이그 015 (D1 적용 완료) — 본 작업은 코드 통합만

---

## 1. 결정

### 1.1 cache integration 위치 — `ImageParser.parse()` 안

후보:
- (A) 호출처 (extract.py / incremental.py) 가 lookup → caption.parse 호출 → upsert 직접 수행
- (B) ImageParser.parse 가 sha256/page 옵션 인자 받아 내부에서 lookup/upsert (채택)

(B) 채택 이유:
- 호출처 중복 코드 0 — extract / incremental 양쪽이 같은 함수 호출 1줄
- cache hit 시 captioner.caption() 호출 자체가 skip → vision_metrics.record_call 도 skip → vlog 자연 절감 측정
- 단독 이미지 호출 (sha256=None) 영향 0 (조건 분기로 자연 skip)

### 1.2 prompt_version

`api/app/services/vision_cache.py` 의 모듈 상수 `_VISION_PROMPT_VERSION = "v1"`.
ENV `JETRAG_VISION_PROMPT_VERSION` 으로 override 가능 — 운영자가 prompt 변경 시 bump.

### 1.3 vlog 정합성 — cache hit 시 vlog insert 0

cache hit fast path 가 `_captioner.caption()` 호출 자체를 skip → `vision_metrics.record_call` 진입 안 함 → `vision_usage_log` insert 0. 절감 측정은 `vision_page_cache.estimated_cost` 와 `vision_usage_log.estimated_cost` 비교로 사후 분석.

### 1.4 race 보호

`upsert(on_conflict="sha256,page,prompt_version", ignore_duplicates=True)` — ON CONFLICT DO NOTHING 동등. 동시성 호출 시 먼저 저장된 row 우선.

---

## 2. 변경 파일

### 2.1 신규
- `api/app/services/vision_cache.py` — lookup/upsert 헬퍼. DB 부재 graceful (vision_metrics 패턴 재사용).
- `api/tests/test_vision_cache.py` — 11 단위 테스트.

### 2.2 수정
- `api/app/adapters/impl/image_parser.py`
  - `parse(..., sha256=None)` 인자 추가
  - cache lookup → hit fast path (captioner skip) / miss → 기존 호출 + upsert
  - sections 합성 로직을 `_compose_result()` 헬퍼로 추출 (cache hit / miss 동일 구조 보장)
- `api/app/ingest/stages/extract.py`
  - `_fetch_document` SELECT 에 `sha256` 추가
  - `_enrich_pdf_with_vision` 시그니처에 `sha256` 추가, 페이지 루프의 `image_parser.parse` 호출에 sha256 전달
- `api/app/ingest/incremental.py`
  - `documents` SELECT 에 sha256 추가, `_vision_pages_with_sweep` 시그니처에 sha256 추가, 페이지 루프 전달

---

## 3. 단위 테스트

`api/tests/test_vision_cache.py` 11건 신규:
- `VisionCacheLookupTest` 5건 — hit/miss/DB 실패 graceful/ENV disable/prompt_version eq 필터
- `VisionCachePromptVersionInvalidateTest` 1건 — v1 row 있지만 v2 lookup 시 miss
- `VisionCacheUpsertTest` 2건 — upsert 정확한 row + ON CONFLICT 옵션 검증, DB 실패 graceful
- `ImageParserCacheIntegrationTest` 3건
  - cache hit 시 captioner.caption 호출 0 + vision_metrics 0 + sections 동일 합성
  - cache miss 시 captioner 1회 + vision_cache.upsert 1회 (estimated_cost 보존)
  - sha256=None 시 cache 자체 skip (단독 이미지 호출 보호)

전체 단위 테스트 542 건 OK (이전 531 → +11, skip 1). 회귀 0.

---

## 4. cache hit 검증 절차 (E2E — 사용자 별도 수행)

본 ship 단계 DoD 는 단위 테스트로 충족. 실 환경 검증은 사용자 컴퓨터에서:

1. PDF 1건을 vision_enrich 활성으로 reingest (`JETRAG_PDF_VISION_ENRICH=true`)
2. 같은 PDF 를 다시 reingest
3. 검증 SQL:
   ```sql
   -- 두 번째 reingest 후 vision_page_cache 가 채워졌는지
   SELECT COUNT(*) FROM vision_page_cache WHERE sha256 = '<doc.sha256>';
   -- → 페이지 수만큼 row 존재

   -- 두 번째 reingest 동안 vision_usage_log 새 insert 0 인지
   SELECT MAX(called_at) FROM vision_usage_log WHERE doc_id = '<doc_id>';
   -- → 첫 reingest 시점 이후 추가 row 0
   ```

prompt_version 변경 invalidate 검증:
- ENV `JETRAG_VISION_PROMPT_VERSION=v2` 로 reingest → vision_usage_log 새 row 생성됨 + vision_page_cache 에 prompt_version='v2' row 추가

---

## 5. 회귀 보호

- sha256 None / page None 시 cache lookup/upsert 0 — 단독 이미지 호출 (PPTX rerouting 등) 영향 0
- DB 부재 / 마이그 015 미적용 시 graceful — lookup None / upsert no-raise (vision_metrics 패턴)
- W25 D14 sonata 회귀 보호: 같은 PDF reingest 시 chunks·sections 결과 동일성 보장 (cache 의 result JSONB 가 VisionCaption 4필드 직렬화)
- ENV `JETRAG_VISION_CACHE_ENABLED=0` 으로 cold-start 회복 가능

---

## 6. 다음 후보

master plan §6 S0 후속:
- **D3** — budget 데이터 기반 초기값 (1주 누적 → 평균 단가 X 원/page → DOC_BUDGET / DAILY_BUDGET 도출)
- **D4** — `documents.flags.vision_budget_exceeded` graceful degrade UI
- **D5** — 11 docs 재인제스트 + 골든셋 v1 으로 효과 측정 (top-1 영향 ≥ 0, 비용 60%+ 절감)

또는 sprint plan §11 의 E1 2차 ship 잔여:
- (A1) GPU 토글 + dense 임베딩 batch 32 가속
- (A2) sparse 임베딩 동시화

권고: **D5 (효과 측정)** 우선 — vision_page_cache 의 절감 효과를 데이터로 검증한 후 D3 budget 초기값 산정. 다만 D5 는 reingest + 골든셋 측정 → 1~2일 걸림.

대안 권고: **E1-A1 (GPU dense batch 32)** 먼저 — vision_page_cache 통합 후에도 인제스트 latency 의 다른 병목이 dense 임베딩 → 효과가 즉시 측정됨.

사용자 의견 필요: D3 / D4 / D5 / E1-A1 중 어느 것 ?

---

## 7. push 상태

본 작업은 commit 만 (push 는 사용자 명시 후).
