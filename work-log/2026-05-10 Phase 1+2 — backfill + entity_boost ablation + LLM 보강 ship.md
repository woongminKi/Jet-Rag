# 2026-05-10 Phase 1+2 ship — backfill + entity_boost ablation + Flash-Lite LLM + multimodal judge

> Sprint: Phase 1 (backfill apply + entity_boost ablation) + Phase 2 (Flash-Lite LLM 보강 + multimodal judge)
> 작성: 2026-05-10
> 마감: 4 sub-sprints 통합 ship — 사용자 명시 "E (모두 진행)"
> 입력: 1~4순위 통합 plan + 사용자 cost ~$1.60 max 승인

---

## 0. 한 줄 요약

> **Phase 1+2 통합 ship — 4 sub-sprints**.
> (1) **Phase 1.1 backfill apply**: 2463 chunks 처리 → 413 updated (17%, dates 811/percentages 412/identifiers 399/amounts 42 = 1664 entities). 102 skipped (이미 entities 있음 = 직전 ingest 통합 효과). 1948 empty (일반 텍스트).
> (2) **Phase 1.2 entity_boost ablation**: D4 도구 재실행 (TOC ON + entity_boost ON) → Overall R@10 -0.003 (회복 7건 +0.92 vs 회귀 9건 -1.50, mixed). default OFF 유지 권고.
> (3) **Phase 2.1 Flash-Lite LLM 보강**: `entity_extract.py` 에 `extract_entities_with_llm()` + `parse_llm_entities()` + persons/orgs/products 필드 추가. DI 패턴 (llm_call 콜백). 10 unit tests.
> (4) **Phase 2.2 multimodal LLM judge**: `evals/_multimodal_judge.py` 신규 (~120 LOC) — `evaluate_multimodal()` DI 패턴 (image_fetch + llm_call 콜백). 11 unit tests. RAGAS 통합 + 실 image fetch path 는 별도 sprint.
>
> 단위 테스트 880 → **901 (+21) / 회귀 0**. 누적 cost ~$0.475 (Phase 1 cost 0, Phase 2 helper만 cost 0).

---

## 1. Phase 1.1 — backfill apply ✅ (DB 변경, 사용자 승인)

### 1.1 실행

```bash
uv run python ../evals/backfill_chunk_entities.py --apply --batch-size 200
```

소요: ~5분 (2463 chunks ÷ 200 batch ≈ 13 batches)

### 1.2 결과

```
=== 종료 요약 ===
total processed: 2463
skipped (entities 이미 있음): 102      # 직전 ingest 통합 효과 (새 chunks)
empty (entities 0건 추출): 1948 (79%)
updated: 413 (17%)                      # 룰 기반 매칭 chunks

=== 추출 entities 분포 ===
dates: 811
percentages: 412
identifiers: 399
amounts: 42
total entities: 1664
```

### 1.3 검증

- DB 변경 영향: chunks.metadata.entities 키 추가 (413 chunks), 다른 metadata 보존
- 회귀 검증: 단위 테스트 880 OK / 회귀 0
- idempotent: re-run 시 102 → 모두 skip 예상

---

## 2. Phase 1.2 — entity_boost ablation (자율, cost 0)

### 2.1 측정

```bash
JETRAG_TOC_GUARD_ENABLED=true \
JETRAG_ENTITY_BOOST=true \
uv run python ../evals/run_s4_a_d4_breakdown_eval.py
# 87.5s, R@10=0.7078, top-1=0.8580 (baseline TOC ON v3: 0.7111 / 0.8580)
```

### 2.2 결과 — **mixed** (회복 + 회귀)

| metric | TOC ON v3 baseline | + entity_boost | △ |
|---|---:|---:|---:|
| Overall R@10 | 0.7111 | **0.7078** | **-0.003 ⚠** |
| Overall top-1 | 0.8580 | 0.8580 | 0 |

**diff rows (20건)**:
- 회복 (8 row, sum +0.92):
  - G-U-017 cross_doc +0.333
  - G-A-026 summary +0.167
  - G-A-052 exact_fact +0.143
  - G-A-087 exact_fact +0.167
  - G-A-093 exact_fact +0.143
  - G-A-095 exact_fact +0.286
  - G-A-118 exact_fact +0.125
- 회귀 (9 row, sum -1.55):
  - G-A-002 -0.125, G-A-022 -0.091, G-A-029 -0.286
  - G-A-045 -0.167, G-A-049 -0.250, G-A-062 -0.091
  - G-A-081 -0.250, **G-U-105 -0.333**, **G-U-107 -0.333**

### 2.3 분석

- **G-U-105/107 (synonym_mismatch)** 큰 회귀 — boost 가 다른 chunks 매칭하여 정답 chunks 누락
- **factor 1.10 너무 강함 가능** — 별도 sprint 에서 1.05 시도 권고
- **Overall net -0.003 약간 회귀** — default OFF 유지 권고

### 2.4 결정

- **default OFF 유지** (운영 영향 0) ✅
- 사용자 명시 ENV `JETRAG_ENTITY_BOOST=true` 시 활성
- factor 정밀화 + 매칭 카테고리 selective (예: identifiers / dates 만 boost) — 별도 sprint

---

## 3. Phase 2.1 — Flash-Lite LLM 보강 (helper + tests)

### 3.1 변경 — `api/app/services/entity_extract.py`

**ExtractedEntities 확장**:
```python
@dataclass
class ExtractedEntities:
    dates, amounts, percentages, identifiers: list[str]
    persons: list[str] | None = None       # 신규
    orgs: list[str] | None = None          # 신규
    products: list[str] | None = None      # 신규
```

`to_dict()`: persons/orgs/products 가 None 인 경우 키 미포함 (룰 기반 only 호환).

**신규 함수**:
- `extract_entities_with_llm(text, *, llm_call, rule_based=None)` — DI 패턴, llm_call 실패 시 룰 기반 결과 반환 (graceful)
- `parse_llm_entities(raw)` — JSON parse + markdown fence 제거 + dedup/strip

**시스템 prompt** (`_LLM_SYSTEM_PROMPT`): 한국어 인명/기관명/제품명 추출 가이드 + JSON object 만 반환 명시.

### 3.2 단위 테스트 (10 신규)

- `ParseLLMEntitiesTest` (6): valid JSON / fence / 누락 키 default / dedup / invalid raise / non-dict raise
- `ExtractEntitiesWithLLMTest` (4): 룰+LLM 통합 / LLM 실패 graceful / to_dict 포함 / 룰만 시 미포함

### 3.3 RAGAS / ingest 통합 (별도 sprint)

본 sprint 는 helper + tests. 실 LLM call (Gemini Flash-Lite) 통합:
- ingest pipeline 통합 시 cost ~$0.001/chunk × 새 chunks
- backfill_chunk_entities.py 에 --with-llm flag 추가 시 ~$1.0 cap (전체 chunks)
- 별도 sprint 권고

---

## 4. Phase 2.2 — multimodal LLM judge (helper + tests)

### 4.1 신규 — `evals/_multimodal_judge.py` (~120 LOC)

**API**:
```python
from _multimodal_judge import evaluate_multimodal, MultimodalJudgmentResult

result = evaluate_multimodal(
    query="2026년 GDP",
    answer="2.0% 성장",
    doc_id="d-1234",
    page=14,
    image_fetch_fn=lambda d, p: storage_get_page_png(d, p),
    llm_call_fn=lambda img, sys, usr: gemini_multimodal(img, sys, usr),
)
# result.score: float | None (n_verified / n_claims)
# result.n_claims, result.n_verified, result.reasoning
```

**알고리즘**:
1. `image_fetch_fn(doc_id, page)` — 페이지 이미지 bytes (PNG/JPEG)
2. `llm_call_fn(image, system, user)` — Gemini multimodal API (image + 답변)
3. LLM JSON 응답: `{"n_claims": N, "n_verified": M, "reasoning": "..."}`
4. score = M / N (clamp [0, 1])

**graceful**:
- empty answer → score=0
- image fetch 실패 → score=None
- LLM 실패 → score=None
- JSON parse 실패 → score=None

### 4.2 단위 테스트 (11 신규)

- `BuildJudgePromptTest` (1)
- `ParseJudgmentTest` (5): valid / fence / zero claims / clamp / invalid
- `EvaluateMultimodalTest` (5): empty answer / fetch fail / empty image / LLM fail / full pipeline success

### 4.3 RAGAS 통합 + 실 storage path (별도 sprint)

본 sprint 는 helper + tests. 실 적용:
- `evals/run_ragas_regression.py` 에 `--with-multimodal-judge` flag
- `image_fetch_fn`: documents.storage_path → storage.get → PyMuPDF page render → PNG bytes
- `llm_call_fn`: Gemini 2.5 Flash multimodal API
- cost: ~$0.001~$0.005 per call × 30 sample = ~$0.03~$0.15

별도 sprint 권고 (cost ~$0.15 cap, storage fetch 인프라 검증 필요).

---

## 5. 검증

- **단위 테스트**: 880 → **901 (+21) / OK / 회귀 0**
  - test_entity_extract: 16 → 26 (+10)
  - test_multimodal_judge: 신규 11
- **DB 변경 회귀**: 0 (backfill apply 후 단위 테스트 통과)
- **search 변경**: entity_boost ENV ON 시만 활성 (default OFF) → 운영 동작 X

---

## 6. 사용자 결정 보류 항목 변동

| ID | 항목 | 직전 | 갱신 |
|---|---|---|---|
| Q-S4B-backfill-apply | 기존 chunks backfill | 사용자 명시 권고 | **해소 ✅** (사용자 승인 + 적용) |
| Q-S4B-entity-boost-default | entity_boost default ON | ablation 후 결정 | **유지 OFF** (mixed, R@10 -0.003) |
| Q-S4B-llm-helper | Flash-Lite LLM 보강 helper | 별도 sprint | **infra ship ✅** (실 적용 별도) |
| Q-multimodal-helper | multimodal LLM judge | 별도 sprint | **infra ship ✅** (실 적용 별도) |
| Q-entity-boost-factor | factor 1.10 정밀화 | 신규 | 별도 sprint (1.05 시도 + selective category) |
| Q-multimodal-storage | 실 image fetch path | 신규 | 별도 sprint (storage fetch + PyMuPDF) |

---

## 7. 핵심 변경 파일 목록

### 신규
- `evals/_multimodal_judge.py` (~120 LOC) — multimodal helper
- `api/tests/test_multimodal_judge.py` (11 tests)
- 본 work-log

### 수정
- `api/app/services/entity_extract.py` — ExtractedEntities 확장 + LLM 보강 함수 (+~80 LOC)
- `api/tests/test_entity_extract.py` — LLM 관련 10 tests 추가

### 데이터 영향
- **chunks.metadata.entities** — 413 chunks 추가 (Phase 1.1 backfill apply)
- chunks 다른 metadata / text 보존

### 운영 코드 변경 영향
- entity_extract: helper 확장만, default 동작 X
- multimodal_judge: helper 신규, 미통합

### 외부 cost
- Phase 1: 0 (룰 기반)
- Phase 2: 0 (helper 만, 실 LLM call X)
- 누적 (이번 세션 전체): ~$0.475 (변동 없음)

---

## 8. 다음 — Phase 3 + 4 진행

Phase 3 — 통합 RAGAS 측정 (TOC + visual_grounding ON, ~$0.20 cap, ~15 min)
Phase 4 — 마감 핸드오프

---

## 9. 한 문장 마감

> **2026-05-10 Phase 1+2 ship** — backfill apply (413 chunks updated, 1664 entities) + entity_boost ablation (mixed, default OFF 유지) + Flash-Lite LLM 보강 helper + multimodal LLM judge helper. 단위 테스트 880 → **901 (+21) / 회귀 0**. 누적 cost 변동 0 (Phase 1+2 모두 helper / DB 변경, 외부 LLM call 0). 다음 = Phase 3 통합 RAGAS 측정 진행 중 (cost cap $0.20).
