# 2026-05-09 S4-A D5 본격 reingest ship + D2 metadata 전파 한계 발견

> Sprint: S4-A D5 (Master plan §6) — **본격 진입 + 부분 ROI 검증 + D2 한계 신규 발견**
> 작성: 2026-05-09 (어제 D5 시범 차단 학습 다음 sprint)
> 마감: sample-report 1 doc reingest (v2 prompt 18 page) + 측정 비교 + 진단
> 입력: 어제 work-log §3 (가설 reframe) + 핸드오프 §5.1 (1순위 권고)

---

## 0. 한 줄 요약

> **S4-A D5 본격 reingest ship — sample-report v2 prompt 18 page 신규 호출 + cost $0.1113→$0.3487 (+$0.2374). caption_dependent=true R@10 0.6796 → 0.6898 (+0.0102), caption gap +0.0346 → +0.0247 (28% 회수)**, table_lookup top-1 0.1667 → 0.3333 (+0.167), summary R@10 +0.019. 단 **chunks 의 caption metadata 부착 = 1건만** (D2 메커니즘의 OCR section 미부착 한계 신규 발견 — caption section 만 metadata 부착, OCR + action_items section 은 미부착). 단위 테스트 766 OK / 회귀 0. ENV `JETRAG_DOC_BUDGET_USD=0.30` 1회성 상향, reingest 후 unset (운영 코드 변경 0). 다음 후보 1순위 = **D2 OCR section caption metadata 보강** (1 chunk → 18+ chunks 가능, ROI 4~5배 가능, cost 0).

---

## 1. 진입 결정 + 비판적 재검토

### 1.1 진입 조건 점검

핸드오프 §5.1 의 "24h cap 회복" 가정이 부정확함을 진입 전 발견:

- `_sum_doc_cost(doc_id)` 는 **all-time SUM** (success=true row 전체) — 24h sliding 아님 (`api/app/services/budget_guard.py:362`)
- sample-report 의 `vision_usage_log` SUM = $0.1113 > cap $0.10 → 시간 경과로 회복 불가
- 즉 어제 D5 시범 차단의 cap 발동은 **영구 누적**

→ 진입 옵션 3개 비교:

| 옵션 | 방법 | 위험 | 권고 |
|---|---|---|---|
| A | `JETRAG_DOC_BUDGET_USD=0.30` ENV 상향 | 낮음 (scope 한정, 운영 코드 0) | ★★★ |
| B | `JETRAG_BUDGET_GUARD_DISABLE=1` | 중 (daily/24h cap 도 OFF) | ★ |
| C | `vision_usage_log` row 삭제 | 높음 (data 정합성 손상) | ✗ |

옵션 A 채택 (사용자 명시 승인). reingest 후 ENV unset 까지 1 sprint 으로 ship.

### 1.2 비판적 재검토 (3회)

| 단계 | 결정 |
|---|---|
| 1차 안 | 옵션 A 즉시 진입 |
| 1차 비판 | "ENV cap 상향이 우회 행위 아닌가?" → **운영 default 변경 X**, env scope, work-log 명시 |
| 2차 비판 | "page cap 50p 안 18 page = 정상 vision call 범위" → 정량 진입 |
| 3차 비판 | "어제 cost 0.1113 vs 오늘 추가 0.24 = 2배. ROI 가설 차단 risk" → **page cap 50 도달 시 stop, 사실상 동일 page 범위 v2 재호출** |

→ 권고: 옵션 A + page cap 50 (기존) + cost cap 0.30 (1회성 상향) + reingest 후 ENV unset.

---

## 2. 실 reingest 결과

### 2.1 환경

```bash
JETRAG_DOC_BUDGET_USD=0.30 \
DEFAULT_USER_ID='00000000-0000-0000-0000-000000000001' \
uv run python /tmp/reingest_sample_report.py
```

### 2.2 실행 로그 (핵심)

```
pre-reingest chunks: 898  (어제 D5 시도 후)
pre-reingest vision_page_cache: 8 (v1=8)

vision_enrich: 93페이지 중 첫 50페이지만 처리 (paid tier RPM/latency 보호)
vision_enrich: budget cap 도달 — $0.3487 > $0.3000 (남은 페이지 50 skip)
chunk_filter: 마킹 비율 48.0% > 5% — false positive risk 검토 필요
pipeline elapsed: 612.8s — OK

post-reingest chunks: 931  (898 → 931, +33)
post-reingest vision_page_cache: 26 (v1=8, v2=18)
sample-report vision_usage_log SUM: $0.3487 (26 rows)
```

### 2.3 캐시 분포 (sha=b35f5b1955)

| version | rows | pages |
|---|---:|---|
| v1 | 8 | 51, 52, 53, 55, 56, 57, 59, 60 (어제 D5 phase 1) |
| v2 | 18 | 1~11, 13~18, 20 (오늘 D5 본격) |

→ v2 는 첫 50p 안 needs_vision=true 18 page. 51p 이상은 v1 그대로 유지 (재호출 안 함, page cap 50 도달).

### 2.4 cost

- D5 phase 1 (어제): $0.1113
- D5 본격 (오늘): +$0.2374 (v2 18 page 평균 $0.0132/page)
- 총 누적: $0.3487 (cap $0.30 도달, 다음 진입 차단)

---

## 3. D2 metadata 전파 한계 — 신규 발견

### 3.1 caption metadata 부착 chunks = **1건** (931 chunks 중)

```
sample-report chunks total: 931
  table_caption: 1
  figure_caption: 1
  any caption: 1
  chunk_idx=909 page=None metadata={table_caption, figure_caption, overlap_with_prev_chunk_idx}
```

### 3.2 v2 cache 의 caption field 채움 (정상)

```
v2 cache rows: 18
  with table_caption: 11  (page 3, 5, 7, 8, 9, ...)
  with figure_caption: 6  (page 8, 9, ...)
  result keys: ['type', 'caption', 'ocr_text', 'structured', 'table_caption', 'figure_caption']
```

→ vision API 응답 자체는 정상. caption 추출 OK.

### 3.3 root cause — ImageParser._compose_result 의 한계

`api/app/adapters/impl/image_parser.py:165-219` — **caption section 1개 에만** caption_metadata 부착:

```python
sections = []
sections.append(ExtractedSection(
    text=f"[{caption.type}] {caption.caption}",
    section_title=f"이미지 분류: {caption.type}",
    metadata=caption_metadata,  # ← 여기만 table_caption/figure_caption
))
ocr_clean = caption.ocr_text.strip()
if ocr_clean:
    sections.append(ExtractedSection(
        text=ocr_clean,
        section_title="OCR 텍스트",
        # ← metadata 인자 없음 → caption metadata 부재
    ))
```

→ **OCR section + action_items section 의 chunks 는 caption metadata 부착 X**. sample-report 의 경우 vision-derived sections 대부분이 OCR section (OCR 텍스트 풍부) 라 caption metadata 가 chunks 에 거의 도달 못함.

이론상 caption 부착 chunk 수:
- 18 vision call page × (caption section 1 + OCR section 1) = 36 sections
- 하지만 각 section 의 chunking 후 caption metadata 가 caption section 의 chunks (대부분 1 chunk) 에만 부착
- 실제 1 chunk = caption section 중 chunk text 합성 + metadata 둘 다 부착된 경우

### 3.4 D2 ship 시 의도

D2 (`2026-05-09 S4-A D2 metadata 전파 + 검색 path 합성.md`) 는 caption 추출 단위 (caption section) 에 한정해서 metadata 부착 의도. 단 OCR section + action_items section 도 같은 vision page 에 속하므로 동일 page 의 caption metadata 를 공유해야 retrieval 효과 극대화.

→ **D2 OCR section + action_items section 에도 caption metadata 부착 보강** 이 다음 sprint 1순위 후보로 부상.

---

## 4. 측정 비교 (RRF-only baseline, golden v2 157 row)

### 4.1 Overall

| 시점 | n_eval | R@10 | top-1 | P95 lat |
|---|---:|---:|---:|---:|
| D5 시도 후 | 148 | 0.7103 | 0.6351 | (기록 X) |
| **D5 본격 후** | **148** | **0.7116** | 0.6284 | 550.6ms |
| △ | 0 | **+0.0013** | -0.0067 | — |

→ Overall R@10 미세 양성. top-1 미세 회귀.

### 4.2 caption_dependent gap (D5 의 직접 KPI)

| 시점 | true R@10 | false R@10 | gap (false − true) |
|---|---:|---:|---:|
| D5 시도 후 | 0.6796 | 0.7142 | +0.0346 |
| **D5 본격 후** | **0.6898** | **0.7145** | **+0.0247** |
| △ | **+0.0102** ✅ | +0.0003 | **gap 축소 -0.0099** |

→ **caption gap 축소 28% 회수**. expected gain ceiling 의 일부 확인 — D5 ROI 가설 부분 검증.

### 4.3 qtype breakdown (변화 큰 항목)

| qtype | n_eval | D5 시도 R@10 | D5 본격 R@10 | △ R@10 | △ top-1 |
|---|---:|---:|---:|---:|---:|
| **table_lookup** | 6 | 0.6247 | 0.6096 | -0.015 (회귀) | **+0.1667 ⭐** (1/6 → 2/6 hit) |
| **summary** | 9 | 0.6481 | **0.6667** | +0.0186 ✅ | -0.111 |
| exact_fact | 112 | 0.7435 | 0.7443 | +0.0008 | -0.0089 |
| numeric_lookup | 7 | 0.5499 | 0.5295 | -0.0204 | 0 |
| cross_doc | 4 | 0.2917 | 0.2917 | 0 | 0 |
| vision_diagram | 2 | 0.8333 | 0.8333 | 0 | 0 |
| fuzzy_memory | 4 | 0.7083 | 0.7083 | 0 | 0 |
| synonym_mismatch | 4 | 0.7330 | 0.7330 | 0 | 0 |

→ table_lookup top-1 +0.167 가 가장 큰 win. `[표: ...]` chunk text 합성이 dense + lex 매칭 회복 신호. summary R@10 +0.019 도 chunk text 합성 효과로 추정.

### 4.4 doc_type breakdown

| doc_type | D5 시도 R@10 | D5 본격 R@10 | △ |
|---|---:|---:|---:|
| pdf | 0.6764 | 0.6797 | **+0.0033** ✅ |
| hwpx | 0.7768 | 0.7695 | -0.0073 |
| docx | 0.6436 | 0.6436 | 0 |
| hwp | 0.8283 | 0.8366 | +0.0083 |
| pptx | 1.0000 | 1.0000 | 0 |

→ pdf + hwp 회복 (sample-report 영향). hwpx 미세 회귀 (chunk_filter 마킹 부수 효과 가능).

---

## 5. ROI 가설 검증 결과

### 5.1 가설 — D5 v2 prompt reingest 가 caption_dependent=true R@10 향상

**검증**: 부분 양성.

- caption=true R@10 +0.0102 (직접 효과)
- caption gap 28% 회수
- table_lookup top-1 +0.167 (간접 효과)
- 단 caption 부착 chunks = 1건만 (D2 OCR section 미부착 한계)

### 5.2 expected gain 회수율

D4 baseline 의 caption gap = +0.0346 (false − true). D5 본격 후 gap = +0.0247.

→ 회수율 = (0.0346 − 0.0247) / 0.0346 = **28.6%**.

이 28.6% 가 chunks 의 1 caption 부착으로만 달성됐다는 점이 중요. 만약 D2 OCR section 보강 시 OCR section chunks 도 caption metadata 보유 → text 합성 + lex 매칭 효과 4~5배 가능.

### 5.3 cost ROI

- cost: +$0.2374 (v2 18 page)
- gain: caption gap 0.0099 축소
- 단위 ROI: $24/0.001 R@10

→ cost 만 보면 ROI 낮음. 단 D2 OCR section 보강 시 동일 v2 cache 재사용 (cost 0 추가) → ROI 4~5배 가능. **D5 본격 진입의 잔존 가치 = D2 보강 시 cost 0으로 추가 회수 가능**.

---

## 6. 다음 후보 우선순위 (D5 본격 후 재정렬)

### 6.1 신규 1순위 — D2 OCR section caption metadata 보강 (cost 0)

**왜?**
- caption metadata 가 caption section 1 chunk 만 부착 (931 중 1건)
- OCR section + action_items section 의 chunks 도 같은 page caption 보유 시 text 합성 + lex 매칭 효과 4~5배 가능
- v2 cache 재사용 → cost 0 추가
- expected gain ceiling = caption gap 0.0247 → 0 (full 회수) + table_lookup R@10 회복

**작업**:
- `api/app/adapters/impl/image_parser.py` `_compose_result` — OCR section / action_items section 에도 `caption_metadata` 인자 전달
- `api/app/ingest/incremental.py` 동일
- 단위 테스트 보강 (caption section 외에도 metadata 부착 검증)
- sample-report 재 reingest 또는 chunks 재구성 (cost 0)
- D4 도구 재실행

### 6.2 2순위 — table_lookup R@10 회귀 진단 (cost 0)

**왜?** D5 본격 후 R@10 -0.015 (top-1 +0.167 와 정반대). chunk_filter 48% 마킹 false positive 가능. chunks 1061 → 931 (-130) 의 retrieve 영향 분리 필요.

### 6.3 3~10순위

| # | 후보 | 작업량 | 권고도 |
|---|---|---|---|
| 3 | summary fuzzy_memory 회귀 (-0.111/0) row 확장 | 0.5~1일 | ★★ |
| 4 | search() cross_doc retrieve 진단 | 0.5~1일 | ★★ |
| 5 | reranker 조건부 비활성 | 1일 | ★★ |
| 6 | chunk_filter 48% 마킹 분석 | 0.5일 | ★★ |
| 7 | D5 phase 2 (page cap 51p+ 영역 reingest) | cost 가변 | ★ |
| 8 | 운영 default combo c 채택 | 0.25일 | ★★ |
| 9 | S4-B 핵심 엔티티 추출 | 3일 | ★★ |
| 10 | S5 /answer UX | 1주 | ★★ |

---

## 7. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-D2-OCR-1 | D2 OCR section caption metadata 보강 진입 | cost 0, 운영 코드 변경 + 단위 테스트 보강 | 다음 sprint |
| Q-D5-resume | D5 phase 2 (51p+ 영역 reingest) 진입 시점 | D2 OCR 보강 후 평가, 추가 cost ~$0.30 | 후순위 |
| Q-cap-policy | `JETRAG_DOC_BUDGET_USD` 운영 default 검토 | $0.10 그대로 유지 (시범 한정 0.30) | 차후 |
| Q-D6-1 | 운영 default combo (c) 채택 | combo c 확정 | 사용자 명시 |
| (어제 잔존) | Q-D5-trial-2 / Q-Phase2-B / 기타 | 별도 sprint | |

---

## 8. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 수정
- `evals/results/s4_a_d4_results.md` — D5 본격 후 측정 갱신 (gitignored)
- `evals/results/s4_a_d4_raw.json` — raw (gitignored)
- `/tmp/reingest_sample_report.py` — DEFAULT_USER_ID UUID + cost 컬럼 정정 (gitignored 일회성)

### 운영 코드
- **0 건** — 모든 변경이 ENV 토글 + 시범 스크립트 + work-log

### 데이터 영향
- sample-report chunks 898 → 931 (+33, chunk_filter 48% 마킹 후)
- vision_page_cache: v1=8 그대로, **v2=18 신규** (page 1~11/13~18/20)
- vision_usage_log: 8 rows → 26 rows (+18), SUM $0.1113 → $0.3487
- ENV `JETRAG_DOC_BUDGET_USD` — 1회성 상향 후 unset (이번 세션 한정)

---

## 9. 한 문장 마감

> **2026-05-09 — S4-A D5 본격 reingest ship**. sample-report v2 prompt 18 page 신규 호출 (cost +$0.2374, $0.1113 → $0.3487). caption=true R@10 0.6796 → 0.6898 (+0.0102), caption gap 28% 회수, table_lookup top-1 +0.167, summary R@10 +0.019. 단 caption metadata 부착 chunks=1건만 — **D2 ImageParser._compose_result 의 OCR section 미부착 한계 신규 발견**. 단위 테스트 766 OK / 회귀 0. ENV `JETRAG_DOC_BUDGET_USD=0.30` 1회성 상향, 운영 코드 변경 0. 다음 후보 1순위 = **D2 OCR section caption metadata 보강** (cost 0, ROI 4~5배 가능).
