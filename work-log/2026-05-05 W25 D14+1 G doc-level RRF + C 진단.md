# 2026-05-05 W25 D14+1 — G (doc-level RRF) ship + C (HF endpoint 진단)

> 검색 성능 향상 plan §S4 (doc-level embedding) ship + S2 reranker 회귀 원인 진단.
> 사용자 결정: C (진단 30분) + G (doc-level RRF, 즉시 측정) 병행.

---

## 0. 한 줄 요약

C 진단 — **HF sentence-similarity pipeline 이 BGE-reranker cross-encoder 미작동 확정** (curl 직접 호출로 query-independent ranking 증거).
G ship — `documents.doc_embedding` (1024 dim, 이미 적재) cosine sim 의 doc-level RRF 가산 추가.
단위 테스트 10건 (388 → 398 OK), opt-in default false (S2 reranker 회귀 학습).
**multi-doc 효과 측정은 sonata 단일 골든셋의 천장 효과로 미확정 — 후속 골든셋 필요**.

---

## 1. C — HF endpoint 진단 결과

### 1.1 직접 curl 호출 (3 query × 3 sentences)

```python
url = "https://router.huggingface.co/hf-inference/models/BAAI/bge-reranker-v2-m3/pipeline/sentence-similarity"
sentences = [
    "소나타 시트 종류는 인조가죽, 천연가죽, 스웨이드 + 나파가죽, 나파가죽 4종이 있다",  # A: 시트
    "소나타 안전 기능은 현대 스마트센스 (FCA, BCA, NSCC, LFA, RCCA)",  # B: 안전
    "오늘 날씨가 매우 좋습니다",  # C: 날씨
]
```

| query | A 시트 | B 안전 | C 날씨 | 정답 |
|---|---:|---:|---:|---|
| "소나타 시트 종류 뭐가 있어?" | 0.7304 | **0.9758** ❌ | 0.6468 | A 가 1위여야 |
| "소나타 안전 기능 뭐 있어?" | 0.6890 | **0.9614** ✅ | 0.6037 | B 가 1위 (정답) |
| "날씨 어때" | **0.9795** ❌ | 0.8180 | 0.9863 | C 가 1위여야 (top-1 X) |

### 1.2 결론

- **score 가 query 마다 변함** → query-dependent 동작 (일반 sentence embedding 효과)
- 그러나 **ranking 정확성 낮음** — query 와 무관하게 특정 sentence (시트, 안전) 가 항상 높은 score
- 가장 결정적: "시트 종류" query 에 안전 chunk 가 더 높은 score (0.97 > 0.73)
- **HF sentence-similarity pipeline 이 BGE-reranker-v2-m3 cross-encoder 모드로 동작 안 함** — 일반 sentence-transformers 의 embedding cosine 유사하게 동작 (cross-encoder 는 query+passage concat 후 score, sentence-similarity 는 두 sentence embedding 의 cosine)

### 1.3 다음 액션 (S2 reranker 후속 sprint)

| 후보 | 내용 | 작업량 |
|---|---|---|
| A1 | HF API `text-classification` pipeline 시도 — `inputs=[[query, passage]]` body 형식 검증 | 中 (~1시간) |
| A2 | 구 endpoint `https://api-inference.huggingface.co/models/BAAI/bge-reranker-v2-m3` body 변형 시도 | 中 (~30분) |
| A3 | Cohere Rerank API ($1/1k) — 검증된 한국어 multilingual-v3 | 中 (사용자 비용 결정 필요) |
| A4 | reranker 폐기 (`JETRAG_RERANKER_ENABLED` default false 유지) + 다른 path 우선 | 下 |

권고: A2 (~30분) → 실패 시 A1 → 실패 시 A4. 본 sprint 에선 reranker 코드 보존 (default off 유지).

---

## 2. G — doc-level embedding RRF 가산 ship

### 2.1 현황 파악

`api/migrations/001_init.sql`:
```sql
CREATE TABLE documents (
    ...
    summary        TEXT,
    implications   TEXT,
    doc_embedding  vector(1024),  -- 이미 존재
    ...
);
CREATE INDEX idx_documents_embed ON documents USING ivfflat
    (doc_embedding vector_cosine_ops) WITH (lists = 100);
```

`api/app/ingest/stages/doc_embed.py` 가 인제스트 시 자동 적재 — `summary + implications` 또는 `raw_text[:3000]` 임베딩.

→ 데이터 이미 있음, search 단계에서 **사용 안 되고 있음** (chunks dense + sparse RRF 만 사용).

### 2.2 구현

`api/app/routers/search.py`:

1. docs_meta fetch select 에 `doc_embedding` 추가 (추가 query 0)
2. opt-in ENV `JETRAG_DOC_EMBEDDING_RRF` (default `false`)
3. 활성 시: candidate docs 의 doc_embedding 과 query_dense cosine sim → 내림차순 rank → `1/(_RRF_K + rank)` 를 doc_score 에 가산
4. doc_embedding NULL 인 doc 은 graceful skip
5. dense_vec None (sparse-only fallback) 시 가산 skip
6. `_cosine()` helper — numpy 의존성 0
7. `QueryParsedInfo.doc_embedding_rrf_used` / `doc_embedding_hits` 필드 (backward compatible)

### 2.3 default false 결정 근거

S2 reranker 의 학습:
- W25 D14+1 S2 ship 시 default off, smoke 정성 후 사용자 활성 결정
- 본 sprint (E) 측정 인프라가 reranker 회귀 발견
- → 정량 baseline 없이 default on 변경 위험

본 sprint 의 G 도 동일 정책: opt-in default off, 정량 검증 후 default on 결정.

### 2.4 단위 테스트 (10건)

- `_cosine` helper — identical / orthogonal / zero / dim mismatch
- search 통합 — default off / ENV on / NULL skip / cosine 변경 / chunks 비슷할 때 flip / string 형식 파싱

전체 회귀: **398 OK (이전 388 → +10건, 0 fail)**.

---

## 3. 정량 측정 결과

### 3.1 doc-scope (sonata 10건)

| 메트릭 | OFF | ON | Δ |
|---|---:|---:|---:|
| Recall@10 | 0.9000 | 0.9000 | 0 |
| MRR | 0.7167 | 0.7167 | 0 |
| nDCG@10 | 0.7458 | 0.7458 | 0 |

**예상대로 영향 0** — doc-scope (`?doc_id=...`) 검색은 단일 doc 내 chunks 만 reranking → doc-level 가산 의미 X.

### 3.2 multi-doc (doc_id 미지정, 10건)

| 메트릭 | OFF | ON | Δ |
|---|---:|---:|---:|
| doc-level top-1 hit | **1.0000** | **1.0000** | 0 |
| doc-level top-3 hit | 1.0000 | 1.0000 | 0 |
| doc-level MRR | 1.0000 | 1.0000 | 0 |

**천장 효과 (ceiling)** — sonata catalog 가 sonata 관련 query 에 이미 100% top-1 매칭. doc-level RRF 가산 효과 측정 불가.

→ 다양한 docs 의 골든셋 필요 (b218e8a1 데이터센터, 보건의료 빅데이터, 승인글 템플릿 등).
→ 또는 sonata 와 비슷한 매칭 강도의 다른 doc 와의 경쟁 시나리오.

---

## 4. 비판적 한계 (정직)

### 4.1 측정 한계

- sonata 골든셋 단일 doc → doc-level RRF 가산 효과 미확정
- 천장 효과 (top-1 hit 100%) 로 비교 불가
- **다양한 docs 골든셋 확장이 본 효과 검증의 전제** — 후속 sprint 필요

### 4.2 default off 의 의미

- ship 됐지만 운영 활성화 X (사용자가 명시적 ENV on 필요)
- 효과 검증 안 된 상태 → S2 reranker 와 동일 정책
- 정량 baseline 후 default on 결정

### 4.3 가산 자체의 한계

- 1/(60+rank) 가산이 chunks RRF (큰 값) 에 비해 작음 (~0.0164 / 0.0667)
- chunks RRF 가 큰 격차일 땐 가산 영향 미미
- chunks RRF 가 비슷할 때만 ranking 변경 — 효과 좁은 영역
- 가중치 (k_rrf=60) 튜닝 필요할 수 있음

### 4.4 doc_embedding 자체의 품질

- summary + implications 임베딩 — 인제스트 시점의 LLM 요약 품질 의존
- summary NULL 인 doc 은 raw_text[:3000] fallback — 첫 3000자만 → bias 있음
- 모든 doc 가 좋은 summary 보유한다는 가정

---

## 5. 다음 후보

### 5.1 P0 (즉시)

| 후보 | 내용 | 작업량 |
|---|---|---|
| **A2** (S2 후속) | HF API endpoint pipeline 변경 (`text-classification` 또는 raw POST) — reranker 회복 시도 | ~30분 |
| **NEW** | 다양한 docs 골든셋 확장 (b218e8a1 / 의료 / 등) — doc-level RRF 효과 측정 가능 | 中 (1~2일, 사용자 협조) |

### 5.2 P1 (다음 sprint)

| 후보 | 내용 | 효과 |
|---|---|---|
| **F** (S3) | HyDE / multi-query — Gemini 가 query → hypothetical doc 생성 → 임베딩 | +5~8pp |
| **H** (S5) | PGroonga 정밀화 (OR → AND 핵심어 혼합) | +2~5pp |

### 5.3 사용자 결정 필요

- A2 시도 (reranker 회복) vs NEW (골든셋 확장, doc-level RRF 효과 측정)
- 양쪽 모두 가치 — A2 가 더 빠름 (30분), NEW 가 본질적 (정량 baseline 확장)

---

## 6. 환경 변수 정리

| 변수 | default | 설명 |
|---|---|---|
| `JETRAG_RERANKER_ENABLED` | `false` | S2 BGE-reranker (HF sentence-similarity pipeline) — 정량 회귀 확인됨, off 권고 |
| `JETRAG_DOC_EMBEDDING_RRF` | `false` | G doc-level embedding 가산 — 효과 미확정, off |
| `HF_API_TOKEN` | (필수) | BGE-M3 + reranker 공통 |

---

## 7. 한 문장 요약

W25 D14+1 G — doc-level embedding RRF 가산 ship (default off, 단위 테스트 +10건, 398 OK).
C 진단 — HF sentence-similarity pipeline 이 cross-encoder 미작동 확정 (S2 reranker 회귀 원인).
multi-doc 효과는 sonata 천장 효과로 미확정 → 다양한 docs 골든셋 확장이 다음 sprint 의 전제.
