# 2026-05-09 multi-doc cross_doc retrieve 진단 ship

> Sprint: combo c 측정 핸드오프 §5.1 1순위 — multi-doc cross_doc retrieve 진단
> 작성: 2026-05-09 (combo c P95 root cause 정정 ship 직후)
> 마감: 7 multi-doc U-row 의 search 응답 추적 + 4 양상 root cause 분리
> 입력: combo c sub-report (cross-doc R@10 0.0833 / top-1 0.0000 잔존)

---

## 0. 한 줄 요약

> **multi-doc cross_doc retrieve 진단 ship — 7 multi-doc U-row 의 search 응답 추적**. 4가지 root cause 양상 분리: (1) **doc-size bias** — 큰 doc (sample-report 994, 데이터센터 443) 의 chunks 가 RRF top 우선, 작은 doc (law sample 28~30, 한마음 56) 정답 top-50 밖 (G-U-018/027/015 한마음), (2) **정답 doc 매칭 + 정답 chunk 미매칭** (G-U-015 직제/G-U-017 law3/G-U-031/G-U-032), (3) 정상 작동 case 1건 (G-U-026 R@10=1.0 가능), (4) **라벨 부정확 의심** (chunk_idx 가 reingest 후 변동 가능). `_pick_target_items` multi-doc helper 자체는 정상. 단위 테스트 775 OK / 회귀 0. 운영 코드 변경 0 (read-only 진단). 다음 후보 1순위 = **acceptable_chunks 확장 + 라벨 재검증** (cost 0, R@10 +0.10~0.15 추정).

---

## 1. 진단 대상 (7 multi-doc U-row)

| id | qtype | expected docs | relv chunks |
|---|---|---|---|
| G-U-015 | cross_doc | 한마음 + 직제 | 15, 0 |
| G-U-017 | fuzzy_memory | law2 + law3 | 6, 3 |
| G-U-018 | fuzzy_memory | law2 + law3 | 16, 27 |
| G-U-026 | summary | 템플릿1 + 3 | 101, 112 |
| G-U-027 | exact_fact | 템플릿1 + 3 | 43, 112 |
| G-U-031 | cross_doc | sonata + 데이터센터 | 129, 397 |
| G-U-032 | cross_doc | 보건의료 + 데이터센터 | 10, 441 |

각 row 에 대해 `app.routers.search.search()` 직접 호출 (RRF-only, hybrid mode) → response items 의 doc_title + matched_chunks 분포 추적.

---

## 2. 진단 결과 (4 양상 분리)

### 2.1 Pattern 1 — 정답 doc 자체가 search top-50 밖

| row | 매칭된 expected docs | top-3 응답 | 진단 |
|---|---|---|---|
| **G-U-018** | 0/2 (둘 다 top-50 밖) | 포트폴리오 / sample-report / 데이터센터 | dense+sparse 매칭이 큰 doc 우선 |
| **G-U-027** | 0/2 | 데이터센터 / 직제 / 포트폴리오 | 같은 doc-size bias |
| G-U-015 | 1/2 (한마음 미매칭) | 승인글3 / 보건의료 / 직제 | 한마음 doc (chunks 56) 작아서 밀림 |

→ **doc-size bias 명확**. 큰 doc (sample-report 994, 데이터센터 443, 보건의료 175) 의 chunks 가 RRF score 상위. 작은 doc (law sample 28~30, 한마음 56, 템플릿 113~139) 의 chunks 는 동일 query 대해 더 적은 매칭 score → top-50 진입 fail.

### 2.2 Pattern 2 — 정답 doc 매칭 + 정답 chunk 미매칭

| row | 매칭 doc | top-5 chunks | 정답 (relv) | 라벨 hit |
|---|---|---|---|---|
| G-U-015 직제 | 응답 #3 | [95, 101, 106] | 0, 15 | 0/2 |
| G-U-017 law3 | 응답 #5 | [9, 21, 27] | 3 | 0/1 (law3 만) |
| G-U-031 sonata | 응답 #2 | [64, 113, 133] | 129 | 0/1 |
| G-U-032 | 응답 #1, #2 | [151..173], [385..407] | 10, 441 | 0/2 |

→ doc 은 retrieve 되지만 라벨된 chunk_idx 가 top-50 안 못 듬. 가능성:
1. **라벨 부정확** — query intent 와 정답 chunk 의 의미 매칭이 약함
2. **chunk_idx 변동** — sample-report reingest 후 chunk_idx 변경 (G-A-104~113 의 stale 라벨 와 같은 패턴)
3. **acceptable_chunks 부재** — 단일 chunk 라벨이 너무 엄격, 인접 chunks 가 acceptable 후보

### 2.3 Pattern 3 — 정상 작동 (R@10 1.0 가능)

| row | 매칭 doc | top-5 chunks | 정답 hit |
|---|---|---|---|
| **G-U-026** | 템플릿1 응답 #6, 템플릿3 응답 #3 | 템플릿1 [66,75,**101**], 템플릿3 [35,49,**112**] | **2/2 ✅** |

→ multi-doc helper (`_pick_target_items` Phase 2-A) 가 정확히 작동. 정답 doc + chunk 모두 매칭 → R@10=1.0 가능.

### 2.4 Pattern 4 — 라벨 정확도 의심

- **G-U-031 sonata chunk 129** — sonata chunks=134, chunk 129 이 query "안전" 의미와 약한 매칭. 정답 chunk 재검증 필요
- **G-U-015 chunk 0, 15** — chunk 0 은 보통 doc 시작/표지. "위원회" 의미와 약한 매칭. 라벨 부정확 가능
- **chunk_idx 재라벨링 권고**: 측정 도구 등으로 chunks text 보고 query intent 와 일치하는 chunk_idx 정정

---

## 3. Root cause 종합

### 3.1 doc-size bias (Pattern 1 의 root cause)

**Why**:
- RRF score = 1/(k + rank_dense) + 1/(k + rank_sparse)
- 큰 doc 은 chunks 수가 많아 다양한 매칭 후보 → 일부 chunks 가 query 키워드 일치 시 RRF top 진입
- 작은 doc 은 chunks 수 적어 매칭 후보 좁음 → 동일 query 대해 score 더 낮음

**측정**:
- sample-report 994 chunks, 데이터센터 443 chunks → top-50 점유율 높음
- law sample 28~30 chunks, 한마음 56 chunks → top-50 진입률 낮음

**Fix 후보** (별도 sprint):
- **per-doc cap retrieval** — 각 doc 별 top-K 균등 retrieve 후 RRF 합산 (multi-doc query 한정)
- **query intent detection + doc filter** — query 안 doc 키워드 ("law sample", "한마음", "직제") 인식 시 해당 doc 만 search

### 3.2 라벨 부정확 (Pattern 2 + 4 의 일부 root cause)

**Why**:
- multi-doc U-row 의 라벨링 시 정답 chunk 가 query intent 와 약한 의미 매칭
- reingest 후 chunk_idx 변동 (sample-report 894 → 994)
- acceptable_chunks 부재 → 단일 정답 너무 엄격

**Fix 후보** (cost 0):
- **라벨 재검증** — 7 row 의 정답 chunk 텍스트 직접 확인 후 정정
- **acceptable_chunks 확장** — 인접 chunks (±1) 또는 의미 매칭 chunks 추가

### 3.3 measurement helper 정상 (Pattern 3 검증)

`_pick_target_items` Phase 2-A 의 multi-doc helper:
- sub_titles 별 첫 매칭 item 합산
- search 응답 안 expected doc 이 있으면 정확히 식별
- G-U-026 R@10=1.0 가능 (정답 chunks 모두 매칭)

→ 측정 도구 자체 정상. cross-doc R@10 0.0833 잔존은 **search 회귀 (Pattern 1+2)** 와 **라벨 부정확** 합산 효과.

---

## 4. ROI 가설 추정

### 4.1 acceptable_chunks 확장 + 라벨 재검증 (cost 0, ~0.5일)

**예상 효과**:
- Pattern 2 + 4 의 4 row 를 R@10 ≥ 0.5 로 회복 가능
- cross-doc sub-report R@10 0.0833 → ~0.20~0.30
- Overall R@10 +0.005~0.010 (cross-doc 4 row × 0.3~0.5 / 165 n_eval)

### 4.2 doc-size bias fix (별도 sprint, 운영 코드 변경)

**예상 효과**:
- Pattern 1 의 3 row 를 R@10 ≥ 0.3 로 회복 가능
- cross-doc R@10 +0.20 가능
- 단 운영 코드 변경 + 다른 single-doc query 회귀 risk

---

## 5. 다음 후보 우선순위

### 5.1 1순위 — acceptable_chunks 확장 + 라벨 재검증 (cost 0, 0.5일)

**작업**:
1. G-U-015/017/018/031/032 의 정답 doc chunks 직접 확인 (현재 chunks text 기반)
2. query intent 와 일치하는 chunks 의 chunk_idx 식별
3. acceptable_chunks 컬럼에 추가 (relevant 는 그대로 유지, acceptable 만 확장)
4. D4 도구 재실행 → cross-doc R@10 회복 검증

### 5.2 2순위 — doc-size bias 완화 (운영 코드 변경, 1~2일)

multi-doc query detector + per-doc top-K cap retrieval. 별도 sprint, 회귀 risk.

### 5.3 3~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 3 | 12 docs v2 prompt reingest | 가변 | $0.5~1.5 | ★★ |
| 4 | combo c P95 안정성 재측정 (HF cap 회복 후 다른 시간대) | 0.25일 | 0 | ★★ |
| 5 | numeric_lookup R@10 0.5295 진단 | 0.5일 | 0 | ★★ |
| 6 | fuzzy_memory top-1 -0.25 진단 | 0.5일 | 0 | ★ |
| 7 | chunk_filter 45.5% 마킹 분석 | 0.5일 | 0 | ★★ |
| 8 | budget_guard cache hit 무료 인식 | 0.5일 | 0 | ★ |
| 9 | S4-B 핵심 엔티티 추출 | 3일 | 0 | ★★ |
| 10 | S5 /answer UX | 1주 | 0 | ★★ |

---

## 6. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-cross-label | acceptable_chunks 확장 + 라벨 재검증 | cost 0, 0.5일 — 즉시 진입 | 다음 sprint |
| Q-doc-bias | doc-size bias 완화 fix | 운영 코드 변경, 회귀 risk | 라벨 fix 후 결정 |
| Q-other-docs | 12 docs v2 prompt reingest | 사용자 명시 cost 승인 후 | 후순위 |
| (이전 잔존) | 별도 sprint | | |

---

## 7. 핵심 변경 파일 목록

### 신규
- 본 work-log
- `/tmp/diagnose_multi_doc.py` — 진단 스크립트 (gitignored, 일회성)

### 수정
- 0 건

### 운영 코드
- 0 건

### 데이터 영향
- 0 건 (read-only 진단)

---

## 8. 한 문장 마감

> **2026-05-09 — multi-doc cross_doc retrieve 진단 ship**. 7 multi-doc U-row 의 search 응답 추적으로 4 root cause 양상 분리: (1) **doc-size bias** (큰 doc 의 chunks 가 RRF top 우선, 작은 doc 정답 top-50 밖), (2) 정답 doc 매칭 + 정답 chunk 미매칭 (라벨 부정확 또는 chunk_idx 변동), (3) 정상 작동 case 1건 (G-U-026 R@10=1.0 가능), (4) 라벨 정확도 의심. `_pick_target_items` multi-doc helper 자체는 정상. 단위 테스트 775 OK / 회귀 0. 운영 코드 변경 0. 다음 후보 1순위 = **acceptable_chunks 확장 + 라벨 재검증** (cost 0, 0.5일, R@10 +0.005~0.010 예상).
