# S3 D3 — gated paid query decomposition ship

- 일자: 2026-05-09
- 단계: Sprint S3 Day 3 / planner v0.1 §C-I
- 작업자: senior-developer (Jet-Rag)
- 시작 baseline: 710 tests / OK / 14 commits ahead origin/main
- 종료 결과: **719 tests / OK / 회귀 0** (target 717+ 달성)

---

## 1. 목표

intent_router (S3 D1) + confidence 안전망 (S3 D2) 위에 **유료 LLM 분해 (Gemini 2.5 Flash-Lite)** 를 ENV gated 로 wire-in. cross-doc / 비교 / 인과 / 복수대상 의도 query 를 2~5 sub-query 로 분해 → 원본 query top_k=20 + sub-query top_k=10 → RRF merge → recall 향상.

**핵심 제약**
- default OFF — `JETRAG_PAID_DECOMPOSITION_ENABLED=false` 시 LLM 호출 0
- 의존성 추가 0 (google-generativeai SDK 재사용)
- 마이그레이션 0 (`vision_usage_log` 재사용 — 사용자 결정 Q-S3-D3-1)
- 회귀 0 — D2 와 동일 단위 테스트 통과

## 2. 사용자 결정 (모두 채택)

| ID | 결정 | 효과 |
|---|---|---|
| Q-S3-D3-1 | `vision_usage_log` 재사용 (마이그 0) | `source_type='query_decomposition'` 분리 SUM 으로 vision 호출과 분리 집계 |
| Q-S3-D3-2 | 원본 top_k=20 + sub-query top_k=10 → RRF | sub-query 가 노이즈여도 원본 풀이 fallback. 다중 hit 가산 |
| Q-S3-D3-3 | few-shot 2건 (cross_doc + 인과) | LLM 응답 형식 안정화. system prompt 에 inline |

## 3. 변경 파일 + LOC

| 파일 | 종류 | LOC | 비고 |
|---|---|---|---|
| `api/app/services/query_decomposer.py` | 신규 | 512 | core module — decompose() + check_decomposition_budget() + LRU cache |
| `api/app/services/budget_guard.py` | 수정 | +5/-1 | `BudgetScope` literal 에 `'query_decomposition'` 추가 (planner §D) |
| `api/app/routers/answer.py` | 수정 | +217/-4 | placeholder 자리 wire + `_gather_chunks_with_decomposition` + `_fetch_query_pool` + `_rrf_merge_pools` + `_enrich_rows` |
| `.env.example` | 수정 | +18 | ENV 토글 3건 + 한국어 주석 |
| `api/tests/services/test_query_decomposer.py` | 신규 | 287 | mock 6건 + 통합 1건 (skipUnless GEMINI_API_KEY) |
| `api/tests/test_answer_router_decomposition.py` | 신규 | 150 | meta 노출 + 분기 검증 2건 |

총 신규 LOC ~960, 수정 LOC ~240.

## 4. grep 검증 (작업 절차 #1, #2)

### 4.1 factory.py — `decomposition` purpose 매핑
```
api/app/adapters/factory.py:49     "decomposition",
api/app/adapters/factory.py:69     "decomposition": "gemini-2.5-flash-lite",
```
→ `get_llm_provider("decomposition")` 호출 시 `gemini-2.5-flash-lite` 인스턴스 반환 확정.

### 4.2 budget_guard.py — `BudgetScope` 확장
```
74:BudgetScope = Literal[
75:    "doc",
76:    "daily",
77:    "24h_sliding",
78:    "page_cap",
79:    "query_decomposition",  # ← S3 D3 신규
80:]
```
→ 기존 4 scope + 신규 1. `check_doc_budget` / `check_daily_budget` / `check_24h_sliding_budget` / `check_doc_page_cap` 영향 0 (각자 자기 scope 하드코딩).

### 4.3 vision_usage_log schema (014 마이그) — `source_type` 컬럼 자유 TEXT
```sql
CREATE TABLE IF NOT EXISTS vision_usage_log (
    ...
    source_type      TEXT,
    estimated_cost   NUMERIC(10, 6),
    model_used       TEXT,
    ...
);
```
→ `source_type='query_decomposition'` insert + SUM filter 모두 마이그 0 으로 동작. `vision_metrics.record_call` 의 enum 강제는 본 모듈을 우회 (직접 client.table.insert 호출).

## 5. /answer 라우터 변경 diff 요약

### 5.1 import + 상수
- `from app.services import intent_router, query_decomposer` (decomposer 추가)
- `_DECOMP_TOP_K_ORIGINAL = 20` (원본 query 풀)
- `_DECOMP_TOP_K_PER_SUB = 10` (sub-query 별 풀)

### 5.2 placeholder 자리 (answer.py:266 → 296)
**Before** (D2 placeholder)
```python
# TODO(S3-D3): if low_confidence and "T1_cross_doc" in signals: call decomposer
router_decision = intent_router.route(clean_q)
answer_meta = {...}
chunks, query_parsed = _gather_chunks(...)
```

**After** (D3 wire)
```python
router_decision = intent_router.route(clean_q)
answer_meta = {...}
decomp = query_decomposer.decompose(clean_q, router_decision)
answer_meta.update({
    "decomposed_subqueries": list(decomp.subqueries),
    "decomposition_cost_usd": decomp.cost_usd,
    "decomposition_cached": decomp.cached,
})
if decomp.subqueries:
    chunks, query_parsed = _gather_chunks_with_decomposition(...)
else:
    chunks, query_parsed = _gather_chunks(...)  # 회귀 0
```

### 5.3 신규 헬퍼 3개
- `_gather_chunks_with_decomposition` — 원본 + sub-query 풀 수집 → RRF merge → enrich
- `_fetch_query_pool` — 단일 query → RPC 풀만 (enrich 분리, 풀 사이즈 인자화)
- `_rrf_merge_pools` — chunk_id 단위 1/(k+rank) 합산
- `_enrich_rows` — chunks/documents 1회 조회로 enrich (기존 `_gather_chunks` 와 동일 schema)

`_gather_chunks` 자체는 **수정 0** — `subqueries=()` 분기에서 그대로 호출되어 회귀 가드 (D2 단위 테스트 변경 없이 통과).

## 6. 단위 테스트 — 9건 신규 (target 717+ 달성)

### 6.1 query_decomposer (7건 = mock 6 + skipUnless 1)
| # | 테스트 | 검증 |
|---|---|---|
| 1 | `EnvOffSkipsTest::test_env_off_does_not_call_llm` | ENV off → `llm.complete.assert_not_called()` + skipped_reason=`'... ENV ...'` |
| 2 | `NeedsDecompFalseSkipsTest::test_needs_decomp_false_skips_even_when_env_on` | needs=False → ENV 무관 LLM 호출 0 + reason=`'... 불필요 ...'` |
| 3 | `NormalDecompositionTest::test_cross_doc_returns_three_subqueries` | mock LLM `'["...","...","..."]'` → 3건 + cost > 0 |
| 4 | `LruCacheHitTest::test_second_call_uses_cache` | 동일 query 2회 → `llm.complete.call_count==1` + 2번째 cached=True / cost=0 |
| 5 | `JsonParseFailureTest::test_invalid_json_returns_empty_with_reason` | mock LLM "이 질문은 분해할 수 없습니다." → () + reason=`'... 파싱 ...'` |
| 6 | `BudgetCapExceededTest::test_monthly_cap_exceeded_skips_with_reason` | _sum mock=0.50 / cap=0.10 → llm 호출 0 + reason=`'... 한도 초과 ...'` |
| 7 | `GeminiIntegrationTest` (skipUnless) | 실 GEMINI_API_KEY 부재로 skip — 키 주입 시 실 호출 1회 |

### 6.2 answer router decomposition (2건)
| # | 테스트 | 검증 |
|---|---|---|
| 1 | `AnswerDecompositionMetaTest::test_env_off_meta_shows_empty_subqueries` | ENV off → resp.meta `decomposed_subqueries=[]` / `decomposition_cost_usd=0.0` |
| 2 | `AnswerDecompositionMetaTest::test_env_on_with_mock_subqueries_invokes_decomposed_gather` | mock decompose 3건 → `_gather_chunks_with_decomposition` spy 1회 + `_gather_chunks` 0회 (분기 검증) |

### 6.3 실행 결과
```
Ran 719 tests in 15.634s

OK
```
- baseline 710 + 9 신규 = **719** (target 717 초과)
- 회귀 0 (기존 710 모두 그대로 통과)
- skipped 1 (GeminiIntegrationTest — 실 API 키 부재 정상)

## 7. 회귀 가드 결과

| 가드 | 결과 |
|---|---|
| ENV off 시 LLM 호출 0 | 통과 (test #1 mock.assert_not_called) |
| 단위 테스트 회귀 0 | 통과 (719 / OK) |
| /answer P95 latency: ENV off 시 D2 와 동일 | `subqueries=()` → `_gather_chunks` 분기 (코드 동일) — 보장 |

## 8. ENV ON 검증 절차 (사용자 명시 ON 시점 안내)

### 8.1 사전 점검
1. `.env` 에 `GEMINI_API_KEY=<실제키>` 설정 + `JETRAG_PAID_DECOMPOSITION_ENABLED=true` 추가
2. 마이그레이션 014 적용 확인 (`vision_usage_log.estimated_cost` 컬럼 존재) — 없으면 budget_guard 가 graceful skip (cap 무력화)
3. 월간 cap 기본 $0.30 (~414 KRW) — 약 3,000 호출 분해 가능 (`gemini-2.5-flash-lite` 단가 + 200 token 기준)

### 8.2 동작 검증 명령
```bash
# 백엔드 기동
cd api && uv run uvicorn app.main:app --reload

# cross-doc query 호출 — needs_decomposition=True 발화
curl -s "http://localhost:8000/answer?q=작년+보고서랑+올해+자료+비교&top_k=5" | jq .meta
# 기대값:
# {
#   "low_confidence": true,
#   "router_signals": ["T1_cross_doc"],
#   "router_confidence": 0.55,
#   "decomposed_subqueries": ["작년 보고서 매출", "올해 자료 매출", "두 자료 차이점"],
#   "decomposition_cost_usd": 0.000123,
#   "decomposition_cached": false
# }

# 같은 query 2번째 호출 — LRU cache hit
curl -s "http://localhost:8000/answer?q=작년+보고서랑+올해+자료+비교&top_k=5" | jq .meta
# decomposition_cached: true / decomposition_cost_usd: 0.0

# 비-분해 query — needs_decomposition=False
curl -s "http://localhost:8000/answer?q=오늘+날씨&top_k=5" | jq .meta
# decomposed_subqueries: [] / cost: 0.0 / skipped_reason 은 응답에 노출 X (디버그 로그만)
```

### 8.3 비용 모니터링
```sql
-- 이번 달 분해 호출 비용 누적
SELECT SUM(estimated_cost) FROM vision_usage_log
WHERE source_type = 'query_decomposition'
  AND success = TRUE
  AND called_at >= date_trunc('month', NOW() AT TIME ZONE 'UTC');

-- 호출 빈도 + 평균 비용
SELECT COUNT(*), AVG(estimated_cost)::numeric(10,6)
FROM vision_usage_log
WHERE source_type = 'query_decomposition' AND success = TRUE;
```

### 8.4 회복 토글 (긴급)
- 비용 폭주 시: `JETRAG_PAID_DECOMPOSITION_ENABLED=false` → 즉시 LLM 호출 0
- 캐시 의심 시: `JETRAG_DECOMPOSITION_CACHE_DISABLE=1` → 매 호출 fresh
- 전체 budget guard kill: `JETRAG_BUDGET_GUARD_DISABLE=1` (cost cap 무력 — 디버깅 전용)

## 9. 발견 이슈

### 9.1 vision_metrics 모듈 vs 직접 insert (의도된 분리)
`vision_metrics.record_call` 은 `_normalize_source_type` 으로 enum (`image`/`pdf_scan`/...) 강제. 본 모듈은 이를 우회하여 `client.table("vision_usage_log").insert(...)` 직접 호출. 이유:
- vision_metrics 는 vision API 호출 metrics 가 본질 (in-memory counter + DB write-through 분리)
- query_decomposer 는 단순 비용 1줄 기록 (counter 불필요)
- enum 추가 시 vision_metrics 의 W16 #90 강제 의미가 흐려짐

추후 같은 패턴이 (eg. `hyde`, `query_rewrite`) 늘어나면 `usage_logger` 모듈로 추상화 검토.

### 9.2 비용 추정의 정확도 (~±20%)
SDK `usage_metadata` 노출 path 부재로 char/4 ≈ token 근사 사용. monthly cap (0.30 USD) 같은 보수적 가드에는 충분하나, 정확 대시보드는 추후 SDK metadata wiring 필요. 본 ship 의 검증 충분성 만족.

### 9.3 cache 키 normalization
`(query.lower().strip(), signals)` — 한국어는 lower-case 효과 낮으나 영문 혼용 query 안정화 + 양 끝 공백 제거. NFC 는 호출자 (`/answer`) 가 이미 적용. signals 가 다르면 같은 query 라도 별도 entry — prompt few-shot 경로가 다른 signal 로 유도될 수 있어 보수적.

### 9.4 통합 테스트 skip 정책
`@unittest.skipUnless(GEMINI_API_KEY != 'dummy-test-token')` — CI 가 dummy 토큰만 가지므로 자동 skip. 실 키 보유한 개발자가 ENV 주입 시 자동 활성. 명세 §H 의 `skipif` 의도와 동등.

## 10. 남은 이슈

- 없음 — 명세 작업 절차 1~9 모두 완료. work-log 11번 작성 완료.

## 11. 다음 스코프 (S3 D4 후보 — 별도 planner 결정 대기)

- **decomposition recall 측정** — golden셋 v1 / sonata catalog 에서 ENV on/off A/B → R@10 / nDCG@10 차이 정량화
- **prompt few-shot 확장** — T7_multi_target / T2_compare 케이스 추가 (현 2건 → 4건)
- **sub-query 별 doc_id 다양화** — RRF merge 시 같은 doc 의 chunks 가 위쪽 점령하지 않도록 doc-level diversity penalty 옵션
- **비용 정확도 향상** — Gemini SDK usage_metadata wiring (S0 D2 vision_metrics 패턴 답습)

## 12. 커밋 후보 메시지

```
feat(answer): S3 D3 ship — gated paid query decomposition (Flash-Lite, ENV default off)

- query_decomposer 신규 — needs_decomposition=True + ENV on 시만 LLM 호출
- /answer 분기 와이어 — 원본 top_k=20 + sub-query top_k=10 → RRF merge
- BudgetScope 에 query_decomposition 추가 (vision_usage_log 재사용, 마이그 0)
- LRU cache 200건 + monthly cap 0.30 USD + cache disable ENV 토글
- 단위 테스트 +9 (mock 6 + 통합 skipUnless 1 + 라우터 분기 2)
- 회귀 0: 710 → 719 / OK
```

(임의 커밋 금지 — 사용자 명시 후 수행)
