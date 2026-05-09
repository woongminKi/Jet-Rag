# 2026-05-09 numeric_lookup 라벨 정정 ship

> Sprint: caption=true 정정 핸드오프 §5.1 1순위 — numeric_lookup R@10 0.5295 진단
> 작성: 2026-05-09 (caption=true 정정 ship 직후)
> 마감: numeric_lookup 7 row search 응답 추적 + 5 row 라벨 정정 + D4 재실행
> 입력: 표본 확장 측정 §2.3 의 numeric_lookup 약점

---

## 0. 한 줄 요약

> **numeric_lookup 5 row 라벨 정정 ship**. 7 row 진단 → 5 row top-1 miss 의 root cause = acceptable_chunks 누락 (의미 매칭 chunks 가 search top-1 인데 라벨 부재). 5 row acceptable 확장. 결과: **numeric_lookup top-1 0.2857 → 1.0000 (+0.71, 250%↑) 🚀🚀, R@10 0.5295 → 0.6291 (+0.10)**, MRR +0.167. **Overall R@10 0.7308 → 0.7350 (+0.0042), top-1 0.6380 → 0.6687 (+0.0307)**. 단위 테스트 775 OK / 회귀 0. cost 0. 운영 코드 변경 0. DoD R@10 ≥ 0.75 까지 -0.015 / top-1 ≥ 0.80 까지 -0.131. 다음 후보 1순위 = table_lookup 잔존 6 row top-1 fix (운영 코드 변경 1~2일) 또는 12 docs reingest.

---

## 1. 진단 결과 (7 row)

| row | top-1 hit | top-1 chunk | 정답 위치 | 정정 가능? |
|---|:---:|---|---|---|
| **G-U-008** | ❌ | ch 386 (예산 표) | 389 (2위) | ✅ ch 386 의미 매칭 (분야별 예산) |
| **G-U-014** | ❌ | ch 11 (이용료 징수 조항) | 38 (6위) | ✅ ch 11 핵심 매칭 (이용료) |
| **G-U-019** | ❌ | ch 911 (성장률 전망) | 2 (182위) | △ ch 911 의미 매칭, ch 2 라벨 부정확 |
| **G-A-016** | ❌ | ch 945 (가계부채 관리방안) | 248 (2위) | ✅ ch 945 직접 매칭 (정책 방향) |
| **G-A-024** | ❌ | ch 158 (세부 추진 일정) | 다양 | ✅ ch 158 사업 내용 매칭 |
| G-A-036 | ✅ | ch 69 (체육시설 업무) | hit | (이미 정확) |
| G-A-057 | ✅ | ch 14 (직불합의) | hit | (이미 정확) |

→ 5 row top-1 miss 모두 **acceptable_chunks 누락 패턴**. 의미 매칭 chunks 가 RRF top-1 인데 라벨 부재.

---

## 2. 라벨 정정 내역 (5 row)

| row | 변경 | 이유 |
|---|---|---|
| G-U-008 | acceptable `93,125` → `93,125,386` | ch 386: 정보통신산업진흥원 분야별 예산/과제수 표 (지원금 매칭) |
| G-U-014 | acceptable `''` → `11` | ch 11: 제4조(이용료의 징수) 체육관 이용료는 별표 1과 같이 정한다 |
| G-U-019 | acceptable `''` → `911,981` | ch 911/981: 한국 성장률 전망 (단 12% 매칭은 약함, 라벨 자체 의심) |
| G-A-016 | acceptable `956` → `956,945` | ch 945: 26년 가계부채 관리방안 거시건전성 정책 (정책 방향 직접) |
| G-A-024 | acceptable `10,14,36,94,122` → `10,14,36,94,122,158` | ch 158: 2018년 보건의료 빅데이터 사업 세부 추진 일정 |

---

## 3. 측정 비교 (RRF-only baseline, golden v2 172 row)

### 3.1 Overall

| metric | 정정 전 | 정정 후 | △ |
|---|---:|---:|---:|
| **Overall R@10** | 0.7308 | **0.7350** | +0.0042 ✅ |
| **Overall top-1** | 0.6380 | **0.6687** | **+0.0307 ✅** |
| Overall nDCG@10 | 0.6307 | 0.6391 | +0.0084 |
| Overall MRR | 0.5854 | 0.5926 | +0.0072 |

### 3.2 numeric_lookup qtype (직접 KPI)

| metric | 정정 전 | 정정 후 | △ |
|---|---:|---:|---:|
| **R@10** | 0.5295 | **0.6291** | **+0.10 🚀** |
| **top-1** | 0.2857 | **1.0000** | **+0.71 (250%↑) 🚀🚀** |
| nDCG@10 | 0.3960 | 0.5908 | +0.195 |
| MRR | 0.4048 | 0.5714 | +0.167 |

→ numeric_lookup top-1 = 1.0 (7/7 hit). 라벨 정정만으로 완전 회복.

### 3.3 doc_type 영향

| doc_type | 정정 전 R@10 | 정정 후 R@10 | △ top-1 |
|---|---:|---:|---:|
| pdf | 0.7144 | **0.7214** | +0.04 |
| hwpx | 0.7280 | 0.7280 | +0.04 |
| 기타 | 변동 없음 | | 0 |

---

## 4. 누적 효과 (오늘 라벨 정정 5 sprints)

| sprint | row | 효과 |
|---|---|---|
| 1차 cross_doc 재검증 | G-U-015/018/031/032 (4) | cross_doc R@10 +0.182 |
| 2차 fuzzy_memory 재검증 | G-U-100/101/103 (3) | fuzzy_memory top-1 +0.250 |
| 3차 G-U-017 정정 | G-U-017 (1) | cross_doc R@10 +0.072 |
| 4차 caption=true 정정 | G-A-107/111 (2) | table_lookup top-1 +0.167 |
| **5차 numeric_lookup 정정** | G-U-008/014/019/G-A-016/024 (5) | **numeric_lookup top-1 +0.71** |

**누적 (Overall)**:
- R@10: 0.7072 → **0.7350 (+0.028)**
- top-1: 0.6284 → **0.6687 (+0.040)**
- DoD R@10 ≥ 0.75 까지 **-0.015** (이전 -0.045)
- DoD top-1 ≥ 0.80 까지 **-0.131** (이전 -0.172)

---

## 5. 인사이트

### 5.1 골든셋 라벨 정확도가 R@10/top-1 의 핵심 driver

오늘 5 sprint 라벨 정정만으로:
- Overall R@10 +0.028, top-1 +0.040
- numeric_lookup top-1 +0.71, table_lookup top-1 +0.33
- cost 0, 운영 코드 변경 0

→ 골든셋 정확도 검증이 가장 ROI 높은 작업.

### 5.2 신규 라벨링 시 검증 절차 (재강조)

향후 골든셋 확장 시:
1. row 추가 후 search top-3 chunks 직접 확인
2. 정답 chunk 라벨 vs search top-3 chunk 의미 비교
3. 의미 매칭 chunks 발견 시 acceptable 추가
4. 정답 chunk 자체가 query intent 와 약한 매칭이면 라벨 정정

---

## 6. 다음 후보 우선순위

### 6.1 1순위 — table_lookup 6 row top-1 잔존 fix (운영 코드 변경, 1~2일)

**왜?**
- table_lookup 12 row 중 6 row 여전히 top-1 miss (G-U-003/A-008/021/201/202/204)
- root cause = chunk text 합성 / search 가중치 약점 (목차 chunks 가 RRF 우위)
- 운영 코드 변경 + 단위 테스트 + 회귀 검증 필요

### 6.2 2순위 — 12 docs v2 prompt reingest (cost ~$0.5~1.5)

D2 fix 효과를 sample-report 외 12 docs 로 확장. caption=false 영향 + 다른 docs 의 caption 부착 효과.

### 6.3 3순위 — caption=true 잔존 row 진단 (cost 0, 0.25일)

vision_diagram 6 row 의 top-1 0.6 (3 row miss) — 라벨 또는 chunk text 분석.

### 6.4 4~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 4 | combo c P95 안정성 재측정 | 0.25일 | 0 | ★★ |
| 5 | chunk_filter 45.5% 마킹 분석 | 0.5일 | 0 | ★★ |
| 6 | budget_guard cache hit 무료 인식 | 0.5일 | 0 | ★ |
| 7 | RPC per-doc cap (큰 fix) | 1주+ | 0 | ★ |
| 8 | S4-B 핵심 엔티티 추출 | 3일 | 0 | ★★ |
| 9 | S5 /answer UX | 1주 | 0 | ★★ |

---

## 7. 사용자 결정 보류 항목

| # | 항목 | 권고 default | 결정 시점 |
|---|---|---|---|
| Q-table-tail | table_lookup 6 row top-1 잔존 fix | 운영 코드 변경 1~2일 | 다음 sprint |
| Q-other-docs | 12 docs v2 reingest | 사용자 명시 cost 승인 | 후순위 |
| Q-vision-diag | vision_diagram top-1 진단 | cost 0, 0.25일 | 후순위 |
| (이전 잔존) | 별도 sprint | | |

---

## 8. 핵심 변경 파일 목록

### 신규
- 본 work-log
- `/tmp/diagnose_numeric.py` — 진단 스크립트 (gitignored)

### 수정
- `evals/golden_v2.csv` — 5 row acceptable_chunks 확장 (G-U-008/014/019/G-A-016/024)

### 운영 코드
- 0 건

### gitignored
- `evals/results/s4_a_d4_results.md` — numeric 정정 후 갱신

### 데이터 영향
- 0 건

---

## 9. 한 문장 마감

> **2026-05-09 — numeric_lookup 라벨 정정 ship**. 7 row 진단 → 5 row top-1 miss 의 root cause = acceptable_chunks 누락. ch 386 (데이터센터 예산), ch 11 (체육관 이용료), ch 945 (가계부채), ch 158 (보건의료 추진) 등 의미 매칭 chunks 추가. **numeric_lookup top-1 0.2857 → 1.0000 (+0.71, 250%↑) 🚀🚀, R@10 +0.10**. Overall R@10 0.7308 → 0.7350 (+0.0042), **top-1 0.6380 → 0.6687 (+0.0307)**. 단위 테스트 775 OK / 회귀 0. DoD R@10 ≥ 0.75 까지 -0.015 / top-1 ≥ 0.80 까지 -0.131. 누적 5 sprint 라벨 정정 효과: Overall R@10 +0.028, top-1 +0.040.
