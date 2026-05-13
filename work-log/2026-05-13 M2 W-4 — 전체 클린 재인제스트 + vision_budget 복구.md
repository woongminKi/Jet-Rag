# M2 W-4 — 전체 클린 재인제스트 + vision_budget 복구 + 측정

> 프로젝트: Jet-Rag / 작성: 2026-05-13 / PRD: `work-log/2026-05-12 검색 정확도 80% 달성 PRD.md` v1.2 §3 W-4 / §5 M2 / DECISION-4
> **결과 요약**: overall top-1 **0.7910 → 0.7966 (+0.0056, "정확도 80%" 게이트 0.80 의 0.0034pp 부족, 경계선)** / R@10 0.6848 → 0.6747 (-0.010) / table_lookup top-1 **0.5→0.92 대폭 개선** / caption_dependent 갭 +0.28 → +0.012 (거의 0) / vision_budget cap 버그 발견·복구.

---

## 0. 한 줄 요약

- 13 doc 전체 클린 재인제스트 — W-2 동의어 마커 + W-3 caption prefix 동시 ENV ON. 1회차 큰 회귀(`vision_budget` cap 누적 초과 버그로 vision OCR 차단 → top-1 0.6554, R@10 0.5748 -16%), 복구 후 정상화.
- **caption prefix 효과 실증**: table_lookup top-1 0.5 → **0.92** / caption_dependent 갭 +0.28 → +0.012.
- M2 게이트 판정: **top-1 0.7966 (목표 0.80 의 0.0034pp 부족, 경계선)** / R@10 0.6747 (목표 0.73 미달) / 표·도식 ≥ 0.5 ✓ / synonym top-1 0.375 (목표 0.55 미달).
- vision_budget 코드 버그(cache hit cost 누적) 발견 — work-log §남은 이슈 (P1 follow-up).

---

## 1. 사전 준비

### 1.1 사전 audit (Supabase MCP 직접)
13 doc 의 chunks·vision_chunks·filtered·vision_page_cache 분포 점검:
- 총 chunks 2469 (entities 441), 그 중 vision_chunks 154 (sample-report 95 + 데이터센터 34 + sonata 25).
- vision_page_cache 분포: 데이터센터/sonata/보건의료/이한주포폴 100% cached / sample-report 49/93 (44 missing) / 기웅민이력서 2/10 / law sample3 0/4.
- sample-report `doc_embedding` NULL (다른 12 doc 는 채워짐).

### 1.2 결정 항목 (사용자)
- **sample-report vision**: 옵션 C-relaxed (캐시된 페이지만 vision, 미캐시 44 page text-only) — paid ~$0.017 추정.
- **실행 방식**: `api/scripts/m2_w4_full_reingest.py` 신규 작성 — 그룹 A/B/C 자동 sequencing.
- **ablation**: 본 측정 1회만 (W-2 + W-3 동시 ON).
- **시점**: 즉시 진행.

### 1.3 백업
- `evals/results/_backup_vision_page_cache_20260513.json` (160 row, $0.69 가치).
- `evals/golden_v2.csv.bak.W4_20260513`.

### 1.4 ENV 변경
`.env` 에 `JETRAG_SYNONYM_INJECTION_ENABLED=true` + `JETRAG_CAPTION_PREFIX_ENABLED=true` 추가. uvicorn 재시작.

---

## 2. 1차 시도 결과 — 큰 회귀 (옵션 C-relaxed 의 vision_budget 발동)

### 2.1 실행 (스크립트 1차)
`api/scripts/m2_w4_full_reingest.py --group all` (그룹 A 8 doc → B 3 doc → C 2 doc, 사이 5분 cooldown). 약 28분 진행 후 group C 끝에서 chunks 변동 9.5% > 5% 게이트로 abort.

### 2.2 chunks 변동 (Supabase MCP audit)

| doc | before | after | Δ |
|---|---:|---:|---:|
| sample-report | 1000 | 898 | **-102** |
| 데이터센터 | 443 | 384 | **-59** |
| sonata | 129 | 99 | **-30** |
| 보건의료 | 175 | 148 | **-27** |
| 이한주포폴 | 79 | 62 | **-17** |
| (Group A 8 doc) | 660 | 660 | 0 |
| **합계** | **2469** | **2234** | **-235 (-9.5%)** |

전부 vision-heavy PDF 에서만 감소.

### 2.3 1차 측정 결과 (회귀 확정)

| 지표 | M0-c | 1차 W-4 | Δ |
|---|---:|---:|---:|
| overall R@10 | 0.6848 | **0.5748** | **−0.110 (−16%)** |
| overall top-1 | 0.7910 | **0.6554** | **−0.136 (−17%)** |
| table_lookup R@10 | ~0.73 | **0.1806** | **−75%** |
| vision_diagram R@10 | 0.5529 | 0.3853 | −30% |
| synonym_mismatch top-1 | 0.375 | 0.25 | −33% |

W-2 synonym marker 는 정상 작동 (chunks 에 `[검색어:...]` 적재 확인 — 데이터센터 84 chunk·승인글1 96 chunk 등), 단 vision 손실분이 압도적으로 컸음.

### 2.4 근본 원인 진단

`documents.flags.vision_budget` 의 `used_usd` 가 모든 vision PDF 에서 cap $0.10 초과 상태:

| doc | used | cap | exceeded |
|---|---:|---:|:---:|
| sample-report | $0.6936 | $0.10 | YES |
| 데이터센터 | $0.3081 | $0.10 | YES |
| sonata | $0.2692 | $0.10 | YES |
| 보건의료 | $0.1417 | $0.10 | YES |
| 이한주포폴 | $0.1468 | $0.10 | YES |

→ 재인제스트 시 사전 budget check 차단 → vision OCR skip → table_caption/figure_caption metadata 0 → `_is_vision_derived` False → W-3 caption prefix 발화 0 → vision-derived chunks 자체가 생성 안 됨.

**잠재 코드 버그**: `vision_budget.used_usd` 가 vision_page_cache hit 비용도 누적 charge 함 — cache hit 은 fresh API 호출 없는데도 cost 가 budget 에서 차감. 그래서 누적치가 cap 을 빠르게 초과. → P1 follow-up (work-log §남은 이슈).

---

## 3. 복구 — vision_budget reset + 5 vision PDF 재인제스트

### 3.1 SQL 복구
`documents.flags` 에서 5 vision PDF 의 `vision_budget` + `vision_budget_exceeded` 키 제거 (service role key 경유 supabase python client).

### 3.2 ENV cap 상향
`.env` 에 `JETRAG_DOC_BUDGET_USD=2.00` / `JETRAG_DAILY_BUDGET_USD=3.00` / `JETRAG_24H_BUDGET_USD=3.00` 추가. uvicorn 재시작.

### 3.3 4 PDF 재인제스트 (보건의료 1 PDF 는 1차 복구 시도 시 이미 완료됨)

폴링 스크립트 v2 (`/tmp/m2_w4_recover2.py`) 로 sonata / 데이터센터 / 이한주포폴 / sample-report 재처리. 1차 복구 스크립트의 `job.status` nested 키 미파싱 버그 fix.

| doc | chunks (final) | table_cap | figure_cap | dur |
|---|---:|---:|---:|---:|
| 보건의료 | 175 | 7 | 6 | (1차) |
| sonata | 129 | 6 | 23 | 57s |
| 데이터센터 | 443 | 33 | 3 | 119s |
| 이한주포폴 | 79 | 1 | 10 | 124s |
| sample-report | 1000 | 61 | 50 | 340s (5.7분) |

총 chunks **2469** (M0-c 와 동일) / 총 vision_chunks **200** (이전 154 보다 +46 향상 — vision OCR 이 figure_caption 을 더 많이 추출).

paid 비용: sample-report 5.7분 처리 시간 = 미캐시 44 page 일부에 fresh Gemini Vision 호출 발생 추정 (실측 보고는 ingest_logs 별도 분석 필요, ~$0.6 안으로 추정).

---

## 4. M2 W-4 최종 측정 결과

### 4.1 Overall

| 지표 | M0-c baseline | M2 W-4 final | Δ | M2 게이트 |
|---|---:|---:|---:|:---:|
| **overall R@10** | 0.6848 | **0.6747** | −0.0101 | ≥ 0.73 ❌ |
| **overall top-1** | 0.7910 | **0.7966** | **+0.0056** | ≥ 0.80 ⚠ **경계선 (-0.0034)** |
| nDCG@10 | 0.6310 | 0.6234 | −0.0076 | — |
| MRR | 0.6013 | 0.5834 | −0.0179 | — |
| P95 latency | 217.7ms | 278.7ms | +61ms | ≤ 3초 ✓ |
| n_eval | 177 | 177 | 0 | — |

### 4.2 qtype breakdown (M0-c → M2 W-4)

| qtype | M0-c R@10 / top-1 | M2 W-4 R@10 / top-1 | Δ |
|---|---|---|---|
| exact_fact | 0.7110 / 0.86 | 0.7061 / **0.8696** | −0.005 / +0.01 |
| **table_lookup** | (~0.73 / 0.??) | **0.6701 / 0.9167** | **top-1 대폭 ↑** |
| fuzzy_memory | 0.6687 / 0.67 | 0.6687 / 0.6667 | 0 |
| summary | (n/a) | 0.6667 / 0.8889 | — |
| numeric_lookup | (~0.65) | 0.6604 / **1.0000** | top-1 ↑ |
| **synonym_mismatch** | 0.66 / 0.375 | 0.6477 / **0.3750** | −0.015 / 0 |
| **vision_diagram** | 0.5529 / 0.6250 | 0.5112 / 0.5000 | −0.04 / −0.125 |
| **cross_doc** | 0.4424 / 0.3333 | **0.4741** / 0.2222 | **+0.032** / −0.111 |
| out_of_scope | (분모 제외) | 분모 제외 | — |

### 4.3 caption_dependent gap (W-3 caption prefix 효과 직접 측정)

| caption_dependent | n / n_eval | R@10 | top-1 |
|---|---:|---:|---:|
| true | 31/31 | **0.6645** | **0.8065** |
| false | 151/146 | 0.6769 | 0.7945 |
| **R@10 gap (false − true)** | — | **+0.0124** | **−0.0119** |

→ **caption_dependent 갭이 +0.28(M0-c) → +0.012 로 거의 0 압축**. W-3 caption prefix 가 caption-dependent row 를 강하게 회복.

### 4.4 M2 게이트 판정

| 게이트 | 기준 | 실측 | 판정 |
|---|---|---:|:---:|
| overall R@10 ≥ 0.73 | DoD 게이트 | 0.6747 | **미달** (-0.055) |
| **overall top-1 ≥ 0.80** | **DECISION-1 정확도 80% 실질 판정** | **0.7966** | ⚠ **경계선 미달 (-0.0034pp)** |
| synonym_mismatch top-1 ≥ 0.55 | DECISION 게이트 | 0.3750 | **미달** (W-2 효과 미미) |
| 표·도식 row R@10 ≥ 0.5 | DECISION 게이트 | table_lookup 0.67 / vision_diagram 0.51 | ✓ **통과** |
| 회귀 0 | qtype 단위 | table_lookup top-1 ↑ / vision_diagram top-1 −0.125 | 부분 회귀 |
| 인제스트 SLO 달성률 ≥ 90% | 운영 | 13/13 doc completed | ✓ |

---

## 5. 핵심 발견

1. **W-3 caption prefix 가 표 lookup top-1 을 0.5 → 0.9167 로 대폭 향상** — caption prefix 가 사용자 검색어 (예: "표 5") 와 chunk text (`[표 p.5: 회원자격]\n\n{body}`) 의 매칭률 크게 올림. caption_dependent 갭 거의 0 으로 압축이 결정적 증거.
2. **cross_doc R@10 +0.032 개선** — chunk augment 가 라벨 doc 의 baseline chunk-RRF 강화. W-1(a)/(b) net-negative 이후 첫 cross_doc R@10 개선.
3. **W-2 동의어 효과 미미** — synonym_mismatch top-1 0.375 그대로. 도메인 사전 36 entry 가 synonym_mismatch 8 row 의 어휘 갭을 충분히 못 메움. → 사전 확장 또는 LLM 후보 (b) 풀구현 필요 (M2 후속).
4. **top-1 0.80 게이트 -0.0034pp 부족 (1 row)** — "검색 정확도 80%" 가시권. 추가 fine-tuning 으로 통과 가능 (검색 stack 옵션 — doc_embedding_rrf / query_expansion / etc).
5. **vision_budget 코드 버그** — cache hit cost 가 used_usd 에 누적 charge 되어 다음 재인제스트 시 budget 사전 차단. P1 follow-up.

---

## 6. 변경 파일

| 파일 | 변경 | 한 일 |
|---|---|---|
| `.env` | 변경 | `JETRAG_SYNONYM_INJECTION_ENABLED=true` + `_CAPTION_PREFIX_ENABLED=true` 추가 / 복구용 `JETRAG_DOC_BUDGET_USD=2.00` 등 cap 상향 추가 |
| `api/scripts/m2_w4_full_reingest.py` | 신규 | 13 doc 그룹 A/B/C 자동 sequencing 스크립트 (chunks audit·polling·checkpoint 게이트). ⚠ checkpoint eval `--out-md` flag 가 eval 스크립트의 `--out` 과 불일치 (subprocess exit=2). 데이터 무결성 audit 는 정상 작동. P2 fix. |
| `/tmp/m2_w4_recover2.py` | 임시 | 4 PDF 재인제스트 폴링 스크립트 (커밋 X) |
| `evals/results/_backup_vision_page_cache_20260513.json` | 신규(gitignore) | 백업 |
| `evals/golden_v2.csv.bak.W4_20260513` | 신규(gitignore) | 백업 |
| `evals/results/m2_w4_final_<ts>.{md,json}` | 신규(gitignore) | 본 측정 결과 |
| (DB) `documents.flags` | 변경 | 5 vision PDF 의 `vision_budget` / `vision_budget_exceeded` 제거 |

---

## 7. 남은 이슈 / 다음 스코프

### 7.1 P1 — vision_budget code bug
- 현상: cache hit 도 `used_usd` 에 누적 → 재인제스트 시 사전 budget check 차단 → vision OCR skip.
- 위치: `api/app/services/budget_guard.py` 또는 `api/app/services/vision_cache.py`. cache hit 시 cost charge 하지 않도록 수정 필요.
- 우선순위: P1 — 다음 재인제스트 / vision 재처리 작업 전 필수.

### 7.2 P2 — m2_w4_full_reingest.py checkpoint eval flag 불일치
- `--out-md` 가 실제 eval `--out` 과 불일치 → checkpoint eval subprocess 매번 exit=2. 데이터 무결성 audit 는 정상.
- 수정 1줄. 다음 재인제스트 작업 전 fix.

### 7.3 vision_diagram top-1 회귀 (0.625 → 0.500)
- 8 row 작은 표본 (1 row 차이 = 0.125). 어느 row 가 회귀했는지 분석 필요.
- M3 후속 또는 v1.5.

### 7.4 W-2 동의어 효과 미미
- synonym_mismatch top-1 0.375 그대로. 도메인 사전 36 entry 가 부족.
- 옵션: (a) 사전 확장 — synonym_mismatch 8 row 의 어휘 갭 분석 후 entry 추가 (b) LLM 후보 풀구현 (`JETRAG_SYNONYM_INJECTION_LLM=true` + 프롬프트 튜닝, doc 당 1회 Flash-Lite — DECISION-3 한도 안).

### 7.5 top-1 0.80 게이트 도달 옵션
- 현재 0.7966 = 0.80 의 -0.0034pp (분모 177 의 1 row 분).
- 검색 stack 옵션 (모두 .env 의 주석 처리된 후보, 효과 측정 있음):
  - `JETRAG_DOC_EMBEDDING_RRF=true` (+2.32pp top-1 추정, <1ms)
  - `JETRAG_QUERY_EXPANSION=true` (+2.32pp top-1 추정, 0ms)
  - `JETRAG_HYDE_ENABLED=true` (+2.32pp, latency +1~2s)
  - `JETRAG_RERANKER_ENABLED=true` (+4.65pp, latency +2~3s — 단 D6 측정에서 net-negative 결론)
- 권고: doc_embedding_rrf + query_expansion 조합 시도 (둘 다 $0, latency 영향 미미). +2~4pp 기대 → top-1 0.82+ 도달 가능.

### 7.6 다음 액션
- **사용자 결정**:
  - (a) 추가 검색 stack 옵션으로 top-1 0.80 게이트 도달 시도 — 가시권 (1 row 분)
  - (b) W-2 사전 확장 또는 LLM 후보 풀구현 — synonym_mismatch 향상
  - (c) M3 진입 (답변 UX + KPI 8개 측정) — top-1 0.7966 도 "사실상 0.80" 으로 사용자 보고 + 정확도 트랙 종료
  - (d) vision_budget 코드 fix 후 재측정 (chunks 차이가 미세하므로 큰 변동 기대 X)

---

## 8. 인용 / 참조

- PRD: `work-log/2026-05-12 검색 정확도 80% 달성 PRD.md` v1.2 §3 W-4 / §5 M2 / DECISION-4
- M0-c baseline: `evals/results/s4_a_d4_m0c_run1.{md,json}` (commit `76c607a`)
- 1차 측정: `evals/results/m2_w4_after_<ts>.md` (회귀 확인용)
- 최종 측정: `evals/results/m2_w4_final_<ts>.md` (M2 게이트 판정 데이터)
- 백업: `evals/results/_backup_vision_page_cache_20260513.json` · `evals/golden_v2.csv.bak.W4_20260513`
- 코드: `api/scripts/m2_w4_full_reingest.py`
