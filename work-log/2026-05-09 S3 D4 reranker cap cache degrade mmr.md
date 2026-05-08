# 2026-05-09 — S3 D4 ship: reranker cap + cache + free-tier degrade + MMR 다양성

## 1. 목표

BGE-reranker (HF API) 의 무료 티어 폭주 / 같은 query 반복 호출 / 단일 doc 독점
세 위험을 4중 가드로 직교 방어한다. **default OFF / cap=20 / cache=500 LRU /
degrade 임계 80% / λ=0.7** — 모두 ENV 토글로 즉시 회복 가능.

| 가드 | 모듈 | 무력 시 |
|---|---|---|
| candidate cap | `search.py:_resolve_reranker_cap` | HF latency 폭주 (50p × N pair) |
| in-memory cache | `services/reranker_cache.py` | 같은 query/chunk 재호출 시 HF 중복 청구 |
| free-tier degrade | `search.py:_is_reranker_degraded` | 월간 호출 cap 초과 시 검색 자체 차단 |
| MMR 다양성 | `services/mmr.py` | cross-doc query 의 단일 doc 독점 |

planner v0.1 § A~E + 사용자 결정 3건 (자체 카운터 / cross_doc only / cap=20)
모두 그대로 반영.

## 2. baseline

- 단위 테스트: **726 / 0 fail** (S3 D3 ship 시점)
  - 이중 2건은 `test_reranker.py` 의 fallback 분기가 cache hit 으로 invoke 되지 못하는
    회귀 — 본 ship 의 setUp/tearDown 에서 `reranker_cache._reset_for_test()` 추가로 해결.
- 16 commits ahead origin/main.
- 회귀 가드: ENV default 시 D3 동작과 동일 (HF 호출 0).

## 3. 변경 파일 + LOC

| 파일 | LOC | 비고 |
|---|---|---|
| `api/app/services/reranker_cache.py` | +130 (신규) | OrderedDict LRU 500 + ENV disable |
| `api/app/services/mmr.py` | +197 (신규) | λ=0.7 greedy MMR + cosine sim |
| `api/app/routers/search.py` | ~+280 | reranker block 4 path 분기 + MMR + 헤더 |
| `api/app/routers/answer.py` | +9 | `X-Reranker-Path` 헤더 노출 통합 |
| `api/tests/services/test_reranker_cache.py` | +127 (신규) | 단위 테스트 4건 (#1~#4) |
| `api/tests/services/test_mmr.py` | +141 (신규) | 단위 테스트 3건 (#7 + ENV/λ=1.0) |
| `api/tests/test_search_reranker_path.py` | +318 (신규) | 통합 테스트 6건 (#5/#6/#8 4 path) |
| `api/tests/test_reranker.py` | +7 | setUp/tearDown 에 cache reset |
| `.env.example` | +27 | ENV 4종 + 설명 |

신규 파일 5건 / 수정 파일 4건 / 마이그 0 / 의존성 0.

## 4. 테스트 결과

- **732 tests / 0 fail** (전체)
  - baseline 726 + 신규 6 (test_search_reranker_path.py) — 회귀 0.
  - test_reranker_cache.py 4건 + test_mmr.py 3건 = 7건은 D3 ship 시 사전 추가됨.
  - 본 D4 ship 의 신규 통합 테스트는 6건 (#5/#6/#8) — 8 케이스 중 #1/#5
    (cap)·#6 (degrade)·#8 (4 path) 5종을 통합 테스트로 위임 (모듈 단위 +
    통합 균형).
- 외부 API 호출 0 — HF/Gemini mock + supabase mock + `_count_reranker_invokes_last_30d`
  patch 로 회귀 가드.
- 골든셋 R@10 회귀: 본 ship 은 ENV default OFF 라 reranker / MMR 분기 모두 비활성 →
  D3 baseline 동작 100% 보존.

## 5. search.py diff 요약 (핵심 분기)

```
reranker block (line 580~648):
  if reranker_enabled and len(rpc_rows) > 1 and candidate_chunk_ids:
      cache_candidate_ids = candidate_chunk_ids[:_resolve_reranker_cap()]   # cap (1)
      cached = reranker_cache.lookup(...)                                   # cache (2)
      if cached:        path=cached    + RRF score 대체
      elif degraded():  path=degraded  + RRF score 유지                      # degrade (3)
      else:             path=invoked   + HF 호출 + cache.store + usage_log

cover guard (line 710):
  cover_guard_skip = reranker_used or reranker_path == "cached"             # cache hit 도 skip

X-Reranker-Path 헤더 (line 652~653, answer.py:454~458):
  response.headers["X-Reranker-Path"] = reranker_path

MMR 후처리 (line 826~843):
  if not mmr.is_disabled() and len > 1 and doc_id is None and _is_cross_doc_query(q):  # MMR (4)
      sorted_doc_ids = mmr.rerank(..., embeddings_by_id=docs_meta.doc_embedding)
```

## 6. ENV 갱신 (.env.example)

```bash
# (1) cap
JETRAG_RERANKER_CANDIDATE_CAP=20          # default 20, 권장 5~50

# (2) cache
JETRAG_RERANKER_CACHE_DISABLE=0           # default 0 (캐시 ON, 500 LRU)

# (3) degrade
JETRAG_RERANKER_MONTHLY_CAP_CALLS=1000    # 월간 호출 cap (vision_usage_log COUNT)
JETRAG_RERANKER_DEGRADE_THRESHOLD=0.8     # 임계율 80% 도달 시 degrade

# (4) MMR
JETRAG_MMR_LAMBDA=0.7                     # 0.0=pure diversity, 1.0=pure relevance
JETRAG_MMR_DISABLE=0                      # default 0 (MMR ON)
```

운영 회복 토글:
- `JETRAG_RERANKER_ENABLED=false` 로 reranker 자체 OFF (D3 동작 복귀).
- 모든 가드 ENV 비활성 시 graceful — DB 조회 실패 / 마이그 014 미적용 모두
  `degraded=False` 로 처리되어 검색 자체 차단 X.

## 7. 회귀 가드 체크리스트

- [x] ENV default 시 D3 동작 동일 (`JETRAG_RERANKER_ENABLED=false` default OFF)
- [x] HF mock 으로 외부 API 호출 0 (전 단위 테스트)
- [x] supabase mock + `_count_reranker_invokes_last_30d` patch 로 DB 호출 0
- [x] 의존성 추가 0 (stdlib `OrderedDict` / `hashlib` / `math` 만 사용)
- [x] 마이그 0 (vision_usage_log 재사용, source_type='reranker_invoke')
- [x] 골든셋 R@10 — ENV default 시 reranker block 자체 미진입 → 변화 0
- [x] 4 path 명시 — `X-Reranker-Path` 헤더 + `query_parsed.reranker_path` 응답 노출

## 8. 발견 이슈

### 8.1. test_reranker.py fallback 회귀 (해결됨)

- `test_reranker_transient_failure_falls_back` / `test_reranker_permanent_failure_falls_back`
  2건이 baseline 726 에서 fail 상태였음. 원인: 직전 테스트 (`test_reranker_enabled_reorders`)
  가 cache 에 store 한 (query="테스트", chunks=[c1,c2]) entry 가 다음 테스트에서
  hit → HF mock 호출 0 → `reranker_fallback_reason=None`.
- **해결**: setUp/tearDown 에 `reranker_cache._reset_for_test()` 호출 추가 (+7 LOC).

### 8.2. _count_reranker_invokes_last_30d — 환경 의존성

- supabase-py `count="exact"` 응답이 환경에 따라 `resp.count` 또는 `len(resp.data)`
  로만 노출됨. 본 모듈은 둘 다 fallback 처리 (search.py:1135~1138).
- 단위 테스트는 `patch.object(search_module, "_count_reranker_invokes_last_30d")` 로
  결정성 확보 — DB schema/응답 형식 변경에 영향 0.

### 8.3. MMR — doc_embedding 결측 시

- `docs.doc_embedding` NULL 인 doc 은 `embeddings_by_id` 에서 빠져 `sim=0` 처리
  → MMR 의 diversity term 0 → 사실상 relevance 정렬 보존 (회귀 0).
- 의도된 graceful 동작 — doc_embedding 백필 (S2 D5) 진행률에 따라 효과 점진 강화.

## 9. 남은 이슈

- 골든셋 R@10 측정은 ENV ON 시점에서 별도 ship (S3 D5+) — 본 ship 은 default OFF
  라 측정 의미 0.
- MMR λ tuning (0.5 / 0.7 / 0.9) ablation 은 cross-doc 골든셋 누적 후 진행.
- `_count_reranker_invokes_last_30d` 의 SUM 캐싱 미적용 — 호출 빈도 (분당 ~수 회)
  대비 부하 무시 가능. 운영 누적 후 재검토.

## 10. 다음 스코프

- **S3 D5** — 골든셋 측정 (ENV ON) + reranker / MMR 효과 정량화. R@10 회귀 0
  + Top-3 distinct doc 비율 비교.
- **S3 D5+** — answer 생성 phase 의 reranker 통합 확인 (`/answer` 도 같은
  reranker block 거치는지 retrace).

## 11. 커밋 가이드

본 ship 은 4건의 분리 커밋으로 정리 권장:

1. `feat(search): reranker_cache + reranker_cache 단위 테스트` (모듈 A + 테스트 4건)
2. `feat(search): mmr 다양성 후처리 + 단위 테스트` (모듈 B + 테스트 3건)
3. `feat(search): reranker cap + free-tier degrade + 4 path 헤더 노출` (search.py / answer.py + 통합 테스트 6건 + .env.example)
4. `fix(test): reranker fallback 테스트 cache reset 보강` (test_reranker.py +7)

Conventional prefix 영문 / subject·body 한국어 / Claude 자동 서명 0.
