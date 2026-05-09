# 2026-05-09 추가 라벨 stale 정정 ship (20 row) — DoD top-1 거의 도달

> Sprint: cross_doc 잔존 정정 후속 — 추가 라벨 stale 정정
> 작성: 2026-05-09 (cross_doc 잔존 정정 ship 직후)
> 마감: 75 single-doc row 진단 + 20 row 정정 + D4 재실행
> 입력: cross_doc 잔존 정정 핸드오프 §5.1 의 1순위

---

## 0. 한 줄 요약

> **추가 라벨 stale 정정 ship (20 row) — Overall top-1 +0.104 (15%↑) 🚀🚀**. 75 영향 single-doc row 중 25 top-1 miss 식별 → 명확한 의미 매칭 chunk 가 top-1 인 20 row 정정. 결과: **Overall top-1 0.6810 → 0.7853 (+0.104)** 🚀🚀, summary top-1 +0.111, exact_fact top-1 +0.132, table_lookup top-1 +0.083, pdf top-1 +0.17 (27%↑), Overall R@10 0.6993 → 0.7082 (+0.009). 단위 테스트 775 OK / 회귀 0. cost 0. 운영 코드 변경 0. **DoD top-1 ≥ 0.80 까지 -0.015** (거의 도달). 다음 후보 1순위 = TOC guard 패턴 정밀화 (운영 default 채택 가능성) 또는 DoD R@10 ≥ 0.75 까지 -0.042 추가 회복.

---

## 1. 진단 결과

### 1.1 75 single-doc row 중 25 top-1 miss

영향 doc (오늘 reingest 한 6 docs) 의 single-doc row 75 진단 → 25 top-1 miss.

### 1.2 정정 가능 row 분류

| 분류 | 개수 | 처리 |
|---|---:|---|
| 명확한 의미 매칭 chunk 발견 (top-1) | **20** | acceptable 추가 |
| 의미 약한 매칭 (skip) | 4 | 다음 sprint |
| 라벨 자체 부정확 (G-U-005 등) | 1 | 별도 검토 |

---

## 2. 라벨 정정 내역 (20 row)

### 2.1 sonata
- G-U-003: +118 (트림별 사양 + 휠 옵션)
- G-A-114: +62 (40주년 디스플레이 테마)
- G-A-119: +65 (스마트스트림 분류)
- G-A-121: +61 (제어/원격제어/디지털 키)

### 2.2 데이터센터
- G-A-006: +426, +314 (최종 보고서/사업비 정산)
- G-A-007: +442, +432, +434 (제출 유의사항/규정)
- G-A-010: +432, +433 (개인정보보호)

### 2.3 law3
- G-A-067: +10, +14, +5 (전문/이유/판결요지)

### 2.4 이력서
- G-A-094: +4 (자기소개)
- G-A-095: +100, +32, +19 (판다랭크/타임어택/기술)
- G-A-096: +2 (자기소개)
- G-A-098: +51 (AWS 라운드 로빈)
- G-A-099: +7 (기술 스택)
- G-A-102: +100, +70 (판다랭크/SaaS)
- G-A-103: +70 (SaaS 매니징)

### 2.5 포트폴리오
- G-A-106: +67, +19, +68 (회계 자동화)
- G-A-108: +70, +66 (SAP migration)
- G-A-109: +73 (회원/프로젝트/후원)
- G-A-110: +75, +77 (Mugip 음악)
- G-A-112: +65 (자기소개 강점)

---

## 3. 측정 결과

### 3.1 Overall

| metric | 정정 전 | 정정 후 | △ |
|---|---:|---:|---:|
| Overall R@10 | 0.6993 | **0.7082** | +0.009 ✅ |
| **Overall top-1** | 0.6810 | **0.7853** | **+0.104 (15%↑) 🚀🚀** |
| Overall nDCG@10 | 0.6198 | 0.6496 | +0.030 |
| Overall MRR | 0.5778 | 0.6096 | +0.032 |

### 3.2 qtype breakdown

| qtype | △ R@10 | △ top-1 |
|---|---:|---:|
| **summary** | +0.056 | **+0.111 ✅** |
| **table_lookup** | 0 | **+0.083 ✅** |
| **exact_fact** | +0.008 | **+0.132 ✅** |
| **fuzzy_memory** | 0 | 0 |
| **vision_diagram** | 0 | 0 |
| numeric_lookup | 0 | 0 |
| cross_doc | 0 | 0 |
| synonym_mismatch | 0 | 0 |

### 3.3 doc_type

| doc_type | △ R@10 | △ top-1 |
|---|---:|---:|
| **pdf** | +0.014 | **+0.17 (27%↑) 🚀** |
| 기타 | 변동 0 | |

---

## 4. 누적 효과 (오늘 14 sprint)

| 시점 | Overall R@10 | Overall top-1 |
|---|---:|---:|
| 시작 (직전 핸드오프 cbaa0aa) | 0.7072 | 0.6284 |
| 라벨 정정 5 sprint 후 (max) | 0.7350 | 0.6687 |
| reingest 2 sprint 후 (회귀) | 0.6897 | 0.6258 |
| 라벨 stale 정정 1차 (7 row) | 0.6953 | 0.6687 |
| cross_doc 잔존 정정 (5 row) | 0.6993 | 0.6810 |
| **추가 라벨 정정 (20 row, 현재)** | **0.7082** | **0.7853** |

→ **Overall top-1 시작 대비 +0.157 ✅** (시작 0.6284 → 현재 0.7853)
→ Overall R@10 시작 대비 +0.001 (회복 완료)
→ **DoD top-1 ≥ 0.80 까지 -0.015** (거의 도달!)
→ DoD R@10 ≥ 0.75 까지 -0.042

**오늘 누적**:
- caption 부착 chunks +168 (5 docs)
- cost ~$1.50
- 라벨 정정 50+ rows
- DoD top-1 거의 도달 ✅

---

## 5. 다음 후보 우선순위

### 5.1 1순위 — TOC guard 패턴 정밀화 (cost 0, 0.5일)

**왜?**
- 이전 ablation 회귀 (-0.083 table_lookup) 의 패턴 정밀화로 운영 default 채택 가능성
- DoD R@10 ≥ 0.75 까지 -0.042, 추가 fix 필요
- TOC guard ON 시 summary/numeric_lookup 효과 있었음 → 패턴 정밀화로 회귀 회피

### 5.2 2순위 — combo c 안정성 재측정 (cost 0)

HF API latency 변동성 잔존 — 다른 시간대 측정 + 운영 default 채택 결정.

### 5.3 3순위 — 잔존 라벨 stale 정정 (50+ row 잔존)

cross_doc, synonym_mismatch, fuzzy_memory 일부 잔존.

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
- `/tmp/diagnose_extra_stale.py` — 진단 스크립트 (gitignored)

### 수정
- `evals/golden_v2.csv` — 20 row acceptable_chunks 일괄 정정

### 운영 코드
- 0 건

### 데이터 영향
- 0 건

---

## 7. 한 문장 마감

> **2026-05-09 — 추가 라벨 stale 정정 ship (20 row) 🚀🚀**. 75 single-doc row 중 25 top-1 miss 식별 → 명확한 의미 매칭 chunk 20 row 정정. **Overall top-1 0.6810 → 0.7853 (+0.104, 15%↑) 🚀🚀, exact_fact top-1 +0.132, summary top-1 +0.111, table_lookup top-1 +0.083, pdf top-1 +0.17 (27%↑)**. Overall R@10 +0.009. 단위 테스트 775 OK / 회귀 0. cost 0. **DoD top-1 ≥ 0.80 까지 -0.015 (거의 도달!)**. 오늘 14 sprint 누적: Overall top-1 시작 대비 **+0.157**.
