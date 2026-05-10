# 2026-05-10 B — golden v2 minority qtype AI 자동 확장 ship (10 candidates)

> Sprint: B 단계 — golden v2 표본 확장 (S5-B 후속, 권고 시퀀스 C → A → B 의 마지막)
> 작성: 2026-05-10
> 마감: `evals/generate_golden_rows.py` 신규 + 10 unit tests + Gemini 2.5 Flash 자동 생성으로 minority qtype 10 candidates 산출 (사용자 후속 review 대기)
> 입력: 직전 핸드오프 §6 한계 #11 (golden v2 minority qtype 표본 작음) + 사용자 명시 "AI 자동 생성으로 가자"

---

## 0. 한 줄 요약

> **B 단계 ship — minority qtype AI 자동 확장 인프라**. `evals/generate_golden_rows.py` (310 LOC) 신규: golden v2 의 qtype 별 few-shot prompt + Gemini 2.5 Flash JSON-mode 호출 → candidate row CSV 자동 생성 (사용자 review 후 수동 append). 10 unit tests / 회귀 0. 1차 실행으로 **synonym_mismatch 5 + vision_diagram 5 = 10 candidates** 생성 (cost ~$0.02, 26.5s). 출력은 `evals/results/golden_v2_candidates.csv` (gitignored, 본 work-log §3 reproduced). **자동 append 금지** (LLM hallucination 위험 → 사용자 acceptable_chunks 수동 채움 + 라벨 검증 필수). cross_doc qtype 은 multi-doc 합성 복잡도로 첫 ship 제외. **S5 진척률 ~95% → 100% 도달 (S5 마감) ✅**. 누적 cost (이번 세션) ~$0.21 (S5-B $0.19 + B $0.02).

---

## 1. 변경 내역

### 1.1 신규 — `evals/generate_golden_rows.py` (310 LOC)

**핵심 로직**:
- `load_examples_by_qtype(csv)` — golden v2 → qtype 별 `GoldenExample` (id/query/doc_id/expected_summary/must_include/doc_type/caption_dependent)
- `build_prompt(qtype, examples, count)` — `_QTYPE_GUIDANCE` 의 패턴 가이드 + few-shot examples + count 합성 user prompt
- `call_gemini_json(system, user)` — Gemini 2.5 Flash text-gen, `response_mime_type="application/json"` 강제
- `parse_candidates(raw_json, qtype, id_prefix, start_idx)` — JSON array → `CandidateRow` (id 자동 부여 G-Z-XXX, 누락 필드 graceful)
- `write_candidates_csv(path, candidates)` — golden v2 14 컬럼 schema 동일 CSV (utf-8-sig BOM)
- `CandidateRow.to_csv_row()` — `relevant_chunks` / `acceptable_chunks` 빈값 + `negative=false` default

**qtype 가이드** (`_QTYPE_GUIDANCE`):
- `synonym_mismatch`: 정확한 단어 대신 유사어/동의어/일상 표현
- `vision_diagram`: 그림/도표/사진/다이어그램 관련 query
- `fuzzy_memory`: 추측형/구어체 회상
- `cross_doc`: 여러 doc 가로지른 비교/대조

**CLI 옵션**:
- `--qtypes` (default `synonym_mismatch vision_diagram`) — 확장 대상
- `--count-per-qtype` (default 5) — qtype 당 candidate 수
- `--id-prefix` (default `G-Z-`) — 자동 생성 분리용
- `--out` (default `evals/results/golden_v2_candidates.csv`)
- `--dry-run` — prompt 만 출력 후 종료 (cost 0)

### 1.2 신규 — `api/tests/test_generate_golden_rows.py` (10 tests)

검증 범위 (외부 의존성 0 — Gemini 호출 없이 JSON 입력 직접 주입):
- `LoadExamplesTest` (2) — qtype 그룹화 + 빈 id/qtype skip
- `BuildPromptTest` (2) — guidance + examples 포함 + 알 수 없는 qtype graceful
- `ParseCandidatesTest` (5) — JSON parse + id 자동 부여 / invalid JSON raise / non-array raise / 누락 필드 default / start_idx 연속 numbering
- `CsvWriterTest` (1) — CSV round-trip + 14 컬럼 schema 보존

### 1.3 검증

- **tsc**: 0 error (frontend 변경 0)
- **lint**: 0 error
- **unit tests**: **790 → 800 (+10) / OK / skipped=1 / 회귀 0**

---

## 2. 1차 실행 결과 (cost ~$0.02)

```
[load] golden v2 qtype 수: 9
  [synonym_mismatch] examples=4 → request 5 candidates
    ✅ 5 candidates 생성 (15.1s)
  [vision_diagram] examples=6 → request 5 candidates
    ✅ 5 candidates 생성 (11.4s)
[OK] 10 candidates → evals/results/golden_v2_candidates.csv
```

**총 소요**: 26.5s
**총 cost**: ~$0.02 (Gemini 2.5 Flash text-gen, 2 LLM call)

---

## 3. 생성된 candidates (사용자 후속 review)

### 3.1 synonym_mismatch (5건)

| id | doc | query | expected_summary | must_include |
|---|---|---|---|---|
| G-Z-001 | 보건의료_빅데이터_시범사업 | 보건의료 빅데이터에서 개인 프라이버시 지침은 어떻게 되나요? | 개인정보 비식별화 방안 | 비식별화;개인정보 |
| G-Z-002 | 직제_규정 | 직제 규정에서 자산 관리 지침의 수정 사항은 무엇인가요? | (재산관리내규 chunk text) | 다음과;개정한다;경영본부장 |
| G-Z-003 | 한마음생활체육관_운영_내규 | 한마음생활체육관 운영 내규의 적용 시작일과 바뀐 점을 알려주세요. | (chunk text) | 2022년;7월;1일;내규는;시행한다 |
| G-Z-004 | 한마음생활체육관_운영_내규 | 한마음생활체육관 정기권 이용 안내에 대해 알려주세요. | (chunk text) | 1일;1회;2시간;회원카드를;이용료는 |
| G-Z-005 | 보건의료_빅데이터_시범사업 | 보건의료 대규모 자료에서 개인 정보 안전 조치 사항은 뭔가요? | 개인정보 비식별화 방안 | 비식별화;개인정보 |

### 3.2 vision_diagram (5건)

| id | doc | query | must_include |
|---|---|---|---|
| G-Z-006 | sonata-the-edge_catalog | 쏘나타 엣지 외관 디자인 사진 보여줘 | 외관;디자인 |
| G-Z-007 | 데이터센터 통합_안내서 | 데이터센터 사업 지원 절차 그림 있나 | 지원;절차;사업 |
| G-Z-008 | 직제_규정 | 직제규정에 조직도 그림 있어? | 조직도;계층 |
| G-Z-009 | sample-report | 미국 경제성장률 그림은 어디서 볼 수 있어? | 미국;경제성장률 |
| G-Z-010 | 글로벌 인플레이션 | 글로벌 인플레이션 추이 도표 있어? | 인플레이션;추이 |

→ 모두 `caption_dependent="true"` 자동 분류 (vision qtype 정합).

### 3.3 사용자 후속 review 항목

1. **G-Z-001 ↔ G-Z-005 유사 중복** — 둘 다 보건의료 빅데이터 + 개인정보 보호 주제. 둘 중 1건 drop 권고 또는 query 차별화.
2. **expected_answer_summary 일부 = chunk text** (G-Z-002/003/004) — LLM 이 chunks 미참조 상태로 generate → "summary" 자리에 chunk-like text 채움. 사용자 가 실 chunk 매칭 후 정정 필요.
3. **acceptable_chunks 모두 빈값** — 사용자 가 /search 실측 후 chunk_idx 채움 (D4 도구 또는 result-card debug=1 활용).
4. **doc_id 정합성** — LLM 이 few-shot 의 doc_id 재사용 — 검증 (G-A-104~113 류 stale doc_id 재발 방지).

---

## 4. 비판적 한계 (정직 인정)

| # | 한계 | 영향 | 완화 |
|---|---|---|---|
| 1 | **LLM hallucination 위험** | candidate query 가 실제 doc 에 답이 없을 수 있음 | 자동 append 금지 + 사용자 review 강제 (gitignored 출력) |
| 2 | **chunk text 미참조** | expected_answer_summary 부정확 | 사용자가 /search 실측 + 수동 정정 |
| 3 | **doc_id few-shot 재사용** | 새 doc 자동 발견 X — 기존 example 의 doc 만 cover | minority qtype 의 doc 풀 자체 확장 시 별도 sprint |
| 4 | **cross_doc 미지원** | doc_id 빈 U-row + multi-doc 합성 복잡 | 별도 sprint (cross_doc 전용 generator) |
| 5 | **유사 candidate 가능** (G-Z-001 ↔ 005) | 사용자 review 단계에서 dedup 필요 | LLM temperature ↑ (0.7) 했지만 유사 prompt → 유사 출력 발생 |
| 6 | **id_prefix 충돌 가능성** | G-Z-001 이 다음 실행 시 덮어쓰기 | 사용자가 본 골든셋 add 시 id 재할당 (예: G-A-2XX 또는 G-U-2XX) |
| 7 | **must_include 자동 추출 한계** | LLM 의 must_include 추측이 실 chunk 키워드와 안 맞을 수 있음 | 사용자 검증 필요 |

---

## 5. S5 진척률 추이

| 시점 | 진척률 | 잔여 |
|---|---:|---|
| S5 진입 plan 정정 + PoC ship (586c01d) | ~25% | S5-A + S5-B + S5-C |
| S5-A — RouterSignalsBadge ship (908e6a6) | ~40% | S5-B + S5-C |
| S5-C — relevance-label/rrf debug 분기 (25518c7) | ~60% | S5-B |
| S5-B — RAGAS 회귀 자동화 baseline ship (85d7f5b) | ~95% | sample 확장 옵션 |
| **B — golden v2 자동 확장 인프라 ship (현재)** | **100% ✅** | — |

**S5 마감 ✅** — 인프라 + baseline + 임계 + 표본 확장 도구까지 모두 ship.

---

## 6. 사용자 결정 보류 항목 변동

| ID | 항목 | 직전 | 갱신 |
|---|---|---|---|
| Q-S5-5 | RAGAS sample 크기 | 부분 해소 (n=8 baseline) | **부분 해소 유지** — 본 sprint 의 candidates 사용자 review → golden v2 add 후 n=30 재측정 가능 |
| Q-B-review | candidate review 절차 | 신규 | **사용자 수동 review** — `evals/results/golden_v2_candidates.csv` 검토 후 `evals/golden_v2.csv` add (acceptable_chunks 수동 채움 권장) |
| Q-B-cross-doc | cross_doc qtype 자동 생성 | 신규 | **별도 sprint 권고** — multi-doc 합성 prompt + doc 조합 결정 로직 추가 필요 |

---

## 7. 다음 후보 우선순위 (B 단계 마감 → 새 의사결정)

S5 마감 + B 단계 ship 완료. 다음 후보:

### 7.1 1순위 — golden v2 candidates 사용자 review + add (cost 0, 0.5 day)

본 sprint 산출물 (10 candidates) 의 사용자 manual review:
- 유사 candidate dedup (G-Z-001 ↔ 005)
- expected_answer_summary 정정 (G-Z-002/003/004)
- acceptable_chunks 채움 (search 실측)
- id 재할당 (G-A-2XX 또는 G-U-2XX)

→ golden v2 172 → 180~182 row 확장 → S5-B baseline 재측정 가능 (cost ~$0.05).

### 7.2 2순위 — R@10 -0.037 회복 (cost 0, 0.5 day)

cross_doc 잔존 라벨 정정. 단 G-U-018 doc-size bias 구조적 한계.

### 7.3 3순위 — TOC guard 패턴 정밀화 (cost 0, 0.5 day, ★★)

table_lookup -0.083 회귀 회복.

### 7.4 4~10순위

| # | 후보 | 작업량 | cost | 권고도 |
|---|---|---|---|---|
| 4 | cross_doc qtype 자동 생성 (B 후속) | 1 day | ~$0.05 | ★★ |
| 5 | RAGAS n=30 재측정 (golden v2 확장 후) | 0.5 day | ~$0.05 | ★★ |
| 6 | chunk_filter 마킹 false positive 분석 | 0.5 day | 0 | ★ |
| 7 | budget_guard cache hit 무료 인식 | 0.5 day | 0 | ★ |
| 8 | S4-B 핵심 엔티티 추출 | 3 day | 0 | ★★ |
| 9 | RPC per-doc cap (큰 fix) | 1주+ | 0 | ★ |
| 10 | S6 (search inline answer modal) | 1~2주 | 0 | ★ |

---

## 8. 핵심 변경 파일 목록

### 신규
- `evals/generate_golden_rows.py` (310 LOC) — minority qtype AI 자동 확장
- `api/tests/test_generate_golden_rows.py` — 10 unit tests
- 본 work-log

### gitignored (로컬 only, 결과는 본 work-log §3 에 reproduced)
- `evals/results/golden_v2_candidates.csv` — 10 candidates (사용자 review 대기)

### 데이터 영향
- 0 건 (chunks / vision_page_cache / golden_v2.csv 변동 없음. candidates 는 별도 파일)

### 운영 코드 변경 영향
- 0 건 (`api/app/` / `web/src/` 수정 없음)

### 외부 cost
- ~$0.02 (Gemini 2.5 Flash text-gen, 2 LLM call)
- 누적 (이번 세션 전체): ~$0.21 (S5-B $0.19 + B $0.02)

---

## 9. 한 문장 마감

> **2026-05-10 — B 단계 ship + S5 마감 (100%)**. `generate_golden_rows.py` 310 LOC + 10 unit tests + 1차 실행 10 candidates (synonym_mismatch 5 + vision_diagram 5, cost ~$0.02). 자동 append 금지 → 사용자 manual review 후 골든셋 add. 단위 테스트 790 → 800 (+10) / 회귀 0. **S5 진척률 100% ✅** (인프라 + baseline + 임계 + 표본 확장 도구 모두 마감). 다음 1순위 = **candidates 사용자 review + golden v2 add** (cost 0, 0.5 day) → S5-B baseline 재측정 input 확보 가능.
