# 2026-05-05 W25 D14+1 — S2 BGE-reranker-v2-m3 cross-encoder ship

> 검색 성능 향상 plan §S2 (P1) — RRF top-50 → reranker → top-10 재정렬. opt-in default off.
> 다음 세션 진입 핸드오프 §5.1 의 "Reranker (BGE-reranker-v2-m3) +8~12pp" 후보를 사용자 결정으로 진입.

---

## 0. 한 줄 요약

W25 D14 종합 핸드오프 §5.1 의 검색 성능 향상 plan 중 **S2 (P1) Reranker** 를 ship.
사용자 결정: "S2 빠른 체감 먼저" + "(a) HF API 배포".
**HF Inference API + BAAI/bge-reranker-v2-m3 + sentence-similarity pipeline** 로 BGE-M3 패턴 일관 재사용.
**362 tests OK (+15건, 회귀 0)**, opt-in ENV `JETRAG_RERANKER_ENABLED=true` default off.

---

## 1. 결정 근거 (자율 비판적 재검토 3회)

### 1.1 1차 권고 — S2 (P1) Reranker 직접 도입
- 한국어 RAG 표준 패턴 (검증된 회귀 가능성 낮음)
- 사용자 직접 검색 시 top-1/top-3 정성 체감 즉시
- 측정 정량은 S1 골든셋 없으면 불가 (정성 체감만, 정직 인정)

### 1.2 2차 누락 옵션 검토
- (d) Cohere Rerank API ($1/1k) — 비용 고정 / 한국어 multilingual-v3
- (e) Voyage AI rerank — 비슷
- (f) Gemini LLM-as-reranker — 비용 高
- (g) bge-reranker-base (가벼움, 한국어 약)
→ BGE-reranker-v2-m3 + HF API 가 비용 0 / 한국어 강함 / 패턴 일관

### 1.3 3차 검증 안 된 가정
- "+8~12pp" planner 추정 — **한국어 도메인 정량 검증 X** → 정직 인정, S1 측정 후 확정 권고
- HF Inference API 가 reranker endpoint 지원 — sentence-similarity pipeline 으로 검증 (cross-encoder 도 호출 가능)
- latency +200~500ms 사용자 수용 여부 미검증 (smoke 후 결정)

### 1.4 사용자 결정
> "S2 reranker 먼저 가자. (a) HF API 로, 나머지 다 OK"

→ 즉시 진입.

---

## 2. 구현 (commits 진행)

### 2.1 어댑터 신규 — `api/app/adapters/impl/bge_reranker_hf.py`

- **Endpoint**: `https://router.huggingface.co/hf-inference/models/BAAI/bge-reranker-v2-m3/pipeline/sentence-similarity`
- **Body**: `{"inputs": {"source_sentence": <q>, "sentences": [...]}}`
- **Response**: `[float, ...]` — query 와 각 sentence 의 cross-encoder relevance score (sigmoid 미적용 raw logit, ordering 만 유효)
- **패턴 재사용**: BGE-M3 (`bgem3_hf_embedding.py`) 의 httpx + retry + transient 분류 + 싱글톤 (lru_cache) 동일
- **LRU cache**: key=(query, chunk_id), value=score, maxsize=4096 — 같은 query 재호출 시 HF 호출 0
- **truncate**: BGE-reranker max_length 512 토큰 → 보수적 1200자 cap
- **`is_transient_reranker_error()`**: 4xx 영구 (auth/endpoint/bad request) vs 5xx/네트워크 transient 분류

### 2.2 search.py 통합

새 흐름:
1. embed_query (HF BGE-M3)
2. RPC `search_hybrid_rrf` → rpc_rows (top 50)
3. **NEW: chunks 본문 fetch (top-K) — cover guard meta + reranker 입력 + 응답 조립 한 번에** (기존 분리 fetch 통합)
4. **NEW: reranker (opt-in) — HF 1회 호출 → rpc_rows.rrf_score in-place 대체**
5. dense_hits/sparse_hits 계산 → `query_parsed` (with `reranker_used`, `reranker_fallback_reason`)
6. doc_id 별 그룹화 (cover guard 곱셈은 reranker 비활성 시에만)
7. 정렬 / 페이지네이션 / 응답 조립 (chunks_by_id 재사용 — 추가 fetch 0)

### 2.3 응답 schema 확장 (backward compatible)

```python
class QueryParsedInfo(BaseModel):
    has_dense: bool
    has_sparse: bool
    dense_hits: int
    sparse_hits: int
    fused: int
    fallback_reason: str | None = None
    reranker_used: bool = False              # NEW (default False)
    reranker_fallback_reason: str | None = None  # NEW (None / transient / permanent)
```

### 2.4 cover guard 정책

- reranker **off** (default): 기존 cover guard 곱셈 그대로 (W25 D4 Phase 2)
- reranker **on**: 곱셈 skip — cross-encoder 가 짧은 표지 청크 의미 매칭 약함을 직접 인식 + reranker score 가 음수 logit 일 수 있어 곱셈 시 부호 뒤집힘 회피

---

## 3. 단위 테스트 (`tests/test_reranker.py`)

15건 신규 추가:

| 분류 | 테스트 | 검증 |
|---|---|---|
| **transient 분류** | 4xx is not transient (400/401/403/404) | 영구 실패 즉시 노출 |
| | 429 + 5xx is transient (429/500/502/503/504) | retry/fallback 허용 |
| | network errors are transient | ConnectError/ReadTimeout/RemoteProtocolError |
| | RuntimeError is not transient | 응답 파싱 오류 즉시 노출 |
| **싱글톤** | get_reranker_provider returns same instance | httpx.Client 누수 회피 |
| **rerank()** | body uses sentence-similarity schema | request body 형식 검증 |
| | empty candidates → empty (no API call) | edge case |
| | cache hit skips HF call | LRU 동작 |
| | partial cache hit — miss only HF | cache 부분 hit |
| | response length mismatch raises | schema 검증 |
| | long passage truncation | 1200자 cap |
| **search 통합** | default off — no reranker call | opt-in default off |
| | enabled — score reorders | reranker 가 RRF 순서를 뒤집음 |
| | transient failure falls back to RRF | reranker 실패해도 검색 정상 |
| | permanent failure falls back to RRF | 401 등 영구 실패도 검색 정상 |

**전체 회귀**: 362 tests OK (이전 347 → +15건, 0 fail).

---

## 4. 활성화 절차 (smoke 단계)

### 4.1 환경 변수 추가

```bash
# .env 또는 셸
export JETRAG_RERANKER_ENABLED=true
```

### 4.2 백엔드 재시작

```bash
cd api
JETRAG_RERANKER_ENABLED=true uv run uvicorn app.main:app --reload
```

### 4.3 web UI 정성 체감 검증

본 PDF (b218e8a1) 또는 다른 doc 에 대해 사용자 query 검색:
- top-1 / top-3 결과의 적합도 (사용자 인지)
- 검색 적합도 카드 (`/search/eval-precision`) 의 Context Precision (BGE-M3 cosine 휴리스틱) 값 비교
  - reranker off vs on 의 측정값 차이로 정량 비교 가능 (한 방향 baseline)

### 4.4 fallback 검증

- 일시적으로 잘못된 토큰 (`HF_API_TOKEN=invalid`) → 401 → `query_parsed.reranker_fallback_reason="permanent"`, RRF 결과 정상 노출
- 네트워크 차단 → transient → `reason="transient"`, RRF 결과 정상 노출

---

## 5. 비판적 한계 (정직)

### 5.1 정량 검증 X
- "+8~12pp" 는 planner 추정. **한국어 도메인 정량 검증 안 됨**
- S1 골든셋 (N=30) 없이는 Recall@10/MRR/nDCG 비교 불가 → 정성 체감만
- `검색 적합도 카드` (BGE-M3 cosine + DCG) 는 retrieval 자체의 cosine 측정이라 reranker 효과를 일부만 반영

### 5.2 latency 미검증
- HF Inference API reranker 응답 latency 미측정 (smoke 시 측정 필요)
- BGE-M3 embedding (~300~800ms variable) + reranker (~300~500ms 추정) → 직렬 호출 시 누적
- LRU cache hit 시 HF 호출 0 (페이지네이션·카드 mount 등)

### 5.3 cache 효과
- cache 정책: (query, chunk_id) → score
- 사용자가 같은 query 반복 (재검색 / 페이지 이동) 시에만 hit
- 운영 일반 패턴은 unique query → cache hit ratio 낮을 수 있음
- 4096 entries 메모리 ~수백 KB, 비용 미미

### 5.4 cover guard skip
- reranker on 시 cover guard 곱셈 skip → 짧은 표지 청크 우세 회피를 cross-encoder 에 위임
- cross-encoder 가 모든 case 정확히 처리할 보장은 없음 (W25 D4 Phase 2 의 `SONATA` 표지 청크 시나리오 회귀 검증 필요)
- smoke 때 본 시나리오 ("소나타에서 제공하는 시트 종류") reranker on/off 비교 필수

### 5.5 backward compatibility
- `QueryParsedInfo.reranker_used` / `reranker_fallback_reason` 는 default 값 → 기존 클라이언트 영향 0
- 프론트는 본 필드 무시 (지금은). 추후 web UI 진단 카드에 표시 가능

---

## 6. 다음 후보

1. **smoke** — 사용자 환경에서 `JETRAG_RERANKER_ENABLED=true` 활성 후 정성 체감
2. **W25 D4 Phase 2 회귀 검증** — `SONATA` 표지 시나리오 reranker on/off 비교
3. **S1 측정 인프라** — 골든셋 N=30 (자동/수동 하이브리드) → 정량 baseline
4. **web UI 진단** — `query_parsed.reranker_used` 검색 결과 카드에 작은 배지 표시
5. **답변 시점 reranker** — `/answer`/`/ask` 도 search 의 결과 그대로 reranker 적용 (현재도 자연스럽게 됨, 별 변경 불필요)
6. **HF API rate limit 방어** — reranker rate limit 발생 시 로컬 cache 우선 + circuit breaker

---

## 7. 환경 변수 정리

| 변수 | default | 설명 |
|---|---|---|
| `JETRAG_RERANKER_ENABLED` | `false` | reranker opt-in. `true` 시 활성. |
| `HF_API_TOKEN` | (필수) | BGE-M3 + reranker 공통 |

---

## 8. 한 문장 요약

W25 D14+1 S2 — BGE-reranker-v2-m3 (HF Inference API, sentence-similarity pipeline) cross-encoder rerank ship.
RRF top-50 → reranker → 재정렬, opt-in ENV default off, fallback 포함, 362 tests OK (+15건).
정량 검증은 S1 골든셋 후속 — 지금은 정성 체감 단계.
