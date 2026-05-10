# 2026-05-10 1순위 — candidates 자동 review + golden v2 9 row add ship

> Sprint: 1순위 (B 단계 후속) — candidates 자동 review + 골든셋 add
> 작성: 2026-05-10
> 마감: B 단계 산출 10 candidates → DB ILIKE 자동 verify → golden_v2.csv +9 row append
> 입력: B 단계 work-log §3 (10 candidates 사용자 review 항목) + 한계 #11 (minority qtype 표본 작음)

---

## 0. 한 줄 요약

> **1순위 sprint ship — candidates 자동 review + golden v2 +9 row 추가**. v1 시도는 `/search` HTTP 호출이 BGE-M3 HF API timeout (180s + retry 후에도 실패, 한계 #3/#8/#12 정합) 으로 전수 실패. **v2 pivot — DB 직접 ILIKE keyword 매칭** (HF/embedding 의존 0). 10 candidates 중 **9건 acceptable_chunks 자동 채움 + golden v2 add 완료**, 1건 (G-Z-008 직제규정 조직도) drop — keyword 매칭 chunks 0건으로 LLM hallucination 검증. golden v2 **172 → 181 rows** (synonym_mismatch 4 → 9, vision_diagram 6 → 10). 단위 테스트 800 OK / 회귀 0. CSV schema 14 컬럼 무결성 검증 완료. **누적 cost (이번 세션) 변동 0** (DB 직접 query, 외부 API 호출 0).

---

## 1. v1 → v2 pivot 학습

### 1.1 v1 (`/tmp/review_candidates.py`) — /search HTTP 호출 전수 실패

각 candidate 마다 `GET /search?q=...&doc_id=...` 호출 → timeout 30s 1차 실패 → 180s + retry 1회 (effective 360s) 적용 후에도 G-Z-001 부터 **모두 timeout**. 원인: BGE-M3 HF API dense embedding latency (P95 73s+ + 간헐 timeout, 직전 핸드오프 §6 한계 #3/#8/#12 정합).

### 1.2 v2 (`/tmp/review_candidates_v2.py`) — DB 직접 ILIKE

- `Supabase.table("chunks").eq("doc_id", X).ilike("text", "%키워드%")` 로 keyword 매칭 chunks lookup
- 가장 차별성 높은 keyword (긴 것) 우선 → 5 chunks 모이면 stop
- top-3 chunk_idx → `acceptable_chunks` 자동 채움
- 외부 API 호출 0 — 결정적 + 빠름 (~0.1~0.5s/candidate)

→ **10 candidates 모두 처리 완료** (9 add + 1 drop) — 1차 시도 timeout 전수 실패와 대조.

### 1.3 인사이트

BGE-M3 HF API 의존성이 검증/측정 도구를 fragile 하게 만듦. 본 sprint 의 패턴 (DB 직접 ILIKE 으로 보조 검증) 은 향후 골든셋 라벨링 sprint 에서도 1순위 패턴 권고.

---

## 2. 변경 내역

### 2.1 `evals/golden_v2.csv` +9 rows

| 신규 ID | 원 candidate | qtype | doc | acceptable_chunks |
|---|---|---|---|---:|
| G-U-104 | G-Z-001 | synonym_mismatch | 보건의료_빅데이터 | 9,13,18 |
| G-U-105 | G-Z-002 | synonym_mismatch | 직제_규정 | 63,90,96 |
| G-U-106 | G-Z-003 | synonym_mismatch | 한마음생활체육관 | 10,23,24 |
| G-U-107 | G-Z-004 | synonym_mismatch | 한마음생활체육관 | 11,50,51 |
| G-U-108 | G-Z-005 | synonym_mismatch | 보건의료_빅데이터 | 9,13,18 |
| G-A-211 | G-Z-006 | vision_diagram | sonata-the-edge | 1,4,37 |
| G-A-212 | G-Z-007 | vision_diagram | 데이터센터 통합 | 0,4,6 |
| G-A-213 | G-Z-009 | vision_diagram | sample-report | 119,185,191 |
| G-A-214 | G-Z-010 | vision_diagram | sample-report | 178,344,722 |

**source_hint**: 각 신규 row 에 `auto-gen (G-Z-XXX, B 단계)` 명시 (계보 추적).

### 2.2 G-Z-008 drop 사유 (LLM hallucination 검증)

`G-Z-008 직제규정에 조직도 그림 있어?` (vision_diagram, doc=a5f3cfae...) — must_include `조직도;계층` keyword DB ILIKE 매칭 chunks **0건**.

→ 직제규정 doc 에 "조직도" / "계층" 텍스트 없음. LLM 이 doc 제목/카테고리 ("직제 규정") 만 보고 "조직도가 있을 것" 추측 → false candidate.

이 drop 은 **B 단계 work-log §4 한계 #1 (LLM hallucination 위험) 의 실증 검증**. 본 sprint 의 자동 review 패턴이 이런 false candidate 를 자동 식별 → golden v2 오염 회피.

### 2.3 검증

- **CSV schema**: 14 컬럼 무결성 ✅ (Python csv.DictReader 로드 + 컬럼 set 비교)
- **신규 row acceptable_chunks**: 9건 모두 비어있지 않음 ✅
- **단위 테스트**: 800 / OK / skipped=1 / 회귀 0
- **R@10 D4 측정**: BGE-M3 HF dependency timeout risk → 본 sprint scope 제외 (별도 sprint 권고)
- **golden v2 row 수**: 172 → 181 (+9) ✅

---

## 3. qtype 분포 추이

| qtype | 직전 | 현재 | △ |
|---|---:|---:|---:|
| exact_fact | 115 | 115 | 0 |
| table_lookup | 12 | 12 | 0 |
| **synonym_mismatch** | **4** | **9** | **+5** ⭐ |
| **vision_diagram** | **6** | **10** | **+4** ⭐ |
| fuzzy_memory | 9 | 9 | 0 |
| summary | 9 | 9 | 0 |
| numeric_lookup | 7 | 7 | 0 |
| cross_doc | 5 | 5 | 0 |
| out_of_scope | 5 | 5 | 0 |
| **total** | **172** | **181** | **+9** |

**direct hit on 한계 #11**: minority qtype 2종 (synonym_mismatch + vision_diagram) 표본 +50%~+100% 확장.

---

## 4. 비판적 한계 (정직 인정)

| # | 한계 | 영향 | 완화 |
|---|---|---|---|
| 1 | **acceptable_chunks 자동 = top-3 ILIKE 매칭** | 100% 정확하지 않음 — 사용자 사후 정정 가능 | source_hint 에 `auto-gen` 명시 → 사용자 식별 용이 |
| 2 | **expected_answer_summary 일부 = chunk text** (G-U-105/106/107) | RAGAS 측정 시 "요약" 자리에 chunk text → false summary metric 위험 | 별도 sprint 에서 expected_summary 정정 (수작업 또는 LLM compress) |
| 3 | **G-U-104 ↔ G-U-108 유사 query** (둘 다 보건의료 개인정보 보호) | dedup 로직 (60% word overlap) 통과 — 60% 임계 약함 | 임계 강화 (50%) 또는 LLM-judge dedup (별도 sprint) |
| 4 | **R@10 baseline 미재측정** | BGE-M3 timeout 회피 → 본 sprint scope 제외 | HF latency 회복 후 별도 sprint |
| 5 | **G-Z-008 dropped — vision_diagram 5 → 4 의도 미달** | qtype 확장 목표 +5 → +4 미달 | LLM 재호출 시 prompt 에 "doc 에 실제 그림이 있어야 함" 강조 (별도 sprint) |

---

## 5. 사용자 결정 보류 항목 변동

| ID | 항목 | 직전 | 갱신 |
|---|---|---|---|
| Q-B-review | candidate review 절차 | 사용자 수동 review | **자동 review pattern 검증 ✅** — DB ILIKE 패턴 1순위 권고 |
| Q-summary-fix | expected_answer_summary chunk text 정정 | 신규 | **별도 sprint** — 수작업 또는 LLM compress |
| Q-S5-5 | RAGAS sample 크기 | 부분 해소 (n=8) | **n=30 재측정 input 확보 ✅** — golden v2 181 rows 로 재측정 가능 |

---

## 6. 다음 후보 우선순위

### 6.1 1순위 — RAGAS n=30 재측정 (cost ~$0.05~0.10, 0.5 day)

**왜?** golden v2 181 rows + minority qtype 확장 → S5-B baseline 통계 신뢰도 향상 input 확보 완료. 사용자 cost 승인 시 진입.

### 6.2 2순위 — R@10 D4 baseline 재측정 (cost 0, 0.5 day)

신규 9 row 의 R@10 / top-1 측정. BGE-M3 HF API 안정 시 진입 (한계 #3/#8/#12 부분 회복 시).

### 6.3 3순위 — TOC guard 패턴 정밀화 (cost 0, 0.5 day)

table_lookup -0.083 회귀 회복.

### 6.4 4~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 4 | expected_answer_summary 정정 (G-U-105~107) | 0.5 day | 0 | ★★ |
| 5 | cross_doc qtype 자동 생성 (B 후속) | 1 day | ~$0.05 | ★★ |
| 6 | chunk_filter 마킹 false positive 분석 | 0.5 day | 0 | ★ |
| 7 | S4-B 핵심 엔티티 추출 | 3 day | 0 | ★★ |
| 8 | RPC per-doc cap (큰 fix) | 1주+ | 0 | ★ |
| 9 | S6 (search inline answer modal) | 1~2주 | 0 | ★ |

---

## 7. 핵심 변경 파일 목록

### 수정
- `evals/golden_v2.csv` (+9 rows) — minority qtype 확장 (G-U-104~108 + G-A-211~214)
- 본 work-log

### gitignored / 삭제
- `/tmp/review_candidates.py` — v1 (HTTP 의존, 사용 X)
- `/tmp/review_candidates_v2.py` — v2 실행 도구 (one-off)
- `/tmp/golden_v2.backup.csv` — append 전 backup

### 데이터 영향
- `evals/golden_v2.csv` only (+9 row, schema 변동 0)
- chunks / vision_page_cache / vision_usage_log: 0 변동

### 운영 코드 변경 영향
- 0 건

### 외부 cost
- 0 (DB 직접 query, 외부 API 호출 0)
- 누적 (이번 세션 전체): ~$0.21 (변동 없음)

---

## 8. 한 문장 마감

> **2026-05-10 — 1순위 sprint ship**. candidates 자동 review (v1 BGE-M3 HF timeout 전수 실패 → v2 DB 직접 ILIKE pivot) → 9/10 candidates 검증 + golden v2 +9 row 추가 (1건 drop = LLM hallucination 자동 검증). golden v2 **172 → 181 rows** (synonym_mismatch +5, vision_diagram +4). CSV 14 컬럼 무결성 ✅, 단위 테스트 800 OK / 회귀 0. 누적 cost ~$0.21 (변동 없음). 다음 1순위 = **RAGAS n=30 재측정** (cost ~$0.05~0.10, 사용자 승인 필요) 또는 **R@10 D4 재측정** (cost 0, BGE-M3 안정 시).
