# 2026-05-14 P1 fix — vision_budget cache hit 차단 회귀

> M3 진입 전 잡일 정리 1단계. 핸드오프 §3.4 의 P1 항목 (vision_budget 누적 버그) 해결.
> 변경 핵심: 사전 cap check 직전에 `vision_page_cache` pre-scan → 모두 hit 이면 사전 check 우회.

---

## 1. 한 줄 요약

`documents.flags.vision_budget.used_usd` (= `vision_usage_log` doc_id SUM, historical) 가
재인제스트 시 사전 cap check 를 차단하던 회귀를 해결. **모든 페이지가 `vision_page_cache` hit
이면 사전 check 우회** (신규 비용 0 보장). inner-loop check 는 종전대로 cache miss 발생 시
보호 유지.

---

## 2. 버그 path (M2 W-4 1차 시도 회귀의 직접 원인)

```
첫 reingest:
  - 모든 페이지 cache miss → Gemini Vision API 호출
  - vision_usage_log insert N row (각 row 에 estimated_cost)
  - vision_page_cache upsert N row (v2 prompt_version)

두 번째 reingest (예: M2 W-4 의 13 doc 클린 재처리):
  - documents 의 doc_id 별 vision_usage_log SUM = $0.6~0.7 (1차 누적, "historical")
  - 사전 cap check (extract.py:220 / incremental.py:440):
      check_combined → _sum_doc_cost → historical SUM 반환
      JETRAG_DOC_BUDGET_USD=0.10 default → used > cap → allowed=False
  - vision_page_cache lookup 단 1회도 도달 못 함
  - 결과: vision OCR skip → vision_chunks 0 → top-1 0.7910 → 0.6554 (큰 회귀)
```

inner-loop check (페이지 N 마다 재검사) 는 정상이었다: cache hit 페이지는 `image_parser.parse`
가 `vision_metrics.record_call` 호출 자체를 skip → `vision_usage_log` row insert 0 → SUM 증가 0.
**진짜 문제는 "사전 check 가 cache lookup 전에 historical SUM 으로 차단"**.

---

## 3. Fix 채택안 — Option K (cache pre-scan via count_uncached_pages)

### 검토한 옵션 (3회 비판)

| 옵션 | 변경 범위 | 보호 의미 | 회귀 위험 |
|---|---|---|---|
| **A. 사전 check 자체 제거** | 작음 | inner-loop 만 — 첫 페이지 1회 vision call 가능 | cap 도달 doc 의 첫 페이지 비용 미미 — 허용 가능 |
| **B. reingest-local 카운터** | 큼 | 정확 | architecture 변경, 회귀 risk ↑ |
| **C. effective_used 계산** | 중 | 정확 | 페이지 평균 비용 추산 필요 — 추정 base 약함 |
| **K. cache pre-scan 후 조건부 skip** | **작음** | **종전 보호 유지 + cache hit only 우회** | **안전** |

→ **K 채택**. 변경 작고, "신규 비용 0" 케이스만 우회하므로 보호 의미 그대로.

### 누락된 옵션 (3차 비판)

- "사전 check 의 cap 비교 자체를 cache 보정" — 평균 페이지 비용 추산 필요, base 약함.
- "vision_budget 컬럼을 reingest 시점에 reset" — DB 마이그 + 회귀 위험. 보수적 선택지로는
  Option K 보다 큰 변경.

### 검증 안 된 가정

- cache hit 페이지가 진짜 `vision_usage_log` row 0 인가? → `image_parser.py:98` 에서
  cache hit 시 즉시 return, `vision_metrics.record_call` 호출 X. **확인됨**.
- inner-loop check 가 cache hit only path 에서 통과하는가? → SUM 변화 0 → 조건 미충족 →
  통과. **확인됨**.

---

## 4. 변경 파일

### 4.1 `api/app/services/vision_cache.py` — `count_uncached_pages` 추가

```python
def count_uncached_pages(sha256: str, pages: list[int]) -> int | None:
    """주어진 페이지 리스트 중 (sha256, page, prompt_version) 캐시 미스 수.

    - 0       : 모든 페이지 cache hit → 신규 비용 0 (사전 cap check 우회 가능)
    - N (>0)  : N 개 cache miss (사전 cap check 적용 권장)
    - None    : DB 부재 / disable / 인자 부적합 → 보수적 fallback
    """
```

- 단일 IN query → 페이지 N 개 round trip 1회 (latency 무시).
- DB 실패 graceful (lookup 의 `_warn_first` 패턴 재사용).
- 빈 pages → 0 / falsy sha256 → None.

### 4.2 `api/app/ingest/stages/extract.py` — `_vision_pre_check_all_cached` 헬퍼 + 사전 check 분기

```python
pre_check_skipped = _vision_pre_check_all_cached(data, sha256=doc_sha256)
if pre_check_skipped:
    logger.info("PDF vision enrich — 모든 페이지 cache hit, 사전 cap check 우회 (doc_id=%s)", doc_id)
    pre_status = budget_guard.BudgetStatus(
        allowed=True, ..., reason="모든 페이지 cache hit — 사전 check 우회 (P1 fix)",
    )
else:
    pre_status = budget_guard.check_combined(...)
```

- `_vision_pre_check_all_cached`: fitz.open 으로 페이지 수 파악 + `count_uncached_pages`
  호출. graceful — fitz.open 실패 / sha256 None / count None → False (보수적 fallback).
- 사전 check 분기 후 `not pre_status.allowed` 로직은 종전과 동일.

### 4.3 `api/app/ingest/incremental.py` — vision_cache import + 사전 check 분기

```python
pre_check_skipped = False
if doc_sha256 and missing:
    uncached = vision_cache.count_uncached_pages(doc_sha256, pages=list(missing))
    if uncached == 0:
        pre_check_skipped = True
        logger.info("incremental_vision — missing %d 페이지 모두 cache hit, 사전 cap check 우회 (doc=%s)", ...)
if pre_check_skipped:
    pre_status = budget_guard.BudgetStatus(allowed=True, ..., reason="모든 missing 페이지 cache hit — 사전 check 우회 (P1 fix)")
else:
    pre_status = budget_guard.check_combined(...)
```

### 4.4 단위 테스트

- `api/tests/test_vision_cache.py` — `VisionCacheCountUncachedTest` 클래스 7건 추가:
  - 모두 hit → 0
  - 일부 hit → 미스 수
  - 모두 miss → len(pages)
  - DB 실패 → None
  - falsy sha256 → None
  - 빈 pages → 0
  - ENV disable → None

- `api/tests/test_vision_pre_check_bypass.py` — 신규 9건:
  - `_vision_pre_check_all_cached`: sha256 None / fitz.open 실패 / count==0 / count>0 /
    count None / 1-based pages 전달 확인 등
  - incremental 분기 시그니처: count==0 → budget_guard 호출 X / count>0 → 호출 O

---

## 5. 회귀 검증

- 변경 직후 vision_cache 관련 단위 테스트 27건 (기존 18 + 신규 9) **전부 통과**.
- 전체 단위 테스트 1140 건 중 **2건 실패** — `test_admin_feedback.test_basic_mapping`,
  `test_admin_queries.test_basic_mapping`.
- 변경 전 main HEAD (`a7c3a92`) 에서도 같은 2건 실패 (git stash 후 재실행 확인) → **본 fix
  와 무관**. 오늘 날짜 변경 (2026-05-13 → 2026-05-14) 으로 daily window 계산 영향 가능. 별도
  잡일 (P3?) 로 분리.

---

## 6. 운영 영향

### 6.1 정상 path (변경 0)
- 첫 인제스트: vision_page_cache 비어 있음 → uncached > 0 → 사전 check 적용 → 종전 동작.
- cap 도달 case (예: $0.10 cap 도달한 신규 PDF): 사전 check 차단 → 종전 동작.

### 6.2 회귀 fix path
- 같은 doc 재인제스트 (예: M2 W-4 의 13 doc 클린): 모든 페이지 cache hit (v2 prompt_version
  매칭) → 사전 check 우회 → `_enrich_pdf_with_vision` 진입 → cache hit only path → vision API
  호출 0 → vision_chunks 정상 생성.

### 6.3 안전망
- `count_uncached_pages` None 반환 (DB 부재 / disable) → 보수적으로 사전 check 적용 (종전 동작).
- inner-loop check 는 항상 cache miss 발생 시 SUM 증가 감지 → cap 도달 시 sweep break.
  → cap 도달 doc 이라도 cache miss 페이지가 있으면 첫 N 페이지의 비용은 inner-loop 가 차단.

### 6.4 .env 정리 권고
- `JETRAG_DOC_BUDGET_USD=2.00` 등 임시 상향 → default ($0.10) 으로 복귀 안전 (P1 fix 후
  재인제스트 시 cache hit only path 가 cap 무관 동작).
- 본 변경에선 .env 미수정 — 다음 작업에서 별도 정리.

---

## 7. P2 (다음 잡일 정리)

핸드오프 §6.2 P2 — `api/scripts/m2_w4_full_reingest.py` 의 `--out-md` → `--out` 1줄 fix.

---

## 8. commit 흐름

| 단계 | 결과 |
|---|---|
| 1. 진단 (extract.py / incremental.py / vision_cache.py / image_parser.py 코드 흐름 추적) | "historical SUM 사전 check 차단" 확정 |
| 2. fix 옵션 비판 (A/B/C/K) | K 채택 (변경 작음, 보호 유지) |
| 3. 구현 (vision_cache + extract + incremental + 단위 테스트) | 1 commit 안에 통합 |
| 4. 회귀 검증 (단위 테스트 1140) | 본 fix 무관 2건 외 통과 |
| 5. work-log + commit + push | (본 문서 후 수행) |

---

## 9. 다음 후보 (사용자 확인 필요)

1. **P2** (`m2_w4_full_reingest.py` 1줄 fix) — ~5분, $0
2. **W-9.5 BM25 ablation harness** (KPI #7 측정 인프라) — 1~2일, $0
3. **admin 테스트 2건 실패 진단** (날짜 변경 영향 의심) — ~1시간, $0
4. **W-9 D1 extractive summary** (사용자 가치 최대) — ~1~2일, $0

추천 순서: **P2 (5분) → admin 테스트 실패 진단 (1시간) → W-9.5 또는 W-9 D1**.
