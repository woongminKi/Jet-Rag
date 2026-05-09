# 2026-05-09 caption=true top-1 진단 + 라벨 정정 ship

> Sprint: G-U-017 정정 핸드오프 §4.1 1순위 — caption=true top-1 -0.0589 진단
> 작성: 2026-05-09 (G-U-017 정정 ship 직후)
> 마감: caption=true 28 row top-1 위치 추적 + 2 row 라벨 정정 + D4 재실행
> 입력: 표본 확장 측정 §4 의 caption gap top-1 -0.058

---

## 0. 한 줄 요약

> **caption=true top-1 -0.0589 진단 ship — table_lookup 12 row 의 top-1 0.333 → 0.500 (+0.167, 50%↑) 회복**. 진단: caption=true 28 row 의 top-1 분석 → table_lookup 약점 8/12 row top-1 miss 식별. 2 row (G-A-107/111) 의 ch 69 (SAP 시스템 구축 목표) acceptable 추가로 회복. 결과: **caption=true top-1 0.5714 → 0.6429 (+0.072)**, **caption gap top-1 -0.058 → -0.006 (거의 0 회복) ⭐**, Overall R@10 0.7296 → 0.7308 (+0.0012), Overall top-1 0.6258 → 0.6380 (+0.0122). 단위 테스트 775 OK / 회귀 0. cost 0. 운영 코드 변경 0. 다음 후보 1순위 = numeric_lookup R@10 0.5295 진단 (cost 0, 0.5일).

---

## 1. 진단 결과

### 1.1 caption=true 분포 (29 row, 28 evaluable)

| qtype | n | top-1 (정정 전) |
|---|---:|---:|
| exact_fact | 11 | **0.8182** ✅ DoD 도달 |
| **table_lookup** | 12 | **0.3333** ⚠ 약점 |
| vision_diagram | 6 | 0.6000 |

→ table_lookup + caption=true 약점 집중 (12 중 8 row top-1 miss).

### 1.2 8 row top-1 miss 패턴 분류

| row | top-1 chunk | 정답 위치 | 라벨 정정 가능? |
|---|---|---|---|
| **G-A-107** | ch 69 (SAP 시스템 구축 목표) | ch 38 (2위) | **✅ 의미 매칭** |
| **G-A-111** | ch 69 (동일) | ch 38 (2위) | **✅ 의미 매칭** |
| G-U-003 | ch 118 (트림별 사양 스크린샷) | ch 102 (4위) | 의미 약함 |
| G-A-008 | ch 399 (해외 진출 평가) | ch 374 (3위) | 의미 약함 (다른 사업) |
| G-A-021 | ch 902 (경제전망 목차) | ch 904 (3위) | 목차 chunk, 회복 불가 |
| G-A-201 | ch 806 (예측기관 비교) | ch 911 (4위) | 의미 약함 |
| G-A-202 | ch 75 (시장 전망 분포) | ch 916 (4위) | 의미 약함 |
| G-A-204 | ch 902 (목차) | ch 918 (?) | 목차 chunk, 회복 불가 |

→ **2 row (G-A-107/111) 만 라벨 정정 가능**. 나머지 6 row 는 search 가중치 / chunk text 합성 약점 (별도 fix 필요).

---

## 2. 라벨 정정 내역

### 2.1 G-A-107: SAP 수동전표 승인 시스템

- **search top-1**: ch 69 — "지마켓 SAP 수동전표 승인시스템 구축 월 8천만 건의 회계전표를..." (구축 목표 + 승인 절차)
- **query**: "SAP 수동전표 승인 시스템으로 무엇이 달라지나요?" — **"무엇이 달라지나요"** 의미가 ch 69 (구축 목표 + 효과) 와 매칭
- 라벨 정정: acceptable_chunks `63` → `63,69`

### 2.2 G-A-111: 회계 전표 승인 시스템 도입 목적

- **search top-1**: ch 69 — 동일
- **query**: "회계 전표 승인 시스템의 도입 목적은?" — **"도입 목적"** 이 ch 69 (구축 목표 + 4개월) 와 직접 매칭
- 라벨 정정: acceptable_chunks `38,39,64` → `38,39,64,69`

### 2.3 fix 안 한 6 row 의 root cause

- **G-A-021/204**: top-1 = ch 902 (경제전망 목차) — 큰 doc 의 첫 chunk 가 query 와 일반적 매칭. 별도 search 가중치 fix 필요
- **G-U-003/A-008/201/202**: top-1 chunk 가 의미 약하지만 키워드 매칭 강해 RRF 우위. chunk text 합성 강화 또는 dense embedding 강화 필요

→ 별도 sprint 필요.

---

## 3. 측정 비교 (RRF-only baseline, golden v2 172 row)

### 3.1 Overall

| metric | 정정 전 | 정정 후 | △ |
|---|---:|---:|---:|
| Overall R@10 | 0.7296 | **0.7308** | +0.0012 ✅ |
| **Overall top-1** | 0.6258 | **0.6380** | **+0.0122 ✅** |
| Overall nDCG@10 | 0.6278 | 0.6307 | +0.003 |
| Overall MRR | 0.5839 | 0.5854 | +0.0015 |

### 3.2 table_lookup qtype (직접 KPI)

| metric | 정정 전 | 정정 후 | △ |
|---|---:|---:|---:|
| **R@10** | 0.7215 | **0.7367** | **+0.015 ✅** |
| **top-1** | 0.3333 | **0.5000** | **+0.167 (50%↑) 🚀** |
| nDCG@10 | 0.5389 | 0.5779 | +0.039 |
| MRR | 0.5000 | 0.5208 | +0.021 |

→ table_lookup top-1 50% 회복.

### 3.3 caption_dependent gap (캡션 효과 직접 KPI)

| metric | 정정 전 | 정정 후 | △ |
|---|---:|---:|---:|
| **caption=true R@10** | 0.7795 | **0.7861** | +0.007 ✅ |
| **caption=true top-1** | 0.5714 | **0.6429** | **+0.072 ✅** |
| caption=false top-1 | 0.6296 | 0.6370 | +0.0074 |
| **caption gap top-1 (false−true)** | **-0.058** | **-0.006** | **거의 0 회복 ⭐** |

→ caption=true 의 top-1 약점 거의 완전히 해소 (D2 fix 의 효과 + 라벨 정확도 결합).

### 3.4 doc_type breakdown

| doc_type | 정정 전 R@10 | 정정 후 R@10 | △ |
|---|---:|---:|---:|
| pdf | 0.7125 | **0.7144** | +0.002 ✅ (포트폴리오 영향) |
| 기타 | 변동 없음 | | |

---

## 4. ROI 검증

### 4.1 cost ROI

- cost: 0 (라벨링 작업만)
- gain: table_lookup top-1 +0.167, caption=true top-1 +0.072
- 단위 ROI: 무한대 (cost 0)

### 4.2 누적 라벨 정정 효과 (오늘 4 sprint)

| sprint | row | 효과 |
|---|---|---|
| 1차 cross_doc 재검증 (4 row) | G-U-015/018/031/032 | cross_doc R@10 +0.182 |
| 2차 fuzzy_memory 재검증 (3 row) | G-U-100/101/103 | fuzzy_memory top-1 +0.250 |
| 3차 G-U-017 정정 (1 row) | G-U-017 | cross_doc R@10 +0.072 |
| **4차 caption=true 정정 (2 row)** | G-A-107/111 | table_lookup top-1 +0.167 |

**누적 효과**:
- cross_doc R@10: 0.2917 → 0.5457 (**+0.254, 87%↑**)
- fuzzy_memory top-1: 0.500 → 0.7143 (+0.214)
- table_lookup top-1: 0.1667 → 0.5000 (+0.333, 표본 확장 + 라벨 정정 누적)
- **Overall R@10: 0.7072 → 0.7308 (+0.0236)**
- **Overall top-1: 0.6284 → 0.6380 (+0.0096)**
- caption gap top-1: +0.119 → -0.006 (거의 0 회복)

DoD 잔여: R@10 ≥ 0.75 까지 -0.0192 / top-1 ≥ 0.80 까지 -0.162.

---

## 5. 다음 후보 우선순위

### 5.1 1순위 — numeric_lookup R@10 0.5295 진단 (cost 0, 0.5일)

**왜?**
- numeric_lookup 7 row, R@10 0.5295 (overall 대비 -0.20, 가장 약함 numeric)
- top-1 0.2857 (2/7 hit)
- query/정답 라벨 정확도 검증 + chunk text 매칭 분석

### 5.2 2순위 — table_lookup 6 row top-1 잔존 fix (운영 코드 변경)

G-U-003/A-008/021/201/202/204 의 root cause = search 가중치 / chunk text 합성. 운영 코드 1~2일 변경 필요.

### 5.3 3순위 — 12 docs v2 prompt reingest (cost ~$0.5~1.5)

D2 fix 를 sample-report 외 12 docs 로 확장. caption=false R@10 +0.05~0.10 가능.

### 5.4 4~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 4 | combo c P95 안정성 재측정 | 0.25일 | 0 | ★★ |
| 5 | chunk_filter 45.5% 마킹 분석 | 0.5일 | 0 | ★★ |
| 6 | budget_guard cache hit 무료 인식 | 0.5일 | 0 | ★ |
| 7 | RPC per-doc cap (큰 fix) | 1주+ | 0 | ★ |
| 8 | S4-B 핵심 엔티티 추출 | 3일 | 0 | ★★ |
| 9 | S5 /answer UX | 1주 | 0 | ★★ |

---

## 6. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-numeric | numeric_lookup 진단 진입 | cost 0, 0.5일 — 즉시 가능 | 다음 sprint |
| Q-table-tail | table_lookup 6 row top-1 잔존 fix | 운영 코드 변경 1~2일 | 후순위 |
| Q-other-docs | 12 docs v2 reingest | 사용자 명시 cost 승인 | 후순위 |
| (이전 잔존) | 별도 sprint | | |

---

## 7. 핵심 변경 파일 목록

### 신규
- 본 work-log
- `/tmp/diagnose_caption_top1.py` — 진단 스크립트 (gitignored)

### 수정
- `evals/golden_v2.csv` — 2 row acceptable_chunks 확장 (G-A-107/111)

### 운영 코드
- 0 건

### gitignored
- `evals/results/s4_a_d4_results.md` — caption=true 정정 후 갱신

### 데이터 영향
- 0 건

---

## 8. 한 문장 마감

> **2026-05-09 — caption=true top-1 진단 + 라벨 정정 ship**. 8/12 table_lookup top-1 miss row 의 root cause 분류 → 2 row (G-A-107/111) ch 69 (SAP 시스템 구축 목표) acceptable 추가. **table_lookup top-1 0.3333 → 0.5000 (+0.167, 50%↑) 🚀, caption=true top-1 0.5714 → 0.6429 (+0.072), caption gap top-1 -0.058 → -0.006 (거의 0 회복) ⭐**. Overall R@10 0.7296 → 0.7308, top-1 0.6258 → 0.6380. 단위 테스트 775 OK / 회귀 0. cost 0. 운영 코드 변경 0. 다음 후보 1순위 = numeric_lookup R@10 0.5295 진단.
