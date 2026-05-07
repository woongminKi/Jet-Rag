# 2026-05-08 — S2 D2 page cap + cost budget 병행 ship

> Sprint: master plan §6 — S2 (Vision 선별 + budget 본 ship) D2
> 산출물: `budget_guard.py` 갱신 + `extract.py` + `incremental.py` + `config.py` ENV + 단위 테스트 +13건 + work-log
> Commit: (ship 후 hash 기록)

---

## 0. 한 줄 요약

> S2 D2 — page cap + cost budget 병행 본 ship. ENV `JETRAG_VISION_PAGE_CAP_PER_DOC` (default **50**) 추가, in-memory 카운터로 cost cap (S0 D4) 과 직교 검사. 두 cap 중 먼저 닿는 지점에서 sweep stop + `flags.vision_page_cap_exceeded=true` 마킹. 단위 테스트 619 → **632 (+13) / skipped 1 / 회귀 0**.

---

## 1. 변경 파일

### 1.1 신규 함수 / API

| 파일 | 신규 | 비고 |
|---|---|---|
| `api/app/services/budget_guard.py` | `check_doc_page_cap(*, called_pages, page_cap)` | in-memory 카운터 비교 (DB 미접근) |
| `api/app/services/budget_guard.py` | `BudgetScope` literal 에 `'page_cap'` 추가 | flags JSON 저장값 |
| `api/app/ingest/stages/extract.py` | `_mark_page_cap_exceeded_flag()` | cost cap 의 `_mark_budget_exceeded_flag` 와 직교 |
| `api/app/config.py` | `Settings.vision_page_cap_per_doc: int` (default 50) | + `_parse_int` 헬퍼 신규 |

### 1.2 hook 추가 위치

```
extract.py: _enrich_pdf_with_vision()
  for sweep_idx in ...:
    for page_num in pending_pages:
      [1] cost cap 재검사 (S0 D4, 매 5p) — 도달 시 sweep break
      [2] page cap 검사 (S2 D2, 매 페이지) — 도달 시 sweep break  ← 신규
      [3] needs_vision 검사 (S2 D1) — False 면 called_count 증가 X
      [4] vision call + called_count += 1
```

`incremental.py:_vision_pages_with_sweep()` 도 동일 흐름. 시그니처 변경 1회 — 반환 tuple 3번째에 `page_cap_status` 추가 (caller 가 flags 마킹 책임 분리).

### 1.3 설계 결정 6건

| # | 결정 | 채택 이유 |
|---|---|---|
| 1 | **in-memory 카운터** (DB SUM 아님) | sweep 흐름 안에서만 누적. latency 0 vs DB SUM 의 0.x초 / 매 페이지 SQL 50회 부담 회피 |
| 2 | **별 함수 `check_doc_page_cap()`** (combined 통합 X) | cost cap 은 DB 의존, page cap 은 in-memory — 인자 시그니처 다름. 호출자에서 두 검사 명시 호출이 명료 |
| 3 | **신규 flag `vision_page_cap_exceeded`** (cost cap 의 `vision_budget_exceeded` 와 분리) | 같은 doc 안 두 cap 동시 도달 가능. 사용자에게 어느 한도가 문제였는지 분리 표시 (UI S2 D3 후속) |
| 4 | **needs_vision skip 페이지는 카운터 증가 X** | 사용자 가치 페이지만 cap 차감 — cap 도달 지연 정합. S2 D1 의 비용 절감 효과 보존 |
| 5 | **시그니처 변경 vs module-level** | `_vision_pages_with_sweep` 는 caller 1개 (`run_incremental_vision_pipeline`) + tests 3건. 시그니처 변경 (반환 tuple +1) 이 동시성 안전 + 명료 |
| 6 | **default 50** (Q-S2-4 핸드오프 권고 채택) | S0 D3 본 PC 5 PDF 측정 평균 21.5p/doc × 2.3배 안전 margin = 49.5 → round 50 |

### 1.4 우선순위 (직교 검사)

```
[per-page check]
  cost cap (3중) → DB SUM, N=5 페이지마다 (idx_vision_usage_created 활용)
    │  scope 우선순위: doc → daily → 24h_sliding (가장 좁은 → 넓은)
    │  도달 시 budget_exceeded_status 저장 + sweep break
    ↓
  page cap → in-memory 카운터, 매 페이지
    │  scope 'page_cap' (별 literal)
    │  도달 시 page_cap_exceeded_status 저장 + sweep break
    ↓
  needs_vision → S2 D1, fitz.Page.get_text('dict') 점수
    │  False → called_count 증가 X, sweep retry 대상 X (continue)
    ↓
  vision call → ImageParser.parse() + called_count += 1
```

두 cap 모두 `BudgetStatus` 동일 dataclass — 호출자는 `not allowed` 분기 동일 패턴.

---

## 2. hook 흐름 + 두 cap 직교 검증

### 2.1 정상 (cap 미도달)

50p PDF + cost cap 잠정값 doc $0.10 / page cap 50:
- 평균 $0.0045/p × 50p = $0.225 → **cost cap (doc) 먼저 도달** (page 22 근처)
- page cap 은 50p 까지 미도달 (called=22)
- → 기대 동작: cost cap break + `vision_budget_exceeded=true`, `vision_page_cap_exceeded` 미기재

100p PDF + cost cap doc $0.50 (운영자가 cap 상향):
- 평균 $0.0045/p × 100p = $0.45 → cost cap 통과 가능
- page cap 50 도달 → **page cap 먼저 도달** (page 50)
- → 기대 동작: page cap break + `vision_page_cap_exceeded=true`, `vision_budget_exceeded` 미기재

### 2.2 needs_vision skip 의 cap 도달 지연

5p PDF + page cap 2 + page 1,2,3 = needs_vision False, page 4,5 = True:
- page 1,2,3: needs_vision False → called_count = 0 유지, continue
- page 4: page cap 검사 (called=0 < 2) 통과 → vision call → called=1
- page 5: page cap 검사 (called=1 < 2) 통과 → vision call → called=2
- 정상 종료. cap 도달 X (called=2 == cap 후 다음 페이지 없음)

핵심 인사이트: needs_vision False 페이지가 cap 차감 X 라 **사용자 가치 있는 표·그림 페이지만 cap 50 까지 활용**. 본 PC 5 PDF 측정 35.7% skip (S2 D4-pre) 가 그대로 유지되며 cap 정확도 향상.

### 2.3 회복 토글

`JETRAG_VISION_PAGE_CAP_PER_DOC=0` 시:
- `check_doc_page_cap()` 의 첫 분기: `if page_cap <= 0: return allowed=True`
- → 모든 페이지 호출, cap 영향 0
- S2 D1 동작 (needs_vision hook) 만 작동

`JETRAG_BUDGET_GUARD_DISABLE=1` 시 (S0 D4 기존 ENV 재사용):
- cost cap + page cap 모두 즉시 allowed=True
- S2 D1 needs_vision 도 영향 X (page_needs_vision 은 자체 로직)

---

## 3. 단위 테스트 결과

```
$ uv run python -m unittest discover tests
Ran 632 tests in 16.407s
OK (skipped=1)
```

**+13 신규** (619 → 632), 회귀 0:

| 파일 | 신규 | 검증 |
|---|---:|---|
| `test_budget_guard.py` | +7 | within / at / over / zero / negative / disabled-env / scope literal |
| `test_extract_pdf_vision_enrich.py` | +4 | break_mid_sweep / needs_vision_skip / cap_zero_unlimited / default_50 |
| `test_incremental_vision.py` | +2 | break_mid_sweep / cap_zero_unlimited |
| 기존 3건 시그니처 갱신 | 0 | `_vision_pages_with_sweep` 반환 tuple 3-arity |

기존 회귀 테스트 (S0 D4/D5 cost cap 흐름, S2 D1 needs_vision hook) 영향 0 — 신규 page cap 검사가 모든 cap 통과 case 에서 noop.

---

## 4. D3 진입 prerequisite

S2 D3 (운영 모드 3개 UI 토글) 진입 전:

| Q | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| **Q-S2-1** | 운영 모드 default | **기본** (5~10p) — master plan §3 | D3 진입 시 사용자 결정 필수 |
| Q-S2-2 | 11 docs 재인제스트 시점 | S2 D5 효과 측정과 함께 1회만 | D5 진입 시 |

D3 작업 (web 변경 동반):
- 운영 모드 3개 토글 UI (`<select>`) — 기본 / 빠른 / 정밀
- 모드별 page cap 매핑: 빠른 = 10, 기본 = 50, 정밀 = 100 (또는 0=무한)
- 카드에 `vision_page_cap_exceeded` flag 표시 + 재처리 버튼
- API 응답에 page cap 사용량 (`called_pages`/`page_cap`) 노출

D2 ship 완료로 backend 인프라는 준비 — D3 는 web/API 통합 1.5일 ETA.

---

## 5. 활성 한계 갱신

| # | 항목 | 영향 | 회복 |
|---|---|---|---|
| (기존 1~7) | 핸드오프 §5 그대로 | — | — |
| (신규 8) | **page cap default 50 가 본 PC 5 PDF 분포 기반 — 11 docs 전수 측정 시 조정 필요** | doc 평균 21.5p 기준이라 100p+ 대형 PDF 이미 cap 도달 가정 | S2 D5 11 docs 측정 후 default 재산정 또는 운영자 ENV 조정 권고 |
| (신규 9) | **page cap 도달 시 sweep retry 대상 페이지도 차단** | 503 random 실패 페이지가 sweep 2 에서 회복 못 함 (cap 이 sweep 진입 자체 차단) | 정상 — cost cap 과 동일 정책. cap 풀어주려면 ENV 0 / cap 상향 |

---

## 6. 다음 후보 권고

| # | 후보 | 진입 조건 | 권고도 |
|---|---|---|---|
| 1 | **S2 D3 운영 모드 UI 토글** | Q-S2-1 사용자 결정 (운영 모드 default) | ★★★ — D2 ship 후 master plan 정합 |
| 2 | **S1.5 v3 multi-line table 휴리스틱** | 데이터센터 87.8% skip 의문 해소 | ★★ |
| 3 | **S2 D4 옵션 A 실 reingest 본격 측정** | 비용 ~$0.32 + DB chunks 갱신 → 사용자 명시 승인 | ★ |

권고: **S2 D3** (master plan 순서 정합, D2 backend 완료 → web 통합).

---

## 7. 한 문장 마감

> **S2 D2 ship — page cap + cost budget 병행 + flags 마킹**. ENV `JETRAG_VISION_PAGE_CAP_PER_DOC` (default 50) 추가, `check_doc_page_cap()` 신규 (in-memory 카운터, DB 미접근). cost cap (S0 D4) 과 직교 — 둘 중 먼저 닿는 지점 stop. needs_vision skip 페이지는 카운터 증가 X (사용자 가치 페이지만 차감, cap 도달 지연 정합). 단위 테스트 619 → **632 (+13) / 회귀 0**. 다음: **S2 D3 운영 모드 UI 토글** (Q-S2-1 사용자 결정 의존).
