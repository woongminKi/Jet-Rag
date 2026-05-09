# 2026-05-09 TOC guard 정밀화 ablation 재측정 ship (라벨 정정 후)

> Sprint: 추가 라벨 stale 정정 후속 — TOC guard 패턴 정밀화
> 작성: 2026-05-09 (라벨 정정 14 sprint 직후)
> 마감: 라벨 정정 후 상태 TOC ON 재측정 + trade-off 분석 + default OFF 유지 결정
> 입력: 추가 라벨 stale 정정 핸드오프 §5.1 의 1순위

---

## 0. 한 줄 요약

> **TOC guard 정밀화 ablation 재측정 ship — 라벨 정정 후 net 효과 거의 0 (Overall R@10 -0.001, top-1 0)**. 단 부분 trade-off: **summary top-1 +0.111, synonym_mismatch R@10 +0.125, numeric_lookup R@10 +0.032 회복** vs **table_lookup R@10 / top-1 -0.083 부분 회귀** (이전과 동일). Overall 회귀 회피지만 table_lookup 잔존 회귀 → **default OFF 유지** 권고. 패턴 정밀화 (table_lookup row 식별 + 추가 조건) 는 별도 sprint. 단위 테스트 775 OK / 회귀 0. 운영 코드 변경 0. 다음 후보 1순위 = combo c P95 안정성 재측정 또는 잔존 라벨 stale (50+ row).

---

## 1. ablation 결과 (라벨 정정 14 sprint 후 상태)

### 1.1 Overall

| metric | TOC OFF (default) | TOC ON | △ |
|---|---:|---:|---:|
| Overall R@10 | 0.7082 | 0.7074 | **-0.001 (≈0) ✅** |
| **Overall top-1** | 0.7853 | 0.7853 | **0 ✅** |
| nDCG@10 | 0.6496 | 0.6470 | -0.003 |
| MRR | 0.6096 | 0.6073 | -0.002 |

→ Overall 효과 거의 0 (회귀 회피).

### 1.2 qtype breakdown (trade-off)

| qtype | △ R@10 | △ top-1 | 비고 |
|---|---:|---:|---|
| **summary** | 0 | **+0.111 ✅** | top-1 0.7778 → 0.8889 |
| **synonym_mismatch** | **+0.125 ✅** | 0 | R@10 0.6705 → 0.7955 |
| **numeric_lookup** | +0.032 ✅ | 0 | R@10 0.6287 → 0.6604 |
| exact_fact | +0.001 | 0 | (변동 0) |
| fuzzy_memory | 0 | 0 | |
| vision_diagram | 0 | 0 | |
| **table_lookup** | **-0.083 ⚠** | **-0.083 ⚠** | R@10 0.7535 → 0.6701, top-1 0.6667 → 0.5833 |
| cross_doc | 0 | 0 | |

→ 부분 trade-off. 4 qtype 회복 vs table_lookup 회귀.

### 1.3 doc_type breakdown

| doc_type | △ R@10 | △ top-1 |
|---|---:|---:|
| pdf | -0.001 | 0 |
| 기타 | 변동 0 | |

---

## 2. 이전 ablation (라벨 정정 전) vs 현재 비교

| metric | 이전 ON | 현재 ON | △ |
|---|---:|---:|---:|
| Overall R@10 | 0.7304 | 0.7074 | -0.023 (라벨 정정 후 absolute 떨어짐) |
| Overall top-1 | 0.6687 | 0.7853 | **+0.117 ✅** |
| summary top-1 | 0.6667 | 0.8889 | +0.222 |
| synonym_mismatch R@10 | 0.6705 | 0.7955 | +0.125 |
| table_lookup R@10 | 0.6534 | 0.6701 | +0.017 |
| **△ vs OFF (Overall R@10)** | **-0.0046 ⚠** | **-0.001 ≈ 0 ✅** | net 회귀 회피 |

→ 라벨 정정 후 TOC ON 의 net 회귀가 **줄어듦** (-0.0046 → -0.001).

---

## 3. 운영 default 결정 (재논의)

### 3.1 옵션 비교

| 옵션 | 효과 | 위험 | 권고 |
|---|---|---|---|
| **A. default OFF 유지** | 현재 metric 그대로 (변동 0) | 0 | **현재 채택** |
| B. default ON | summary +0.111, synonym +0.125, numeric +0.032 / table_lookup -0.083 | table_lookup 사용자 영향 | 위험 |
| C. 패턴 정밀화 후 ON | trade-off 해소 가능 | 시간 1~2일 | 별도 sprint |

### 3.2 default OFF 유지 선택 이유

- Overall R@10 -0.001 ≈ 0 으로 net 회귀 거의 0 이지만
- **table_lookup -0.083 부분 회귀가 SLO 부적합** (특정 qtype 영향 받는 사용자에게 직접 노출)
- 운영 default 변경의 회귀 risk 크고, 정밀화 후 재검토가 안전

### 3.3 ENV opt-in 의 가치 유지

- `JETRAG_TOC_GUARD_ENABLED=true` 1회성 set → ablation 측정 가능
- 패턴 정밀화 sprint 시 baseline 비교용

---

## 4. 패턴 정밀화 후보 (별도 sprint 권고)

### 4.1 table_lookup 회귀 root cause 추정

table_lookup 12 row 중 1 row top-1 회귀 → 1/12 = -0.083.
- 현재 table_lookup top-1 hit row: 7 (TOC OFF 시 8/12)
- 회귀 row 식별 후 패턴 정밀화 가능

### 4.2 가능 fix

1. **page_idx 조건 추가** — page=1 만 cover_guard, 그 외는 TOC penalty 약하게 (0.5)
2. **chunk_idx 조건** — text 길이 + page 조합으로 정확한 목차 식별
3. **vision_caption 보유 시 penalty skip** — caption 부착 chunks 는 정답 후보라 penalty 무시

별도 sprint (cost 0, 0.5일).

---

## 5. 다음 후보 우선순위

### 5.1 1순위 — combo c P95 안정성 재측정 (cost 0, 0.25일)

**왜?**
- HF API latency 시간대별 변동성 잔존 (어제 263ms vs 71.7s)
- 다른 시간대 (조용한 시간) 측정 → 운영 default 채택 가능성
- DoD R@10 ≥ 0.75 까지 -0.042, combo c ablation 으로 +0.01~0.02 가능

### 5.2 2순위 — 잔존 라벨 stale 정정 (50+ row)

cross_doc, hwpx/hwp/docx 영향 row 잔존.

### 5.3 3순위 — TOC guard 패턴 정밀화 (cost 0, 0.5일)

table_lookup 회귀 row 식별 + 패턴 정밀화 → default ON 채택 가능성.

### 5.4 4~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 4 | chunk_filter 마킹 분석 | 0.5일 | 0 | ★★ |
| 5 | budget_guard cache hit 무료 인식 | 0.5일 | 0 | ★ |
| 6 | RPC per-doc cap | 1주+ | 0 | ★ |
| 7 | S4-B 핵심 엔티티 추출 | 3일 | 0 | ★★ |
| 8 | S5 /answer UX | 1주 | 0 | ★★ |

---

## 6. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 수정
- 0 건

### 운영 코드
- 0 건 (default OFF 유지)

### gitignored
- `evals/results/s4_a_d4_toc_on_relabeled.md` — ablation 측정

### 데이터 영향
- 0 건

---

## 7. 한 문장 마감

> **2026-05-09 — TOC guard 정밀화 ablation 재측정 ship**. 라벨 정정 후 상태 ablation: net 회귀 거의 0 (Overall R@10 -0.001, top-1 0). 단 부분 trade-off — summary top-1 +0.111, synonym_mismatch R@10 +0.125, numeric_lookup R@10 +0.032 회복 vs **table_lookup R@10/top-1 -0.083 부분 회귀**. **default OFF 유지** 권고 (table_lookup SLO 부적합). 패턴 정밀화 별도 sprint. 단위 테스트 775 OK / 회귀 0. 운영 코드 변경 0.
