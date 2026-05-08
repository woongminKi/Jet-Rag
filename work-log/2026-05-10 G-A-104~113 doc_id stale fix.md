# 2026-05-10 G-A-104~113 doc_id stale fix

> Sprint: Post-S4-A D3 Phase 2-A (핸드오프 §4 후보 #2 / Phase 2-A 후 권고 1순위)
> 작성: 2026-05-10
> 마감: ship 완료 (golden v2 정정 + 재측정 + work-log)
> 입력: D4 raw json (G-A-104~113 doc_fail 10건) + Supabase documents/chunks 직접 조회

---

## 0. 한 줄 요약

> **G-A-104~113 doc_id stale fix ship.** 진단 — golden v2 의 doc_id `629332ab-673d-49b7` 가 stale, 실제 포트폴리오 PDF doc_id 는 `9878d7bd-4766-...` (chunks 77건 / 정답 chunk_idx 10/10 모두 적재 / page NULL 0건). CSV 직접 정정 (10 row). **재측정 — doc_fail 13 → 3** (-10, 의도), **n_eval 138 → 148** (+10 evaluable, coverage ↑). Overall R@10 0.7237 → 0.7203 (-0.0034, 새 evaluable 평균 0.6733 이라 mean dilution), top-1 0.6594 → 0.6284. **caption gap 첫 양수 변환 (-0.0465 → +0.0307)** — D5 prompt v2 reingest ROI 가설 회복 신호. 단위 테스트 766 OK / 회귀 0 (CSV 정정만, 코드 변경 0).

---

## 1. 진단 (root cause)

### 1.1 G-A-104~113 row 정보

10건 모두 동일 doc_id `629332ab-673d-49b7` + expected_doc_title="포트폴리오_이한주 - na Lu":

| id | query (앞 30자) | relevant | doc_type |
|---|---|---:|---|
| G-A-104 | 제시된 시스템 기획 직무의 핵심 내용은? | 5 | pdf |
| G-A-105 | 좌절을 딛고 일어서는 비결은? | 16 | pdf |
| G-A-106 | 토스 회계 데이터 마트 개선 목적과 방법 | 23 | pdf |
| G-A-107 | SAP 수동전표 승인 시스템... | 38 | pdf |
| G-A-108 | 지마켓 데이터 이관 절차는? | 42 | pdf |
| G-A-109 | 프로젝트 진행과 후원 상태... | 53 | pdf |
| G-A-110 | 각 유저의 활동 및 음악 감상 기록 | 57 | pdf |
| G-A-111 | 회계 전표 승인 시스템의 도입 목적은? | 63 | pdf |
| G-A-112 | 작성자가 가진 업무 철학과 강점은? | 67 | pdf |
| G-A-113 | 서비스 케이스는 어떤 기준으로 분류되나요? | 71 | pdf |

D4 측정 결과: 10건 모두 `note="doc 매칭 fail"` — search 응답 items 에 `doc_id=629332ab-673d-49b7` 없음.

### 1.2 DB 직접 조회

**Supabase documents 테이블** (title ILIKE '%포트폴리오%'):
- 1건 hit: `id=9878d7bd-4766-40fa-bebb-7da45f879768`, title="포트폴리오_이한주 - na Lu", doc_type=pdf, deleted_at=NULL

→ golden v2 의 doc_id `629332ab` 와 다름. **stale doc_id**.

**Supabase chunks 테이블** (doc_id=9878d7bd):
- Total chunks: **77건**
- page NULL: **0/77** (핸드오프 §5 #6 의 "포트폴리오 chunk page DB NULL" 도 이미 해소 — 다른 시점 reingest 결과)
- chunk_idx range: 0~76
- 정답 chunk_idx 10건 (5/16/23/38/42/53/57/63/67/71) **모두 존재**

### 1.3 stale 분포 스캔

golden v2 의 13 unique doc_ids 검증:
- DB 존재: 12 / 13
- stale: **1개 (`629332ab`)** → 10 row 영향
- 다른 12개는 모두 정상

→ fix 범위 = G-A-104~113 의 doc_id 컬럼만 정정.

### 1.4 stale 발생 시나리오 (추정)

- 골든셋 v1 빌드 시점 (~2026-04월) 에 적재한 doc 의 id 가 `629332ab`
- 이후 doc 이 삭제 또는 재적재되어 새 id `9878d7bd` 부여
- build_golden_v2.py 의 자동 doc_id 채움이 v1 의 비어있는 doc_id 만 채우므로, v1 에 명시된 stale doc_id 는 그대로 유지

---

## 2. 비판적 재검토 (3회)

| 단계 | 결정 |
|---|---|
| 1차 안 | G-A-104~113 reingest (cost 동반) |
| 1차 비판 | "정말 reingest 필요?" → **불필요**. DB 에 chunks 적재됨. 단지 doc_id mismatch |
| 2차 비판 (옵션 비교) | (A) golden CSV 직접 편집 / (B) build_golden_v2.py 재실행 / (C) search() title-only fallback 추가 → **A 채택**. 최소 영향, cost 0 |
| 3차 비판 (가정) | 다른 stale doc_id 도 있을까? → 13 unique 중 1개만 stale 확인. 다른 12는 정상 |

→ 권고: **golden v2 CSV 직접 편집** (10 row 의 doc_id 컬럼만). build_golden_v2.py 와 무관, 운영 코드 변경 0.

---

## 3. 변경 사항

### 3.1 데이터 수정 (cost 0)

`evals/golden_v2.csv`:
- G-A-104~113 의 doc_id: `629332ab-673d-49b7` → `9878d7bd-4766-40fa-bebb-7da45f879768`
- 10 row 영향, 다른 컬럼 변동 0
- python csv.DictWriter 로 atomic 쓰기 (편집 전 backup 후 검증, 검증 통과 시 backup 삭제)

### 3.2 코드 변경

**0 건** — 운영 코드 / 측정 도구 / 단위 테스트 변동 없음.

### 3.3 단위 테스트 회귀

```
Ran 766 tests in ...s — OK (skipped=1)
```

CSV 정정만이라 회귀 영향 0. (build_golden_v2 단위 테스트는 mock 기반이라 실 데이터와 무관)

---

## 4. 재측정 결과

### 4.1 Overall 비교 (Phase 2-A → fix)

| metric | Phase 2-A | fix 후 | △ |
|---|---:|---:|---:|
| R@10 | 0.7237 | **0.7203** | -0.0034 |
| nDCG@10 | 0.6299 | 0.6224 | -0.0075 |
| MRR | 0.5791 | 0.5732 | -0.0059 |
| top-1 | 0.6594 | 0.6284 | -0.0310 |
| **doc_fail** | **13** | **3** | **-10** |
| **n_eval** | 138 | **148** | **+10** |

**해석**: doc_fail -10 (의도) + n_eval +10 (coverage ↑). 새 evaluable 10 row 의 R@10 평균 = **0.6733** (overall 평균 이하) → mean dilution. 이는 측정 정확도 향상의 자연 효과 (이전엔 fail 분류로 chunk-evaluable 에서 제외).

DoD 0.75 까지 -0.0297 잔여 (Phase 2-A: -0.0263).

### 4.2 G-A-104~113 cell 별 결과

| id | R@10 | top-1 | predicted top-5 |
|---|---:|:---:|---|
| G-A-104 | **1.0** | ✓ | [64, 5, 66, 14, 76] |
| G-A-105 | **1.0** | ✓ | [16, 65, 14, 11, 72] |
| G-A-106 | 0.667 | ✗ | [67, 19, 68, 17, 66] |
| G-A-107 | 0.667 | ✗ | [69, 38, 39, 66, 17] |
| G-A-108 | **1.0** | ✗ | [70, 17, 69, 66, 42] |
| G-A-109 | 0.75 | ✗ | [73, 53, 54, 55, 74] |
| G-A-110 | 0.75 | ✗ | [75, 57, 56, 71, 18] |
| G-A-111 | 0.4 | ✗ | [69, 38, 70, 39, 67] |
| G-A-112 | 0.5 | ✗ | [65, 15, 64, 11, 14] |
| G-A-113 | 0.0 | ✗ | [68, 19, 67, 24, 26] |

mean R@10 = 0.6733 / top-1 hits = 2/10.

→ search() 가 정답 chunk 를 retrieve 못 한 row 가 잔존 (G-A-113 등) — search 또는 chunk 자체 약점, 별개 이슈.

### 4.3 caption gap 첫 양수 변환 ⭐

| caption | Phase 2-A | fix 후 | △ |
|---|---:|---:|---:|
| true (R@10) | 0.7655 | **0.6931** | -0.0724 |
| false (R@10) | 0.7190 | **0.7238** | +0.0048 |
| **gap (false − true)** | -0.0465 | **+0.0307** | **+0.0772** |

**caption gap 가설 첫 양수 변환** — D5 prompt v2 reingest ROI 가설 회복 방향.

원인: G-A-107, G-A-111 (table_lookup, caption=true) 새 evaluable → R@10 0.667/0.4 → caption=true 평균 끌어내림. 표본 18 → 17 (G-A-113 등이 caption=false 라 기존 18 유지하지 않음).

**단 신중**: 표본 17 여전히 작음 + 새 evaluable 의 mean dilution 효과. D5 reingest ROI 정량화는 표본 ≥ 30 필요.

### 4.4 qtype 변화

| qtype | R@10 전 | R@10 후 | △ | 비고 |
|---|---:|---:|---:|---|
| **table_lookup** | 0.6705 | **0.6247** | **-0.0458** | G-A-107/111 evaluable, 평균 끌어내림 |
| summary | 0.6667 | 0.7037 | +0.0370 | (재계산 효과) |
| exact_fact | 0.7489 | 0.7438 | -0.0051 | G-A-104/106/108~110/112/113 추가 |
| 외 6종 | 변동 없음 | | | |

table_lookup 약화 (-0.0458) 가 새 약점 식별 — 다음 sprint 후보.

---

## 5. 다음 후보 우선순위 (재정렬)

| # | 후보 | 작업량 | 권고도 변화 | 이유 |
|---|---|---|---|---|
| 1 | **S4-A D5** prompt v2 reingest | 가변 + cost ~$0.05 | ★★ → **★★★** | caption gap 첫 양수 변환 (+0.0307) → ROI 가설 회복 |
| 2 | **table_lookup 약점 진단** | 0.5일 | 신규 ★★ | R@10 0.6247 (-0.0956 vs overall) — caption_dependent + table 본문 dense 매칭 한계 |
| 3 | **search() cross_doc retrieve 진단** | 0.5~1일 | ★★ 유지 | cross_doc R@10 0.2917 잔존 (G-U-015/032 R@10=0) |
| 4 | **Phase 2-B** cross_doc row 4 → 8~10 확장 | 0.5~1일 | ★★★ → ★★ | search() 진단 우선, 라벨 재검증 후 진입 |
| 5 | **G-A-113 R@10=0 진단** | 0.5일 | 신규 ★ | 단일 row, low ROI |
| 6 | **S4-B** 핵심 엔티티 추출 | 3일 | ★★ 유지 | |

### 권고 (비판적 재검토 후)

**1순위 = S4-A D5 prompt v2 reingest**.
- 이유: caption gap 첫 양수 변환 → D5 reingest 의 expected gain 가설 회복 신호. 단 표본 17 한계 인정 → reingest 후 동일 도구 재측정으로 확증
- 작업: 본 PC 의 11 docs reingest (옵션 A — USB/외장디스크 또는 옵션 B — iCloud). vision API cost 추정 ~$0.05 (sample-report 기반 + 다른 doc 비례)
- 사용자 명시 cost 승인 필요 ⚠️

**2순위 = table_lookup 약점 진단**.
- 이유: D4 fix 후 table_lookup R@10 0.6247 (-0.0956 vs overall) 새 식별. caption_dependent + table 본문 dense 매칭 한계 가능성
- 작업: G-U-003/U-013/A-107/A-111/A-031/A-068 의 search 응답 분석 + caption boost 효과 검증

**3순위 = search() cross_doc retrieve 진단** — Phase 2-A 잔존 G-U-015/032 R@10=0 추적.

---

## 6. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-stale-1 | 다음 sprint 1순위 | **S4-A D5 reingest** (cost ~$0.05) — caption gap 양수 변환 후 ROI 가설 회복 | 사용자 명시 진입 + cost 승인 |
| Q-stale-2 | golden v2 의 다른 stale doc_id 정기 검증 | reingest sprint 마다 자동 검증 도구 추가 | 다음 sprint 진입 시 |
| Q-stale-3 | G-A-113 R@10=0 추적 | 표본 1건이라 우선순위 낮음 | 후순위 |

---

## 7. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 수정
- `evals/golden_v2.csv` — G-A-104~113 의 doc_id 정정 (10 row, 다른 컬럼 변동 0)
- `evals/results/s4_a_d4_results.md` (gitignored) — 재측정 결과
- `evals/results/s4_a_d4_raw.json` (gitignored) — 재측정 raw

### 운영 코드
- 0 건

---

## 8. 한 문장 마감

> **2026-05-10 — G-A-104~113 doc_id stale fix ship**. 진단 — golden v2 의 doc_id `629332ab` stale, 실제 포트폴리오 doc_id 는 `9878d7bd-...` (chunks 77건 / 정답 10/10 적재). CSV 정정 (10 row). 재측정 — **doc_fail 13 → 3** / **n_eval 138 → 148** (coverage ↑) / Overall R@10 0.7237 → 0.7203 (mean dilution). **caption gap 첫 양수 변환** (-0.0465 → +0.0307) — D5 reingest ROI 가설 회복 신호. **table_lookup 약점 신규 식별** (R@10 0.6247). 단위 테스트 766 OK / 회귀 0 / 운영 코드 변경 0. 다음 후보 1순위 = S4-A D5 reingest (사용자 cost 승인 필요).
