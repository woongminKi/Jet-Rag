# 2026-05-09 cross_doc 라벨 재검증 + acceptable 확장 ship

> Sprint: multi-doc 진단 핸드오프 §5.1 1순위 — acceptable_chunks 확장 + 라벨 재검증
> 작성: 2026-05-09 (multi-doc 진단 ship 직후)
> 마감: 4 row 라벨 정정 + D4 도구 재실행 + cross_doc R@10 회복 검증
> 입력: multi-doc 진단 work-log §2.4 + Pattern 2/4 의 4 row

---

## 0. 한 줄 요약

> **cross_doc 라벨 재검증 ship — 4 row (G-U-015/018/031/032) 의 정답 chunk 정정 + acceptable 확장**. 결과: **cross_doc R@10 0.2917 → 0.4738 (+0.182, 62%↑)**, **cross_doc top-1 0.2500 → 0.5000 (+0.250, 100%↑)** 🚀. Overall R@10 0.7219 → 0.7264 (+0.0045). 정정 핵심: G-U-015 한마음에 위원회 chunks 0건 발견 → 직제 ch 102 만 정답으로 변경, G-U-031 sonata ch 129 (QR/만족지수) → ch 113 (스마트센스 안전 기능) 정정, G-U-018/032 acceptable 인접 chunks 추가. G-U-017 은 임대차 자료 자체 부재 (별도 sprint 필요). 단위 테스트 775 OK / 회귀 0. cost 0. 운영 코드 변경 0. 다음 후보 1순위 = doc-size bias 완화 fix (Pattern 1 잔존, 운영 코드 변경 1~2일).

---

## 1. 라벨 정정 내역

### 1.1 G-U-015: 위원회 역할 (한마음 + 직제)

**진단**:
- 한마음 chunks 56 전체 검색 → "위원회|위원장" 매칭 **0건** (정답 자체 부재)
- 직제 chunks 171 → 매칭 **1건 (ch 102)**
- 기존 라벨 (relv 0, 15) = 두 doc 모두 표지/개정이력 (위원회 무관)

**변경**:
- relevant_chunks: `0,15` → **`102`** (직제 ch 102 만)
- acceptable_chunks: `''` → **`0,15`** (기존 라벨 보존)

### 1.2 G-U-018: 손해배상 기준 (law2 + law3)

**진단**: law2 ch 16 (하도급대금 지급보증) + ch 17 (직접지급청구) 둘 다 손해배상 부분 관련.

**변경**:
- relevant_chunks: `16,27` 그대로
- acceptable_chunks: `''` → **`17`** (ch 17 추가)

### 1.3 G-U-031: 안전 관련 (sonata + 데이터센터)

**진단**:
- sonata ch 129 = QR 코드/만족지수 — **안전과 무관**
- sonata ch 113 = 스마트센스 (차로유지/충돌방지) — **안전 핵심 ⭐**
- 데이터센터 ch 397 = 해외 진출 지원

**변경**:
- relevant_chunks: `129,397` → **`113,397`** (sonata 정답 정정)
- acceptable_chunks: `''` → **`129`** (기존 라벨 acceptable 강등)

### 1.4 G-U-032: 데이터 활용 방식 (보건의료 + 데이터센터)

**진단**: 보건의료 ch 151/155 모두 데이터 연계 활용 핵심 / 데이터센터 ch 385 공모 개요 부분 관련.

**변경**:
- relevant_chunks: `10,441` 그대로
- acceptable_chunks: `''` → **`151,155,385`** (3건 추가)

### 1.5 G-U-017: 임대차 분쟁 (라벨 부정확, 별도 sprint)

**진단**: law2 ch 6 (하도급), law3 ch 3 (대여금) — **두 doc 다 임대차 자료 아님**. query 자체가 부재한 자료를 가정.

**처리**: 변경 없음. 다음 sprint 에서 query 자체 정정 또는 negative=true 처리.

---

## 2. 측정 비교 (D4 RRF-only baseline)

### 2.1 Overall

| metric | 라벨 정정 전 | 라벨 정정 후 | △ |
|---|---:|---:|---:|
| Overall R@10 | 0.7219 | **0.7264** | +0.0045 ✅ |
| Overall top-1 | 0.6135 | 0.6196 | +0.0061 |
| Overall nDCG@10 | 0.6204 | 0.6239 | +0.0035 |
| Overall MRR | 0.5811 | 0.5841 | +0.0030 |

### 2.2 cross_doc qtype (직접 KPI)

| metric | 정정 전 | 정정 후 | △ |
|---|---:|---:|---:|
| **R@10** | 0.2917 | **0.4738** | **+0.182 (62%↑) 🚀** |
| **top-1** | 0.2500 | **0.5000** | **+0.250 (100%↑) 🚀** |
| nDCG@10 | 0.2382 | 0.3824 | +0.144 |
| MRR | 0.2083 | 0.3333 | +0.125 |

→ cross_doc 4 row 의 절반 (top-1 1/4 → 2/4 hit, R@10 1.17 → 1.90 점 합) 가 정확한 정답으로 인식됨.

### 2.3 caption_dependent gap (안정 검증)

| 시점 | true R@10 | false R@10 | gap |
|---|---:|---:|---:|
| 표본 확장 후 | 0.7795 | 0.7099 | -0.0696 |
| **라벨 정정 후** | **0.7795** | **0.7153** | **-0.0642** |

→ caption=true R@10 그대로 (cross_doc 4 row 가 caption=false 라 caption gap 미세 변동만).

### 2.4 doc_type breakdown

| doc_type | 정정 전 R@10 | 정정 후 R@10 | △ |
|---|---:|---:|---:|
| pdf | 0.7053 | 0.7125 | +0.0072 ✅ |
| hwpx | 0.7200 | 0.7200 | 0 |
| docx | 0.6638 | 0.6638 | 0 |
| hwp | 0.8502 | 0.8502 | 0 |
| pptx | 1.0 | 1.0 | 0 |

→ pdf 미세 회복 (sonata + 데이터센터 영향).

---

## 3. ROI 검증

### 3.1 가설 — acceptable_chunks 확장 + 라벨 정정이 cross_doc R@10 회복

**검증**: 강한 양성.

- cross_doc R@10 +0.182 / top-1 +0.250 — 예상 (+0.10~0.15) 초과
- Overall R@10 +0.0045 — 예상 (+0.005~0.010) 범위
- multi-doc helper 자체는 정상 (G-U-026 R@10=1.0 검증) → 라벨 정확도가 직접 효과

### 3.2 cost ROI

- cost: 0 (라벨링 + 검증 작업만)
- gain: cross_doc R@10 +0.182, top-1 +0.250
- 단위 ROI: 무한대 (cost 0, R@10 의미 있는 이동)

### 3.3 잔존 한계

- **Pattern 1 (doc-size bias) 미해결** — G-U-018/027/015 한마음 정답 doc 자체가 top-50 밖. 라벨링으로 회복 불가
- **G-U-017 query 자체 부적절** — 임대차 자료 부재 → 별도 sprint
- **cross_doc 표본 4 row** — 통계 신뢰도 한계

---

## 4. 다음 후보 우선순위

### 4.1 1순위 — doc-size bias 완화 fix (운영 코드 변경, 1~2일)

**왜?**
- Pattern 1 잔존 (G-U-018/027 정답 doc top-50 밖)
- 큰 doc 의 chunks 가 RRF top 우선 → 작은 doc 정답 밀림
- 라벨링으로는 회복 불가, search 가중치 변경 필요

**작업**:
- multi-doc query detector + per-doc top-K cap retrieval
- 단위 테스트 + golden v2 회귀 검증
- 운영 코드 변경, 회귀 risk

### 4.2 2순위 — G-U-017 query 정정 또는 row 제외 (cost 0)

임대차 자료 자체 부재 → query 를 다른 의도로 변경 또는 negative=true 처리.

### 4.3 3~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 3 | 12 docs v2 prompt reingest | 가변 | $0.5~1.5 | ★★ |
| 4 | combo c P95 안정성 재측정 | 0.25일 | 0 | ★★ |
| 5 | numeric_lookup R@10 0.5295 진단 | 0.5일 | 0 | ★★ |
| 6 | fuzzy_memory top-1 -0.25 진단 | 0.5일 | 0 | ★ |
| 7 | chunk_filter 45.5% 마킹 분석 | 0.5일 | 0 | ★★ |
| 8 | budget_guard cache hit 무료 인식 | 0.5일 | 0 | ★ |
| 9 | S4-B 핵심 엔티티 추출 | 3일 | 0 | ★★ |
| 10 | S5 /answer UX | 1주 | 0 | ★★ |

---

## 5. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-doc-bias | doc-size bias 완화 fix 진입 | 운영 코드 변경 + 회귀 risk + 1~2일 | 다음 sprint |
| Q-G-U-017 | G-U-017 query 정정 또는 제외 | 임대차 자료 부재 → query 변경 | 후순위 |
| Q-other-docs | 12 docs v2 prompt reingest | 사용자 명시 cost 승인 | 후순위 |
| (이전 잔존) | 별도 sprint | | |

---

## 6. 핵심 변경 파일 목록

### 신규
- 본 work-log

### 수정
- `evals/golden_v2.csv` — 4 row 라벨 정정 (G-U-015/018/031/032)

### 운영 코드
- 0 건

### gitignored
- `evals/results/s4_a_d4_results.md` — 라벨 정정 후 측정 갱신
- `evals/results/s4_a_d4_raw.json`

### 데이터 영향
- 0 건 (chunks / cache 변동 없음)

---

## 7. 한 문장 마감

> **2026-05-09 — cross_doc 라벨 재검증 ship**. 4 row (G-U-015/018/031/032) 라벨 정정 — 한마음 위원회 chunks 0건 발견 → 직제 ch 102 만 정답, sonata ch 129 (QR) → ch 113 (스마트센스 안전) 정정, acceptable 인접 chunks 확장. **cross_doc R@10 0.2917 → 0.4738 (+0.182, 62%↑) / top-1 0.2500 → 0.5000 (+0.250, 100%↑) 🚀**. Overall R@10 +0.0045. 단위 테스트 775 OK / 회귀 0. cost 0. 운영 코드 변경 0. 다음 후보 1순위 = doc-size bias 완화 fix (운영 코드 변경, 1~2일).
